"""Checks that the submodules in cores/ match the pins of the Composer files.

For every system file systems/<system>/<system>.composer.json:

- every repository of the section `sources` is a submodule cores/<repository name> with the same URL, and the
  submodule is pinned to the commit of the file;
- exactly one source is Open Logic FT with a tag, and the tag points to the pinned commit: one Open Logic library
  serves the whole system;
- every core was verified with an Open Logic FT tag (`verifiedWith`), and that tag is the commit of the core's own
  open-logic submodule. A tag newer than the tag of the system is accepted only when every Open Logic file that the
  core uses is the same in both tags: the files that the files of the core in the compile order of the system
  (hdl/<system>_compile_order.txt) name, closed over the references of Open Logic to itself. The check prints the
  core, both tags and the number of files compared, or names the files that differ.

Across the files: all systems pin the same commit of a repository (they share cores/), and every submodule in cores/
is used by a system. Run it from any directory; it exits with 1 on a mismatch.

The tags of Open Logic FT come from its published repository. With --offline they come from the checked-out
submodule cores/open-logic-ft instead, without network access: for a computer that received the repository as Git
bundles (tools/make_offline_bundle.py), whose bundle of Open Logic FT carries the tag of the system.
"""

import argparse
import configparser
import re
import subprocess
import sys
from pathlib import Path

from composer_system import CORES, ROOT, Source, load, normalise_url, sources, system_files, version_key

# Open Logic units that a file of a core names (olo.<unit>), that a file of Open Logic names of itself (work.<unit>,
# olo.<unit>), and that a file of Open Logic declares
CORE_REF = re.compile(r"\bolo\.(olo_\w+)", re.IGNORECASE)
OLO_REF = re.compile(r"\b(?:olo|work)\.(olo_\w+)", re.IGNORECASE)
OLO_UNIT = re.compile(r"^\s*(?:entity|package)\s+(olo_\w+)\s+is\b", re.IGNORECASE | re.MULTILINE)


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


def remote_tags(url: str) -> dict[str, str]:
    """Tag -> commit of a remote repository (annotated tags peeled)."""
    return parse_tags(git("ls-remote", "--tags", url))


def local_tags(repo: Path) -> dict[str, str]:
    """Tag -> commit of the tags of a local clone (annotated tags peeled), without network access."""
    result = subprocess.run(["git", "show-ref", "--tags", "-d"], cwd=repo, capture_output=True, text=True, check=False)
    return parse_tags(result.stdout)


def parse_tags(listing: str) -> dict[str, str]:
    """Tag -> commit from the lines `<commit> refs/tags/<name>` of ls-remote or show-ref -d; the peeled line
    (`^{}`) of an annotated tag gives the commit."""
    tags: dict[str, str] = {}
    for line in listing.splitlines():
        sha, ref = line.split(maxsplit=1)
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            tags[name.removesuffix("^{}")] = sha
        else:
            tags.setdefault(name, sha)
    return tags


def has_commit(repo: Path, commit: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, capture_output=True,
                          check=False).returncode == 0


def fetch_tag(repo: Path, url: str, tag: str) -> None:
    """Fetches a tag of the published repository into a clone that does not have its commit (a shallow submodule
    of the CI); a shallow clone stays shallow."""
    shallow = git("rev-parse", "--is-shallow-repository", cwd=repo).strip() == "true"
    git("fetch", "-q", *(["--depth", "1"] if shallow else []), url, f"refs/tags/{tag}:refs/tags/{tag}", cwd=repo)


def core_files(system_file: Path, src: Source) -> list[str]:
    """The files of a core in the compile order of the system, relative to the repository of the core."""
    order = system_file.parent / "hdl" / f"{system_file.parent.name}_compile_order.txt"
    files = []
    for line in order.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "file" and parts[1] == src.library and parts[2].startswith(f"{src.repo}/"):
            files.append(parts[2].removeprefix(f"{src.repo}/"))
    return files


def read_files(repo: Path, commit: str, paths: list[str]) -> dict[str, str]:
    """The text of files at a commit, read with one call of git cat-file."""
    request = "".join(f"{commit}:{p}\n" for p in paths).encode()
    out = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input=request, capture_output=True,
                         check=True).stdout
    texts, pos = {}, 0
    for p in paths:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode().split()
        if header[-1] == "missing":
            raise ValueError(f"{p} is missing at {commit}")
        size = int(header[2])
        texts[p] = out[end + 1:end + 1 + size].decode("utf-8", errors="replace")
        pos = end + 1 + size + 1
    return texts


def tree(repo: Path, commit: str, path: str) -> dict[str, str]:
    """File -> blob of every file below path at a commit."""
    return {line.split("\t", 1)[1]: line.split()[2]
            for line in git("ls-tree", "-r", commit, "--", path, cwd=repo).splitlines()}


