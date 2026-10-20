# spfi_compress_encrypt on the VCK190: Hardware Test Procedure

## 1. Purpose

This procedure tests the system spfi_compress_encrypt on the AMD VCK190 evaluation board against a STAR-Dundee
STAR-Ultra PCIe as the far end of the SpaceFibre link. The far end reaches every register of the system with RMAP
commands on virtual channel 0, loads the keys of the four channels, sends the samples of test images as Space Packets
on virtual channel 1 and checks the compressed and encrypted images that come back: every chunk must authenticate and
decrypt, and every compressed image must equal the image of the reference model of OpenCompact for the configuration
of its channel. The script [`systems/spfi_compress_encrypt/sw/hardware_test.py`](../../../sw/hardware_test.py) runs
all test cases. For OpenCompact this test is the first test of the compressor in hardware.

The board, the far end, the cable and the reference clock are those of spfi_encrypt
([its procedure](../../../../spfi_encrypt/targets/vck190/docs/hardware_test.md)); this procedure repeats what the
test needs and describes what differs.

## 2. Material

| Item | Use |
| --- | --- |
| VCK190 evaluation board (production silicon), power supply, USB cable to the JTAG and UART port | Device under test |
| STAR-Ultra PCIe in a test computer, STAR-System installed on that computer with its Python package | Far end, test computer |
| Straight QSFP cable (four lanes) | QSFP1 of the VCK190 to port 1 of the STAR-Ultra |
| Build computer with AMD Vivado 2025.2 (device support and a synthesis licence for the XCVC1902) and Git | Build and programming |
| The repository with its submodules: a clone from GitHub, or the offline package (Git bundles and `README.txt`) for a computer without network access | Sources of the build and of the test script |
| Python 3.10 or later on the test computer with `pycryptodome` | Test script and the reference model of OpenCompact |

## 3. Repository as Git bundles

As for spfi_encrypt (its procedure, section 3), with one more core: `tools/make_offline_bundle.py` writes one bundle
per core in `cores/`, `opencompact.bundle` included, and `README.txt` lists the commands to clone them. The test
computer needs `cores/opencompact` also for the test: the reference model of OpenCompact (`tools/ocp_model.py`) and
the register map of its status interface (`sw/ocp_regs.h`) come from there.

## 4. Build

The transceiver reference clock has two sources on the board, and the device image fixes the pins it takes the clock
from and its frequency. Confirm the reference clock on the board first (section 5, step 2), then build the image of
that option only, from the root of the repository:

```shell
# Option A: oscillator Si570 U205 at 100.000 MHz, its frequency after power-up (default)
vivado -mode batch -source systems/spfi_compress_encrypt/targets/vck190/tcl/build.tcl
# Option B: oscillator Si570 U205 set to 156.250 MHz
vivado -mode batch -source systems/spfi_compress_encrypt/targets/vck190/tcl/build.tcl -tclargs all si570 156.25
# Option C: clock generator 8A34001, output Q1 at 156.25 MHz
vivado -mode batch -source systems/spfi_compress_encrypt/targets/vck190/tcl/build.tcl -tclargs all 8a34001
```

The script and its steps, the reference clock files and the clock constraint of the selected frequency, the project path
limit on Windows (`RS_VIVADO_OUT`, for example `C:/git/rs`) and the files in `results/` are those of spfi_encrypt (its
procedure, section 4), with the name `spfi_compress_encrypt_vck190` for the project and the device image. The timing
summary must show WNS and WHS of at least 0 ns for `Clk_sys` at the frequency of the Composer file and for the lane
clock at 156.25 MHz; the hierarchical utilisation report gives the resources of every OpenCompact channel.

## 5. Board set-up

As for spfi_encrypt (its procedure, section 5): boot mode JTAG, the reference clock of the option (option A: Si570 at
100.000 MHz, its frequency after power-up, no setting; option B: Si570 set to 156.250 MHz in the System Controller after
every power cycle; option C: 8A34001 output Q1 at 156.25 MHz), QSFP1 of the VCK190 to port 1 of the STAR-Ultra, then the
device image of the option programmed with the Vivado Hardware Manager. `results/build_info.txt` names the
reference clock and the frequency of the image.

## 6. Far-end configuration

As for spfi_encrypt (its procedure, section 6):

