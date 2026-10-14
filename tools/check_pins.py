"""Checks that the submodules in cores/ match the pins of the Composer files.

For every system file systems/<system>/<system>.composer.json:

- every repository of the section `sources` is a submodule cores/<repository name> with the same URL, and the
  submodule is pinned to the commit of the file;
- exactly one source is Open Logic FT with a tag, and the tag points to the pinned commit: one Open Logic library
  serves the whole system;
- every core was verified with an Open Logic FT tag (`verifiedWith`) that is not newer than this tag, and that tag is
  the commit of the core's own open-logic submodule.

Across the files: all systems pin the same commit of a repository (they share cores/), and every submodule in cores/
is used by a system. Run it from any directory; it exits with 1 on a mismatch.
"""

import configparser
import subprocess
import sys
from pathlib import Path

from composer_system import CORES, ROOT, load, normalise_url, sources, system_files, version_key


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def submodules() -> dict[str, str]:
    """Submodule path (relative, POSIX) -> URL from .gitmodules."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".gitmodules", encoding="utf-8")
    return {parser[s]["path"]: parser[s]["url"] for s in parser.sections()}


def gitlink(path: str, cwd: Path = ROOT) -> str | None:
    """Commit a submodule is pinned to in the index of the repository at cwd (staged pins count)."""
    out = git("ls-files", "--stage", "--", path, cwd=cwd).split()
    if len(out) >= 2 and out[0] == "160000":
        return out[1]
    return None


def tag_commits(url: str) -> dict[str, str]:
    """Tag -> commit of a remote repository (annotated tags peeled)."""
    tags: dict[str, str] = {}
    for line in git("ls-remote", "--tags", url).splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            tags[name.removesuffix("^{}")] = sha
        else:
            tags.setdefault(name, sha)
    return tags


def main() -> int:
    errors: list[str] = []
    modules = submodules()
    pinned: dict[str, tuple[str, str]] = {}  # submodule path -> (commit, system)
    files = system_files()
    if not files:
        errors.append("no Composer file in systems/")

    for file in files:
        rel_file = file.relative_to(ROOT).as_posix()
        data = load(file)
        srcs = sources(data)
        for src in srcs:
            path = src.path.relative_to(ROOT).as_posix()
            if path not in modules:
                errors.append(f"{rel_file}: {src.id} needs the submodule {path} ({src.url})")
                continue
            if normalise_url(modules[path]) != normalise_url(src.url):
                errors.append(f"{rel_file}: {path} has the URL {modules[path]}, the file names {src.url}")
            commit = gitlink(path)
            if commit != src.commit:
                errors.append(f"{rel_file}: {path} is pinned to {commit}, the file pins {src.commit}")
            if path in pinned and pinned[path][0] != src.commit:
                errors.append(f"{rel_file}: {src.id} at {src.commit}, but {pinned[path][1]} pins {pinned[path][0]}")
            pinned.setdefault(path, (src.commit, rel_file))

        olo = [s for s in srcs if s.tag]
        if len(olo) != 1:
            errors.append(f"{rel_file}: exactly one source (Open Logic FT) must have a tag, found {len(olo)}")
            continue
        olo = olo[0]
        tags = tag_commits(olo.url)
        if tags.get(olo.tag) != olo.commit:
            errors.append(f"{rel_file}: tag {olo.tag} of {olo.url} is {tags.get(olo.tag)}, the file pins {olo.commit}")

        for src in srcs:
            if src is olo or src.tag:
                continue
            if not src.verified_with:
                errors.append(f"{rel_file}: {src.id} has no verifiedWith")
                continue
            if src.verified_with not in tags:
                errors.append(f"{rel_file}: {src.id} verifiedWith {src.verified_with} is not a tag of {olo.url}")
                continue
            if version_key(src.verified_with) > version_key(olo.tag):
                errors.append(f"{rel_file}: {src.id} was verified with {src.verified_with}, newer than {olo.tag}")
            if not (src.path / ".git").exists():
                errors.append(f"{rel_file}: {src.path.relative_to(ROOT).as_posix()} is not checked out "
                              "(git submodule update --init)")
                continue
            own = gitlink("open-logic", cwd=src.path)
            if own != tags[src.verified_with]:
                errors.append(f"{rel_file}: {src.id} pins its own open-logic to {own}, "
                              f"not to {src.verified_with} ({tags[src.verified_with]})")

    for path in modules:
        if path.startswith("cores/") and path not in pinned:
            errors.append(f"submodule {path} is not used by any system")

    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"Pins OK: {len(files)} system(s), {len(pinned)} submodule(s) in {CORES.relative_to(ROOT).as_posix()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
