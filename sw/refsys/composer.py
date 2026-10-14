"""Facts of a system from its Composer file (systems/<system>/<system>.composer.json).

A system test takes the register windows, the connections and the constants of the system from the file that
defines it, so that it follows the system when the system changes in the Composer.
"""

import json
import re
from pathlib import Path

_PORT = re.compile(r"^(\w+)\.(\w+)(?:\[(\d+)\])?$")


class SystemFile:
    """Instances, connections and resolved netlist of one system."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.name = self.data["system"]["name"]
        self.connections = self.data["connections"]
        self._instances = {i["id"]: i for i in self.data["resolved"]["instances"]}

    def instances_of(self, block: str) -> list[str]:
        """Instances of a block of the catalog, for example "openspp/spp_core"."""
        return [i["id"] for i in self.data["instances"] if i["block"] == block]

    def instance_of_block(self, block: str) -> str:
        """The only instance of a block."""
        found = self.instances_of(block)
        if len(found) != 1:
            raise KeyError(f"{len(found)} instances of {block} in {self.name}")
        return found[0]

    def generic(self, instance: str, name: str):
        """Value of a generic of an instance as resolved (default values included)."""
        for g in self._instances[instance]["generics"]:
            if g["name"] == name:
                return g["value"]
        raise KeyError(f"{instance} has no generic {name}")

    def connection_from(self, source: str) -> dict:
        """The connection whose source is the interface "<instance>.<interface>[<index>]"."""
        found = [c for c in self.connections if c["from"] == source]
        if len(found) != 1:
            raise KeyError(f"{len(found)} connections from {source}")
        return found[0]

    def connection_to(self, sink: str) -> dict:
        found = [c for c in self.connections if c["to"] == sink]
        if len(found) != 1:
            raise KeyError(f"{len(found)} connections to {sink}")
        return found[0]

    def decoder_windows(self, decoder: str) -> dict[str, int]:
        """Base address of every register port behind an olo_axi_lite_decoder: port p owns the addresses
        p x 2**PortAddrWidth_g to (p + 1) x 2**PortAddrWidth_g - 1 (Open Logic documentation of the decoder)."""
        size = 1 << self.generic(decoder, "PortAddrWidth_g")
        windows = {}
        for c in self.connections:
            m = _PORT.match(c["from"])
            if m and m[1] == decoder and m[2] == "M_AxiLite":
                windows[c["to"].split(".")[0]] = int(m[3]) * size
        return windows

    def tie_off(self, signal: str, field: str):
        """Value of a field of a constant that the resolved netlist ties to a signal (for example the key slot of
        a framer, signal "frm0_Cfg_Slot_s", field "slot")."""
        for net in self.data["resolved"]["nets"]:
            if net["to"] == signal:
                for f in net.get("fields", []):
                    if f["name"] == field:
                        return f["value"]
        raise KeyError(f"no constant field {field} on {signal}")


def instance_of(port: str) -> str:
    """Instance of an interface reference "<instance>.<interface>[<index>]"."""
    return port.split(".")[0]
