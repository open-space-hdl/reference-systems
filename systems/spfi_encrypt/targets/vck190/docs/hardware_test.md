# spfi_encrypt on the VCK190: Hardware Test Procedure

## 1. Purpose

This procedure tests the system spfi_encrypt on the AMD VCK190 evaluation board against a STAR-Dundee STAR-Ultra
PCIe as the far end of the SpaceFibre link. The far end reaches every register of the system with RMAP commands on
virtual channel 0, loads the keys of the four encryption channels, sends Space Packets on virtual channel 1 and
checks the encrypted chunks that come back with AES-GCM. The script
[`systems/spfi_encrypt/sw/hardware_test.py`](../../../sw/hardware_test.py) runs all test cases.

## 2. Material

| Item | Use |
| --- | --- |
| VCK190 evaluation board (production silicon), power supply, USB cable to the JTAG and UART port | Device under test |
| STAR-Ultra PCIe in a test computer, STAR-System installed on that computer with its Python package | Far end, test computer |
| Straight QSFP cable (four lanes) | QSFP1 of the VCK190 to port 1 of the STAR-Ultra |
| AMD Vivado 2025.2 with device support and a synthesis licence for the XCVC1902 | Build and programming |
| Python 3.10 or later on the test computer with `pycryptodome` | Test script |

## 3. Build

