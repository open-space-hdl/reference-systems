"""Host software of the reference systems: packets, registers and transports for system tests.

The modules build and check the packets that a test sends to a system and receives from it, independent of the
equipment that carries them:

- rmap: RMAP commands and replies (ECSS-E-ST-50-52C)
- spp: Space Packets (CCSDS 133.0-B-2) and CPTP packets (ECSS-E-ST-50-53C)
- chunks: the authenticated chunks of OpenCrypto (ocr_framer and ocr_core with dense output), AES-GCM
- regs: register maps from the C headers of the cores
- composer: register windows, connections and constants of a system from its Composer file
- transport: the interface to the link equipment
- ocp: OpenCompact images: model configuration from the generics, test images, samples as bytes, header check and
  the model of a compression channel
- systest: the parts of the hardware tests that all systems share (register access, key slots, the test cases of
  link, identification and RMAP, drain, run)
"""
