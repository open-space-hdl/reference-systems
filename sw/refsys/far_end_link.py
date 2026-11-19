"""The link control of the far end in a process of its own (StarSystemTransport.far_end_link_reset in transport.py):
the link reset of a SpaceFibre port of a STAR-Ultra PCIe Interface (transport.link_reset). The link control needs
channel 0 of the device and keeps it until its process ends, so it runs here and not in the process of the test,
which holds the channels of the device.

Usage: python -m refsys.far_end_link <device name> <SpaceFibre port> <hold s> <timeout s>

Prints one line of JSON, {"down": ..., "text": ...} or {"error": ...}, and ends with 0 or 1.
"""

import json
import sys

from refsys.transport import link_reset


def main(argv: list[str] | None = None) -> int:
    name, port, hold, timeout = sys.argv[1:] if argv is None else argv
    try:
        from STAR_system import STAR_enums as enums
        from STAR_system.STAR_system import STARSystem
        from STAR_Ultra.interface import StarUltraPCIeInterface

        devices = {d.getDeviceName(): d
                   for d in STARSystem().getDeviceListForType(enums.STAR_DEVICE_TYPE.STAR_DEVICE_STAR_ULTRA_PCIE)}
        if name not in devices:
            raise LookupError(f"device {name!r} not found; available: {', '.join(devices) or 'none'}")
        result = link_reset(StarUltraPCIeInterface(), devices[name].deviceID, int(port), float(hold), float(timeout))
    except Exception as err:  # noqa: BLE001 - the reason goes to the test
        print(json.dumps({"error": str(err) or type(err).__name__}))
        return 1
    print(json.dumps({"down": result.down, "text": result.text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