| Parameter | Value |
| --- | --- |
| Port | Port 1 of the STAR-Ultra, four lanes, 6.25 Gbit/s per lane, 8B/10B |
| Data scrambling | On; set per direction (ECSS-E-ST-50-11C 5.7.6.2), the system scrambles what it sends |
| Link start | Auto-start; the system starts the link after a reset |
| Channel 0 | Virtual channel 0 of port 1 through the routing switch of the STAR-Ultra: path address 3 in front of every command, the reply address 1 in front of every reply |
| Channel 1 | Virtual channel 1 of port 1 |

## 7. System configuration

The script uses the reset values of the system and writes only what the tests need. It takes the register windows,
the channels with their compressors, the generics of every compressor, the output APIDs and key slots, the chunk
size and the buffer sizes from the Composer file of the system, and the register maps from the headers of the cores:

| Item | Value |
| --- | --- |
| RMAP target `rmap0` | Logical address 0xFE, key 0x00, the whole register map accessible (reset values) |
| Initiator logical address | 0xFE |
| Register windows | `SPFI_COMPRESS_ENCRYPT_<BANK>_BASE` of `hdl/spfi_compress_encrypt_regs.h`, which the Composer exports with the top level |
| Input APIDs | 0x010 + i for channel i: the receive APID table of `spp0` routes APID 0x010 + i to receive port i and checks its Packet Sequence Count (`RX_TABLE_ENTRY.CHECK`) |
| Input Space Packets | The samples of an image in the input order of the compressor of the channel, ceil(D/8) bytes per sample, most significant byte first, at most 8186 bytes of whole samples per Space Packet; consecutive Packet Sequence Counts per APID; the last Space Packet of an image with the User Application value 0x01 in its CPTP packet, the others with 0x00 |
| Test images | Five kinds, generated from the seed of the script: a scene of three materials with sensor noise, the scene with column stripes, uniform noise, flat bands with sparse steps, flat bands with sparse spikes |
| Keys | One 256-bit key per AES-GCM core in slot 0, the slot its framer uses, with generated IVs; keys and IV values are derived from the seed |
| Test slot | Slot 1 of `ocr0`, which its framer does not use, holds a further key and IV values for the commands of several words |
| Output | Space Packets with APID i (channel i) to destination 0 (Target Logical Address 0xFE), CPTP on virtual channel 1; one or more chunks per compressed image |

## 8. Tests

Run the script on the test computer from the root of the repository:

```shell
python systems/spfi_compress_encrypt/sw/hardware_test.py --device <STAR-Ultra> --profile lab --seed 1
```

`<STAR-Ultra>` is the name of the STAR-Ultra in STAR-System. Before the first test case the script reads channels 0
and 1 until no packet arrives within 0.5 s and prints how many packets of an earlier run it discarded per channel. It
prints one line per test case and stops at the first failure, since the later test cases build on the earlier ones;
TC-RS-HW-09 also prints the measured rate. `--seed` sets the keys and the images, `--test TC-RS-HW-nn` runs one test
case and the test cases it builds on. Before it sends the images of a test case, the script computes their compressed
images with the reference model of OpenCompact, a few seconds per image.

The expected compressed images come from the reference model of OpenCompact for the generics of the compressor of
every channel in the Composer file; the expected images and counters after lost Space Packets come from the error
cases of the OpenCompact channel (OpenCompact, specification of `ocp_channel`, section 3), which the script models.