Build the device image as described in the [target README](../README.md#build) and record the timing, resource and
methodology results listed there.

## 4. Board set-up

1. Boot mode switch SW1 to JTAG (VCK190 user guide UG1366).
2. Reference clock: program the clock generator 8A34001 (U219) so that its output Q1, which drives MGTREFCLK1 of GTY
   quad 200, runs at 156.25 MHz, with the clock tool of the board's system controller (UG1366).
3. Connect QSFP1 of the VCK190 to port 1 of the STAR-Ultra. Lane i of the system must reach lane i of the far end;
   the system detects inverted receive polarity per lane itself.
4. Program the device image (Vivado Hardware Manager). LED 0 blinks (about 0.7 s on, 0.7 s off) and LED 1 is on.

## 5. Far-end configuration

| Parameter | Value |
| --- | --- |
| Port | Port 1 of the STAR-Ultra, four lanes |
| Line rate | 6.25 Gbit/s per lane, 8B/10B |
| Data scrambling | On (reset value of the system) |
| Link start | The STAR-Ultra starts the link. After a reset the SpaceFibre port of the system only waits for the far end (AutoStart), and its registers are reachable only over the link |
| Channel 0 | Virtual channel 0 of port 1 through the routing switch of the STAR-Ultra: path address 3 leads to port 1, path address 1 leads back to channel 0 |
| Channel 1 | Virtual channel 1 of port 1 |

The script sends RMAP commands on channel 0 with the path address 3 in front of the packet and the Reply Address 1;
the system replies with that address and the routing switch delivers the reply on channel 0. Space Packets go on
channel 1 directly, without a path address.

## 6. System configuration

The script uses the reset values of the system and writes only what the tests need. It takes the register windows,
the encryption channels with their output APIDs and key slots, the chunk size and the buffer sizes from the Composer
file of the system, and the register maps from the headers of the cores:

| Item | Value |
| --- | --- |
| RMAP target `rmap0` | Logical address 0xFE, key 0x00, the whole register map accessible (reset values) |
| Initiator logical address | 0xFE |
| Register windows | `spp0` 0x0000_0000, `ocr0` 0x0001_0000, `edac0` 0x0002_0000, `ocr1` to `ocr3` 0x0003_0000 to 0x0005_0000, `rmap0` 0x0006_0000, `ofb0` 0x0007_0000 |
| Input APIDs | 0x010 + i for encryption channel i: the receive APID table of `spp0` routes APID 0x010 + i to receive port i; every other APID keeps the reset path to receive port 0 |
| Keys | One 256-bit key per AES-GCM core in slot 0, the slot its framer uses, with generated IVs: fixed field and first invocation counter per channel; keys and IV values are derived from the seed of the script |
| Test slot | Slot 1 of `ocr0`, which its framer does not use, holds a further key and IV values for the commands of several words |
| Output | Space Packets with APID i (encryption channel i) to destination 0 (Target Logical Address 0xFE), CPTP on virtual channel 1 |

## 7. Tests

Run the script on the test computer from the root of the repository (the register maps come from the headers of the
cores in `cores/`):

```shell
python systems/spfi_encrypt/sw/hardware_test.py --device <name of the STAR-Ultra in STAR-System> --seed 1
```

It prints one line per test case and stops at the first failure, since the later test cases build on the earlier
ones. `--packets` sets the number of Space Packets of TC-RS-HW-06 (default 40), `--seed` the keys and the packets.

The key registers are write-only. TC-RS-HW-04 and TC-RS-HW-05 check the IV values through the slot query;
TC-RS-HW-06 checks the keys, since every chunk must authenticate and decrypt with the key of its channel.

`<PREFIX>_REGMAP_ID` is the identification of the register map in the header `cores/<core>/sw/<prefix>_regs.h` of
the pinned core; the script reads it from there.

| Test | Steps | Pass criterion |
| --- | --- | --- |
| TC-RS-HW-01 Link | Read `ID`, `DL_STATUS` and `LANE_STATUS` of `ofb0` | `ID` = `OFB_REGMAP_ID`, link initialised (`DL_STATUS.LINK_STATE` = 3), all lanes of `ofb0` (`NumLanes_g` in the Composer file) active (`LANE_STATUS.STATE` = 7) |
| TC-RS-HW-02 Identification | Read the identification register of every register window | `ID` of `spp0` = `SPP_REGMAP_ID`, of `ocr0` to `ocr3` = `OCR_REGMAP_ID`, of `rmap0` = `OMAP_REGMAP_ID`, of `ofb0` = `OFB_REGMAP_ID`; `INFO` of `edac0` 0x00001008 (8 channels, 16-bit counters) |
| TC-RS-HW-03 RMAP | Verified writes of `IRQ_ENA` of `edac0` and read back; a read with a wrong key | Every value reads back; the wrong key gives the status "invalid key" (3) |
| TC-RS-HW-04 Commands of several words | Read `ID`, `GENERICS`, `GENERICS2` and `RELEASE_BYTES` of `ocr0` with one command of 16 bytes and with single reads; load the test slot with the key in one write of 32 bytes and `IV_FIXED`, `IV_COUNT_LO` and `IV_COUNT_HI` in one write of 12 bytes, then `KEY_LOAD`; select the slot in `SLOT_QUERY` and read `SLOT_STATUS` to `SLOT_COUNT_HI` with one command of 16 bytes and with single reads | Both reads of 16 bytes give the words of the single reads; the slot is valid with generated IVs (`SLOT_STATUS` 0x3) and holds the written fixed field and invocation counter |
| TC-RS-HW-05 Configuration | Receive APID table of `spp0` (APID 0x010 + i to port i); slot 0 of every AES-GCM core loaded as in TC-RS-HW-04 with the key and IV values of its channel and queried; counters cleared | Table entries read back, no table overrun; `STATUS.BUSY` of every core returns to 0; every slot is valid with generated IVs and holds the fixed field and invocation counter of its channel |
| TC-RS-HW-06 Encryption | Space Packets of 1 byte to 8000 bytes (also one byte less, equal to and one byte more than a chunk of 4096 bytes) on the four input APIDs | Every input packet comes back as ceil(n / 4096) Space Packets with the APID of its channel and consecutive sequence counts; every chunk authenticates and decrypts with the key of its channel, carries the fixed field of its channel, the first invocation counter is the loaded one, the invocation counters have no gap, and the decrypted chunks give the input packets |
| TC-RS-HW-07 Counters | Read the packet counters of `spp0`, the encryption and error counters of the AES-GCM cores and the counters of `edac0` | Received and sent packets and encryptions match TC-RS-HW-06; every error counter and every ECC counter is 0 |

## 8. Records

Record the commit of the repository, the build results of section 3, the seed and the output of the script for
every run, with the date. A failing test case is recorded with its output and the register values it reports.
