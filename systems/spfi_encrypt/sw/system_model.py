# ---------------------------------------------------------------------------------------------------
# Copyright (c) 2026 by Julian Schneider
# Authors: Julian Schneider
# ---------------------------------------------------------------------------------------------------
"""Software model of spfi_encrypt as seen from the far end of its link, for the tests of the hardware test and the
stimuli of the system simulation.

The model of the link, the RMAP node, the Space Packet node, the framers, the AES-GCM cores and the ECC monitor is
that of sw/refsys/sysmodel.py, which lists the documents of the pinned cores that it follows. In spfi_encrypt every
encryption channel gives its framer the Packet Data Field of an input Space Packet unchanged.
"""

import hardware_test as ht
from refsys import sysmodel
from refsys.composer import SystemFile
from refsys.sysmodel import SPECIFIED

SYSTEM = ht.System()
SYSTEM_FILE = SystemFile(ht.SYSTEM_DIR / "spfi_encrypt.composer.json")

__all__ = ["SPECIFIED", "SYSTEM", "SYSTEM_FILE", "SystemModel"]


class SystemModel(sysmodel.SystemModel):
    """spfi_encrypt as seen from the far end of its link.

    first_word: whether a read and whether a write of several words use the address of their first word for every
    word, as with the defect of olo_axi_master_simple in Open Logic FT up to 4.7.0-ft.4.
    """

    def __init__(self, setup: "ht.Setup", corrupt: bool = False, first_word: tuple[bool, bool] = SPECIFIED):
        super().__init__(SYSTEM, SYSTEM_FILE, setup, corrupt, first_word)

    def process(self, channel, data, user_application, loss):
        return [data]
