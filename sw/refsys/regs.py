"""Register maps of the cores, read from their generated C headers (cores/<core>/sw/<prefix>_regs.h).

A header defines the registers as constants (`#define OCR_KEY_LOAD 0x06Cu`), register arrays as macros of one
index (`#define OCR_KEY(n) (0x040u + (n) * 0x004u)`) and the fields as `<REG>_<FIELD>_SHIFT` and `_MASK`.
"""

import re
from pathlib import Path

_CONST = re.compile(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)u?\s*$")
_ARRAY = re.compile(r"#define\s+(\w+)\((\w+)\)\s+\(\s*(0x[0-9A-Fa-f]+|\d+)u?\s*\+\s*\(\2\)\s*\*\s*(0x[0-9A-Fa-f]+|\d+)u?\s*\)\s*$")


class RegisterMap:
    """Constants and register arrays of one header, accessed without the prefix: regs.KEY_LOAD, regs.KEY(3)."""

    def __init__(self, header: Path, prefix: str):
        self.header = Path(header)
        self.prefix = prefix.upper().rstrip("_") + "_"
        self._const: dict[str, int] = {}
        self._array: dict[str, tuple[int, int]] = {}
        for line in self.header.read_text(encoding="utf-8").splitlines():
            if m := _ARRAY.match(line.strip()):
                self._array[self._name(m[1])] = (int(m[3], 0), int(m[4], 0))
            elif m := _CONST.match(line.strip()):
                self._const[self._name(m[1])] = int(m[2], 0)

    def _name(self, name: str) -> str:
        return name[len(self.prefix):] if name.startswith(self.prefix) else name

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._const:
            return self._const[name]
        if name in self._array:
            base, stride = self._array[name]
            return lambda index: base + index * stride
        raise AttributeError(f"{self.header.name} defines no {self.prefix}{name}")

    def constants(self) -> dict[str, int]:
        """All constants of the header, by their names without the prefix."""
        return dict(self._const)

    def field(self, register: str, field: str, value: int) -> int:
        """A field value at its position in the register."""
        mask = self._const[f"{register}_{field}_MASK"]
        if value & ~mask:
            raise ValueError(f"{value} does not fit into {register}.{field}")
        return value << self._const[f"{register}_{field}_SHIFT"]

    def get(self, register: str, field: str, word: int) -> int:
        """A field of a register value."""
        return (word >> self._const[f"{register}_{field}_SHIFT"]) & self._const[f"{register}_{field}_MASK"]
