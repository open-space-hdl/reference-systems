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

    def registers(self, window: int = 0x10000) -> dict[int, str]:
        """The name of every register of a window by its offset. A register is a word-aligned constant in the window
        that is no field (<FIELD>_MASK with <FIELD>_SHIFT) and no reset value (<REG>_RESET), the first of the header
        where two have the same value, or an element NAME(index) of a register array. Register arrays of the same
        stride whose first elements lie within one stride of each other interleave (the registers of one lane); their
        elements end at the next other register of the header, after at most 64 elements."""
        names: dict[int, str] = {}
        fields = {f"{n.removesuffix('_MASK')}{s}" for n in self._const if n.endswith("_MASK")
                  and f"{n.removesuffix('_MASK')}_SHIFT" in self._const for s in ("_MASK", "_SHIFT")}
        reset_values = {f"{name}_RESET" for name in [*self._const, *self._array]}
        for name, value in self._const.items():
            if (value < window and value % 4 == 0 and name not in fields | reset_values
                    and not name.endswith("REGMAP_ID")):
                names.setdefault(value, name)
        starts = set(names) | {base for base, _ in self._array.values()}
        for name, (base, stride) in self._array.items():
            if not stride:
                continue
            group = {b for b, s in self._array.values() if s == stride and abs(b - base) < stride}
            end = min([o for o in starts - group if o >= min(group) + stride] + [window])
            for index in range(64):
                if base + index * stride >= end:
                    break
                names.setdefault(base + index * stride, f"{name}({index})")
        return names

    def field(self, register: str, field: str, value: int) -> int:
        """A field value at its position in the register."""
        mask = self._const[f"{register}_{field}_MASK"]
        if value & ~mask:
            raise ValueError(f"{value} does not fit into {register}.{field}")
        return value << self._const[f"{register}_{field}_SHIFT"]

    def get(self, register: str, field: str, word: int) -> int:
        """A field of a register value."""
        return (word >> self._const[f"{register}_{field}_SHIFT"]) & self._const[f"{register}_{field}_MASK"]

    def fields(self, register: str) -> dict[str, tuple[int, int]]:
        """The fields of a register (an array register by its name without the index): {field: (shift, mask)} in the
        order of their bits. A field of a longer register whose name starts with this one is not one of them."""
        prefix = f"{register}_"
        # registers whose names start with this one (VC_CFG for VC): their fields are not fields of this register
        longer = [r for r in [*self._const, *self._array] if r.startswith(prefix)
                  and not r.endswith(("_MASK", "_SHIFT", "_RESET"))]
        out = {}
        for name, mask in self._const.items():
            if not (name.startswith(prefix) and name.endswith("_MASK")):
                continue
            field = name[len(prefix):-len("_MASK")]
            shift = self._const.get(f"{prefix}{field}_SHIFT")
            if shift is not None and not any(f"{prefix}{field}".startswith(f"{r}_") for r in longer):
                out[field] = (shift, mask)
        return dict(sorted(out.items(), key=lambda item: item[1][0]))

    def decode(self, register: str, word: int, nonzero: bool = False) -> str:
        """The fields of a register value as text, "FIELD value, ..." (values above 9 in hexadecimal); nonzero: only
        the fields that are not 0. Empty for a register without fields."""
        parts = []
        for field, (shift, mask) in self.fields(register).items():
            value = (word >> shift) & mask
            if value or not nonzero:
                parts.append(f"{field} {value if value < 10 else hex(value)}")
        return ", ".join(parts)

    def covering(self, register: str, mask: int) -> list[str]:
        """The fields of a register that the bits of mask touch, in the order of their bits."""
        return [f for f, (shift, m) in self.fields(register).items() if (m << shift) & mask]