| Test | Steps | Pass criterion |
| --- | --- | --- |
| TC-RS-HW-01 Link | As spfi_encrypt | Link initialised, all lanes of `ofb0` active |
| TC-RS-HW-02 Identification | Read the identification register of every register window; read the configuration registers of the status interface of `ocp0` to `ocp3` (`GENERICS` to `BLOCK`) | `ID` of every register bank with an identification = its `SPFI_COMPRESS_ENCRYPT_<BANK>_ID`, `ocp0` to `ocp3` included (0x4F435001); `INFO` of `edac0` gives its channels and counter width; every configuration register of a compressor gives the generics of that compressor in the Composer file |
| TC-RS-HW-03 RMAP | As spfi_encrypt | Every value reads back; the wrong key gives the status "invalid key" (3) |
| TC-RS-HW-04 Commands of several words | As spfi_encrypt | Both reads of 16 bytes give the words of the single reads; the test slot holds the written fixed field and invocation counter |
| TC-RS-HW-05 Configuration | Receive APID table of `spp0` (APID 0x010 + i to port i, continuity check on); slot 0 of every AES-GCM core loaded with the key and IV values of its channel and queried; the packet, loss and encryption counters and the image, SDU and memory error counters of every compressor cleared | Table entries read back with the port, the enable and the continuity check; no table overrun; every slot is valid with generated IVs and holds the fixed field and invocation counter of its channel |
| TC-RS-HW-06 Compression and encryption | Per channel one image of every kind (five images), the four channels interleaved Space Packet by Space Packet | Every compressed image comes back as chunks with the APID of its channel and consecutive sequence counts; every chunk authenticates and decrypts with the key of its channel, carries the fixed field of its channel, the invocation counters start at the loaded one and have no gap; the chunks of every image, first to last position, give exactly the compressed image of the reference model, its header included |
| TC-RS-HW-07 Counters | Read the counters of `spp0`, of the AES-GCM cores, of the status interfaces of the compressors and of `edac0` | Received Space Packets, sent chunks and encryptions match TC-RS-HW-06; `IMAGES_DONE` of every compressor is 5; `RX_LOSS`, `IMAGES_ABORTED`, `SDU_ERRORS`, `SDUS_DISCARDED`, the memory error counters of the compressors and of `edac0` and every error counter of the AES-GCM cores are 0 |
| TC-RS-HW-08 Lost data | Counters cleared, then per channel five images: (a) image 1 without its 25th Space Packet (of 49), image 2 intact; (b) image 3 without its last Space Packet, images 4 and 5 intact. The script leaves the Space Packets out and skips their sequence counts | (a) The Space Packet after the gap carries the Data Loss Indicator; image 1 ends after the samples of Space Packets 1 to 24 and 26 with an error, Space Packets 27 to 49 are discarded; image 2 equals the model. (b) Space Packet 1 of image 4 carries the Data Loss Indicator; its first 144 samples complete image 3, which ends with an error and equals the model of these samples; the rest of image 4 is discarded and gives no output; image 5 equals the model. Per channel `IMAGES_DONE` 2, `IMAGES_ABORTED` 2, `SDU_ERRORS` 2, `SDUS_DISCARDED` 23 + 48 = 71; `RX_LOSS` of `spp0` 8; the aborted image 1 has the complete header and the start of the codewords of its samples, fill bits up to a multiple of 4 bytes |
| TC-RS-HW-09 Sustained rate | Counters cleared, then eight images per channel, all channels at once, as fast as the test equipment sends | No loss, every compressed image equals the model, the counters match; the rate of the samples, compressed bytes and images is recorded. It is limited by the test equipment and the host as long as they send less than the system takes (9.6 Gbit/s of samples for compressed images of at most 32 bits per sample, [README](../../../README.md#throughput), section Throughput) |

The key registers are write-only. TC-RS-HW-04 and TC-RS-HW-05 check the IV values through the slot query;
TC-RS-HW-06 checks the keys, since every chunk must authenticate and decrypt with the key of its channel.

### When the link does not come up

As for spfi_encrypt (its procedure, section 8): record the link and lane states of port 1 in STAR-System, the LEDs of
the VCK190 and the reference clock (option, value read back, `results/build_info.txt`), correct what the records
show and run the script again; after the QSFP cable was unplugged or moved, reset the link of port 1 in STAR-System.

## 9. Records

Keep for every build and every test run, with the date:

| Item | Source |
| --- | --- |
| Commits of the repository and of the submodules | `results/build_info.txt` |
| Build results | `results/timing_summary.rpt`, `results/utilization.rpt`, `results/utilization_hierarchical.rpt`, `results/methodology.rpt`, `results/drc.rpt`, `results/invalid_constraints.xdc` |
| Board state | Reference clock: option and the value read back; LEDs after programming, version of the STAR-System software |
| Link state | When the link did not come up: the link and lane states of port 1 in STAR-System |
| Test run | Command line (profile, seed) and the complete output of the script, the rate of TC-RS-HW-09 included, for example redirected into a file |

A failing test case is recorded with its output and the register values it reports.