def used_olo_files(core_texts: list[str], olo_repo: Path, commit: str) -> list[str]:
    """The files of Open Logic at commit that the texts of a core name, closed over the references of Open Logic to
    itself."""
    texts = read_files(olo_repo, commit, [p for p in tree(olo_repo, commit, "src") if p.endswith(".vhd")])
    index: dict[str, str] = {}
    for path, text in texts.items():
        for name in OLO_UNIT.findall(text):
            index.setdefault(name.lower(), path)
    todo = sorted({u.lower() for text in core_texts for u in CORE_REF.findall(text)})
    used: set[str] = set()
    while todo:
        unit = todo.pop()
        if unit in used:
            continue
        used.add(unit)
        if unit not in index:
            raise ValueError(f"the Open Logic unit {unit} has no file at {commit}")
        todo += [u.lower() for u in OLO_REF.findall(texts[index[unit]]) if u.lower() not in used]
    return sorted({index[u] for u in used})


def compare_used_olo_files(system_file: Path, src: Source, olo_repo: Path, system_commit: str,
                           verified_commit: str) -> tuple[list[str], list[str]]:
    """The Open Logic files that a core of a system uses (at the commit it was verified with), and those of them that
    are not the same at the commit of the system."""
    files = core_files(system_file, src)
    if not files:
        raise ValueError(f"the compile order of {system_file.parent.name} has no file of {src.id}")
    used = used_olo_files(list(read_files(src.path, src.commit, files).values()), olo_repo, verified_commit)
    old, new = tree(olo_repo, system_commit, "src"), tree(olo_repo, verified_commit, "src")
    return used, [p for p in used if old.get(p) != new.get(p)]


def newer_verification(system_file: Path, src: Source, olo: Source, tags: dict[str, str], where: str,
                       offline: bool) -> tuple[str | None, str | None]:
    """A core verified with a newer Open Logic FT tag than the system's: (error, None), or (None, the evidence) when
    every Open Logic file that the core uses is the same in both tags."""
    if not (olo.path / ".git").exists():
        return (f"{src.id}: {olo.path.relative_to(ROOT).as_posix()} is not checked out (git submodule update --init)",
                None)
    verified = tags[src.verified_with]
    if not has_commit(olo.path, verified):
        if offline:
            return (f"{src.id}: the commit of {src.verified_with} ({verified[:12]}) is not in {where}; the bundle of "
                    "Open Logic FT must carry the tag", None)
        fetch_tag(olo.path, olo.url, src.verified_with)
    try:
        used, differ = compare_used_olo_files(system_file, src, olo.path, olo.commit, verified)
    except (ValueError, subprocess.CalledProcessError) as err:
        return f"{src.id}: the Open Logic files it uses cannot be compared: {err}", None
    if differ:
        return (f"{src.id} was verified with {src.verified_with}, newer than {olo.tag}, and {len(differ)} of the "
                f"{len(used)} Open Logic files it uses differ: {', '.join(differ)}", None)
    return None, (f"{src.id}: verified with {src.verified_with}, system olo {olo.tag}, {len(used)} used Open Logic "
                  "files identical")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="read the tags of Open Logic FT from cores/open-logic-ft instead of its remote")
    args = parser.parse_args()
    errors: list[str] = []
    notes: list[str] = []
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
        if args.offline:
            where = olo.path.relative_to(ROOT).as_posix()
            if not (olo.path / ".git").exists():
                errors.append(f"{rel_file}: {where} is not checked out (git submodule update --init)")
                continue
            tags = local_tags(olo.path)
        else:
            where = olo.url
            try:
                tags = remote_tags(olo.url)
            except subprocess.CalledProcessError:
                errors.append(f"{rel_file}: the tags of {olo.url} cannot be read; on a computer without network "
                              "access use --offline")
                continue
        if tags.get(olo.tag) != olo.commit:
            errors.append(f"{rel_file}: tag {olo.tag} of {where} is {tags.get(olo.tag)}, the file pins {olo.commit}")

        for src in srcs:
            if src is olo or src.tag:
                continue
            if not src.verified_with:
                errors.append(f"{rel_file}: {src.id} has no verifiedWith")
                continue
            if src.verified_with not in tags:
                errors.append(f"{rel_file}: {src.id} verifiedWith {src.verified_with} is not a tag of {where}")
                continue
            if version_key(src.verified_with) > version_key(olo.tag):
                error, note = newer_verification(file, src, olo, tags, where, args.offline)
                if error:
                    errors.append(f"{rel_file}: {error}")
                else:
                    notes.append(f"{rel_file}: {note}")
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

    for note in notes:
        print(note)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"Pins OK: {len(files)} system(s), {len(pinned)} submodule(s) in {CORES.relative_to(ROOT).as_posix()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
