# spfi_encrypt

A SpaceFibre node that encrypts Space Packets. One SpaceFibre port with four lanes carries RMAP commands on virtual
channel 0 and Space Packets on virtual channel 1. Four encryption channels take the user data of received Space
Packets, encrypt it in chunks with AES-GCM and send every encrypted chunk back as a Space Packet with the APID of its
channel. An RMAP target gives access to the registers of all blocks over the same link.

The system is defined by [spfi_encrypt.composer.json](spfi_encrypt.composer.json); open it in the
[Open Space Composer](https://openspacehdl.org/composer/) to see or change it.

## Status

| Item | Status |
| --- | --- |
| Composer file | present, core versions checked by `tools/check_pins.py`; the checks of the Composer report no warning |
| Generated top level (`hdl/`) | generated with the command line tool of the Composer (`tools/composer_ref.txt`); the CI checks that it is the export of the Composer file |
| [System testbench](sim/README.md) (`sim/`) | all test cases of the hardware test pass against a far-end SpaceFibre node (profile `sim`), in xsim with the transceiver model and in GHDL with the behavioural model of the adapter |
| [AMD VCK190 target](targets/vck190/README.md) | board top, constraints and build script; not built yet |
| Hardware test ([procedure](targets/vck190/docs/hardware_test.md), [sw/hardware_test.py](sw/hardware_test.py)) | test cases against a STAR-Ultra PCIe as the far end, checked against a software model of the system; not run on hardware yet |

## Blocks

| Instance | Entity | Function |
| --- | --- | --- |
| `pa0` | `openfibre.ofb_pa_gty` | Physical adapter for one AMD Versal GTY quad, 4 lanes at 6.25 Gbit/s |
| `ofb0` | `openfibre.ofb_core` | SpaceFibre port, 4 lanes, 2 virtual channels; input VC buffers of 1024 words (`VcInDepth_g`), four flow control units of 256 words, as in the OpenFibre reference design for the VCK190: with more than one lane, an input VC buffer of one flow control unit can stop the traffic for good; starts the link after the reset (`LaneStart_g`) |
| `nchar0` | `openspp.spp_nchar` | Converts between the packet stream of virtual channel 0 and the N-Char stream of the RMAP node |
| `rmap0` | `openrmap.omap_core` | RMAP target; its memory master accesses the register map below with single AXI4-Lite transfers |
| `spp0` | `openspp.spp_core` | Space Packet node on virtual channel 1, 4 send and 4 receive ports, 8 KiB buffers |
| `frm0` to `frm3` | `opencrypto.ocr_framer` | Cuts the user data of a received Space Packet into chunks and creates one encryption per chunk |
| `ocr0` to `ocr3` | `opencrypto.ocr_core` | AES-GCM cores, 128-bit data path, one per encryption channel |
| `dec0` | `olo.olo_axi_lite_decoder` | Splits the AXI4-Lite master of `rmap0` into 8 register windows |
| `edac0` | `olo.olo_ft_ecc_monitor_axi` | Counts the corrected and uncorrectable errors of the 8 framer memories |
| `auto_rst_sys` | `olo.olo_base_reset_gen` | Synchronous reset of the system clock domain (triplicated synchronisers) |

## Data paths

- **RMAP:** virtual channel 0 of `ofb0` ↔ `nchar0` ↔ `rmap0`. The RMAP target accepts commands to the logical
  address 0xFE with the key 0x00 on the whole register map after the reset.
- **Encryption channel i (i = 0 to 3):** virtual channel 1 of `ofb0` → `spp0` receive port i → `frm<i>` →
  `ocr<i>` → `spp0` send port i with APID i → virtual channel 1. The receive APID table of `spp0` selects the channel
  of a received Space Packet; after the reset every APID goes to receive port 0. The AES-GCM core of a channel needs
  a loaded key slot with generated IVs before the first packet.

## Clocks and reset

| Clock | Frequency | Source | Used by |
| --- | --- | --- | --- |
| `Clk_sys` | 200 MHz | top-level port | all blocks; user, management and free-running clock of `ofb0` and `pa0` |
| lane clock | 156.25 MHz | `pa0` (transmit user clock of the GTY quad) | core and lane clock of `ofb0` |

`Rst` is the asynchronous, high-active reset of the system. `auto_rst_sys` releases it synchronously to `Clk_sys` for
the blocks with a synchronous reset. `ofb0` and `pa0` take the asynchronous `Rst` directly: `ofb0` releases it with
one reset generator per clock domain, `pa0` releases it with a reset generator in its free-running clock and resets
the transceivers and their reset controller with it. `ofb0` holds its link in Near-End Reset until the transmitters of
`pa0` are ready (`Stat_TxReady` of `pa0` to `Phy_TxReady` of `ofb0`).

## Top-level ports

| Port | Direction | Type | Function |
| --- | --- | --- | --- |
| `Rst` | in | `std_logic` | Asynchronous reset, high-active |
| `Clk_sys` | in | `std_logic` | System clock, 200 MHz |
| `pa0_Gt_TxP`, `pa0_Gt_TxN` | out | `std_logic_vector(3 downto 0)` | Transmit lanes of the GTY quad |
| `pa0_Gt_RxP`, `pa0_Gt_RxN` | in | `std_logic_vector(3 downto 0)` | Receive lanes of the GTY quad |
| `pa0_RefClk` | in | `std_logic` | Reference clock of the quad at the frequency of the transceiver wizard instance of the target (VCK190: 100 MHz or 156.25 MHz), output O of the IBUFDS_GTE5 in the board top level |

## Register map

`dec0` gives every register port a window of 64 KiB (`PortAddrWidth_g = 16`). Addresses are RMAP memory addresses of
`rmap0`; the map repeats every 512 KiB.

| Address | Block | Registers |
| --- | --- | --- |
| 0x0000_0000 | `spp0` | Space Packet node: APID tables, destinations, counters |
| 0x0001_0000 | `ocr0` | AES-GCM core of channel 0 |
| 0x0002_0000 | `edac0` | ECC monitor; channels 2i and 2i + 1 are the chunk buffer and the position FIFO of `frm<i>` |
| 0x0003_0000 | `ocr1` | AES-GCM core of channel 1 |
| 0x0004_0000 | `ocr2` | AES-GCM core of channel 2 |
| 0x0005_0000 | `ocr3` | AES-GCM core of channel 3 |
| 0x0006_0000 | `rmap0` | RMAP target: logical addresses, key, windows |
| 0x0007_0000 | `ofb0` | SpaceFibre port: lanes, data link, virtual channels |

The register maps of the blocks are in the user guides of
[OpenSPP](https://openspacehdl.org/openspp/), [OpenCrypto](https://openspacehdl.org/opencrypto/),
[OpenRMAP](https://openspacehdl.org/openrmap/), [OpenFibre](https://openspacehdl.org/openfibre/) and
[Open Logic FT](https://openspacehdl.org/open-logic-ft/).
