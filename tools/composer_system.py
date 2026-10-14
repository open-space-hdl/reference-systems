"""Reads the Composer files of the reference systems.

Every system is defined by systems/<system>/<system>.composer.json. Its section `sources` names the repositories
(cores and Open Logic FT) and the commits the system is built from; each repository is a submodule
cores/<repository name>. This module derives the submodule paths and the VHDL source files of every library from the
Composer files, for tools/check_pins.py and run.py.
"""

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORES = ROOT / "cores"
SYSTEMS = ROOT / "systems"

FORMAT = "open-space-composer-system"


@dataclass(frozen=True)
class Source:
    """One entry of the section `sources` of a Composer file."""

    id: str
    name: str
    url: str
    commit: str
    library: str
    file_list: str
    file_list_format: str
    tag: str | None
    verified_with: str | None

    @property
    def repo(self) -> str:
        """Repository name, the last part of the URL without `.git`."""
        name = self.url.rstrip("/").rsplit("/", 1)[-1]
        return name.removesuffix(".git")

    @property
    def path(self) -> Path:
        """Submodule directory of the repository."""
        return CORES / self.repo


def normalise_url(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").lower()


def system_files() -> list[Path]:
    """All Composer files of the repository, one per system directory."""
    return sorted(SYSTEMS.glob("*/*.composer.json"))


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{path}: not a Composer system file (format {data.get('format')!r})")
    expected = path.name.removesuffix(".composer.json")
    name = data.get("system", {}).get("name")
    if name != expected or path.parent.name != expected:
        raise ValueError(f"{path}: system name {name!r} must equal the file and directory name {expected!r}")
    return data


def sources(data: dict) -> list[Source]:
    result = []
    for entry in data["sources"]:
        result.append(Source(
            id=entry["id"],
            name=entry["name"],
            url=entry["url"],
            commit=entry["commit"],
            library=entry["library"],
            file_list=entry["fileList"],
            file_list_format=entry["fileListFormat"],
            tag=entry.get("tag"),
            verified_with=entry.get("verifiedWith"),
        ))
    return result


def _list_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            lines.append(entry)
    return lines


def library_files(data: dict, vendor: bool = False) -> dict[str, list[Path]]:
    """VHDL source files per library, as the Composer file describes them.

    A `files` list names the files in compile order. A `modules` list names module directories; their sources are
    `src/*.vhd`. Blocks of the system whose file lies outside the listed modules (vendor-specific adapters such as
    ofb_pa_gty) are added from the catalog entry `path`; with vendor=False the files of vendor-specific blocks are
    left out, since they need the vendor libraries (unisim).
    """
    blocks = data["catalogSubset"]["blocks"]
    result: dict[str, list[Path]] = {}
    for src in sources(data):
        files: list[Path] = []
        listed = _list_lines(src.path / src.file_list)
        if src.file_list_format == "files":
            files = [src.path / rel for rel in listed]
        elif src.file_list_format == "modules":
            for module in listed:
                files.extend(sorted((src.path / module / "src").glob("*.vhd")))
        else:
            raise ValueError(f"{src.id}: unknown fileListFormat {src.file_list_format!r}")
        for block in blocks:
            if block.get("source") != src.id or "path" not in block:
                continue
            file = src.path / block["path"]
            if file in files:
                continue
            if block.get("vendor") and not vendor:
                continue
            files.append(file)
        result.setdefault(src.library, [])
        result[src.library].extend(f for f in files if f not in result[src.library])
    return result


def version_key(tag: str) -> tuple[int, ...]:
    """Sort key of an Open Logic FT tag such as 4.7.0-ft.3."""
    base, _, ft = tag.partition("-ft.")
    return tuple(int(part) for part in base.split(".")) + (int(ft) if ft else 0,)
