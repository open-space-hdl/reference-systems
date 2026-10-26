# spfi_compress on the VCK190: Hardware Test Procedure

## 1. Purpose

This procedure tests the system spfi_compress on the AMD VCK190 evaluation board against a STAR-Dundee
STAR-Ultra PCIe as the far end of the SpaceFibre link. The far end reaches every register of the system with RMAP
commands on virtual channel 0, sends the samples of test images as Space Packets on virtual channel 1 and checks the
compressed images that come back in Space Packets of 4096 bytes at most: the Space Packets of every channel must be
the chunks of its compressed images, and every compressed image must equal the image of the reference model of
OpenCompact for the configuration of its channel. The script
[`systems/spfi_compress/sw/hardware_test.py`](../../../sw/hardware_test.py) runs all test cases.

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
| Python 3.10 or later on the test computer | Test script and the reference model of OpenCompact |

## 3. Repository as Git bundles

As for spfi_encrypt (its procedure, section 3): `tools/make_offline_bundle.py` writes one bundle per core in
`cores/`, and `README.txt` lists the commands to clone them. The system uses `cores/opencompact` and not
`cores/opencrypto`, whose bundle the package carries for the other systems. The test computer needs
`cores/opencompact` also for the test: the reference model of OpenCompact (`tools/ocp_model.py`) and the register map
of its status interface (`sw/ocp_regs.h`) come from there.

## 4. Build

The transceiver reference clock has two sources on the board, and the device image fixes the pins it takes the clock
from and its frequency. Confirm the reference clock on the board first (section 5, step 2), then build the image of
that option only, from the root of the repository:

```shell
# Option A: oscillator Si570 U205 at 100.000 MHz, its frequency after power-up (default)
vivado -mode batch -source systems/spfi_compress/targets/vck190/tcl/build.tcl
# Option B: oscillator Si570 U205 set to 156.250 MHz
vivado -mode batch -source systems/spfi_compress/targets/vck190/tcl/build.tcl -tclargs all si570 156.25
# Option C: clock generator 8A34001, output Q1 at 156.25 MHz
vivado -mode batch -source systems/spfi_compress/targets/vck190/tcl/build.tcl -tclargs all 8a34001
```

The script and its steps, the reference clock files and the clock constraint of the selected frequency, the project path
limit on Windows (`RS_VIVADO_OUT`, for example `C:/git/rs`) and the files in `results/` are those of spfi_encrypt (its
procedure, section 4), with the name `spfi_compress_vck190` for the project and the device image. The timing
summary must show WNS and WHS of at least 0 ns for `Clk_sys` at the frequency of the Composer file and for the lane
clock at 156.25 MHz; the hierarchical utilisation report gives the resources of every OpenCompact channel. As in
spfi_encrypt, the script sets `XILINX_LOCAL_USER_DATA=no` for the runs of `launch_runs` (AMD AR 63253).

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
the channels with their compressors, the generics of every compressor, the chunk size of every chunker, the output
APIDs and the buffer sizes from the Composer file of the system, and the register maps from the headers of the cores:

| Item | Value |
| --- | --- |
| RMAP target `rmap0` | Logical address 0xFE, key 0x00, the whole register map accessible (reset values) |
| Initiator logical address | 0xFE |
| Register windows | `SPFI_COMPRESS_<BANK>_BASE` of `hdl/spfi_compress_regs.h`, which the Composer exports with the top level |
| Input APIDs | 0x010 + i for channel i: the receive APID table of `spp0` routes APID 0x010 + i to receive port i and checks its Packet Sequence Count (`RX_TABLE_ENTRY.CHECK`) |
| Input Space Packets | The samples of an image in the input order of the compressor of the channel, ceil(D/8) bytes per sample, most significant byte first, at most 8186 bytes of whole samples per Space Packet; consecutive Packet Sequence Counts per APID; the last Space Packet of an image with the User Application value 0x01 in its CPTP packet, the others with 0x00 |
| Test images | Five kinds, generated from the seed of the script: a scene of three materials with sensor noise, the scene with column stripes, uniform noise, flat bands with sparse steps, flat bands with sparse spikes |
| Test window | Window 3 of `rmap0`, the last of its four windows, disabled after the reset: TC-RS-HW-04 writes its control word (enable bit 0), first and last address with one command and restores them |
| Output | Space Packets with APID i (channel i) to destination 0 (Target Logical Address 0xFE), CPTP on virtual channel 1, Sequence Flags '11': every compressed image cut into chunks of 4096 bytes, the last chunk of an image with the rest; every image starts in a new Space Packet |
| Runtime configuration | `ocp0` and `ocp3` (`RuntimeConfig_g`): the image dimensions of every image, on `ocp3` also its absolute error limit value, from `NEXT_X` to `NEXT_LIMITS` and `CONTROL.COMMIT` (OpenCompact register map); after a reset the values of the generics. TC-RS-HW-10 writes them and ends with the values of the reset; the other test cases use the values of the reset |

## 8. Tests

Run the script on the test computer from the root of the repository:

```shell
python systems/spfi_compress/sw/hardware_test.py --device <STAR-Ultra> --profile lab --seed 1
```

`<STAR-Ultra>` is the name of the STAR-Ultra in STAR-System. Before the first test case the script reads channels 0
and 1 until no packet arrives within 0.5 s and prints how many packets of an earlier run it discarded per channel. It
prints one line per test case and stops at the first failure, since the later test cases build on the earlier ones;
TC-RS-HW-09 also prints the measured rate. `--seed` sets the images and the words that TC-RS-HW-04 writes,
`--test TC-RS-HW-nn` runs one test case and the test cases it builds on. Before it sends the images of a test case, the
script computes their compressed images with the reference model of OpenCompact, a few seconds per image.

The expected compressed images come from the reference model of OpenCompact for the generics of the compressor of
every channel in the Composer file; the expected images and counters after lost Space Packets come from the error
cases of the OpenCompact channel (OpenCompact, specification of `ocp_channel`, section 3), which the script models.

| Test | Steps | Pass criterion |
| --- | --- | --- |
| TC-RS-HW-01 Link | As spfi_encrypt | Link initialised, all lanes of `ofb0` active |
| TC-RS-HW-02 Identification | Read the identification register of every register window; read the configuration registers of the status interface of `ocp0` to `ocp3` (`GENERICS` to `BLOCK`, and `LIMITS`) | `ID` of every register bank with an identification = its `SPFI_COMPRESS_<BANK>_ID`, `ocp0` to `ocp3` included (0x4F435002); `INFO` of `edac0` gives its channels and counter width; every configuration register of a compressor, `LIMITS` included, gives the generics of that compressor in the Composer file |
| TC-RS-HW-03 RMAP | As spfi_encrypt | Every value reads back; the wrong key gives the status "invalid key" (3) |
| TC-RS-HW-04 Commands of several words | Read the configuration registers of `ocp0` (`ID` to `BLOCK`, 48 bytes) with one command and word by word; write `WIN_CTRL`, `WIN_BASE` and `WIN_LAST` of the test window with one verified command, read them word by word, write back the values read before | Both reads give the same words; the window was disabled before; the three registers hold the written values; the window is restored |
| TC-RS-HW-05 Configuration | Receive APID table of `spp0` (APID 0x010 + i to port i, continuity check on); the packet and loss counters of `spp0` and the image, SDU and memory error counters of every compressor cleared | Table entries read back with the port, the enable and the continuity check; no table overrun |
| TC-RS-HW-06 Compression | Per channel one image of every kind (five images), the four channels interleaved Space Packet by Space Packet | Every compressed image comes back in Space Packets with the APID of its channel and consecutive sequence counts; the Space Packets of every channel are the chunks of its compressed images in order, of 4096 bytes and the rest, every image starting in a new Space Packet; every compressed image equals the image of the reference model, its header included |
| TC-RS-HW-07 Counters | Read the counters of `spp0`, of the status interfaces of the compressors and of `edac0` | Received and sent Space Packets match TC-RS-HW-06; `IMAGES_DONE` of every compressor is 5; `RX_LOSS`, `IMAGES_ABORTED`, `SDU_ERRORS`, `SDUS_DISCARDED` and the memory error counters of the compressors and of `edac0` are 0 |
| TC-RS-HW-08 Lost data | Counters cleared, then per channel five images: (a) image 1 without its 25th Space Packet (of 49), image 2 intact; (b) image 3 without its last Space Packet, images 4 and 5 intact. The script leaves the Space Packets out and skips their sequence counts | (a) The Space Packet after the gap carries the Data Loss Indicator; image 1 ends after the samples of Space Packets 1 to 24 and 26 with an error, Space Packets 27 to 49 are discarded; image 2 equals the model. (b) Space Packet 1 of image 4 carries the Data Loss Indicator; its first 144 samples complete image 3, which ends with an error and equals the model of these samples; the rest of image 4 is discarded and gives no output; image 5 equals the model. Every image, the aborted ones included, comes back as its chunks and equals the compressed image of the model (for an aborted image: of the samples taken). Per channel `IMAGES_DONE` 2, `IMAGES_ABORTED` 2, `SDU_ERRORS` 2, `SDUS_DISCARDED` 23 + 48 = 71; `RX_LOSS` of `spp0` 8 |
| TC-RS-HW-09 Sustained rate | Counters cleared, then eight images per channel, all channels at once, as fast as the test equipment sends | No loss, every compressed image equals the model, the counters match; the rate of the samples, compressed bytes and images is recorded. It is limited by the test equipment and the host as long as they send less than the system takes (9.6 Gbit/s of samples for compressed images of at most 32 bits per sample, [README](../../../README.md#throughput), section Throughput) |
| TC-RS-HW-10 Runtime configuration | `ocp0` and `ocp3` have the runtime configuration (`RuntimeConfig_g`), `ocp1` and `ocp2` do not. Counters cleared. Read `CONTROL` to `NEXT_LIMITS` of `ocp0` and `ocp3`; write `NEXT_X` to `NEXT_LIMITS` and `CONTROL.COMMIT` of `ocp1` and `ocp2`. Then 21 images on each of channels 0 and 3, every one after its values in `NEXT_X` to `NEXT_LIMITS` (one command) and `CONTROL.COMMIT`: the seeded sequence of the runtime testbench of OpenCompact (first the bounds of every dimension, 1 or 2 to 64 and 48, and of the error limit value of channel 3, 0 to 1023, then 12 images at random), then the values of the README. After the first COMMIT, COMMITs with one value out of range: NX 1 (channel 0 only), NX 65, NY 0, NZ 49 and the absolute error limit value 1024 (channel 3 only), each followed by a write of 1 to `STATUS.CFG_ERROR`. With the first image, one image of the generics on each of channels 1 and 2 | `ocp0` and `ocp3` read the values of the reset (`CONTROL` and `STATUS` 0, `NEXT_X` to `NEXT_Z` 64, 64 and 48, `NEXT_LIMITS` 0 and 12); `ocp1` and `ocp2` read 0 in the six registers and keep `IMAGE_X` to `LIMITS`. Every valid COMMIT gives `STATUS.CFG_PENDING` 1; every COMMIT with a value out of range gives `STATUS.CFG_ERROR` 1 with `CFG_PENDING` still 1, and the register reads back the value written; every compressed image comes back as its chunks and equals the image of the model for its values, header included; after every image `IMAGE_X` to `IMAGE_Z`, `ORDER` and `LIMITS` show its values and `STATUS` is 0. The counters match: `IMAGES_DONE` 21 on channels 0 and 3 and 1 on channels 1 and 2, no error |

TC-RS-HW-10 ends with the values of the reset on channels 0 and 3. After a run that stopped in TC-RS-HW-10, program
the device again before the next run: the runtime registers and the values taken may still hold the values of an image
of its sequence, which TC-RS-HW-02 and TC-RS-HW-10 would report.

The Space Packets do not mark the end of an image (system [README](../../../README.md#images-on-the-link)): the
script knows the length of every compressed image from the model, the aborted images included, and checks the cut
points between the chunks as well as their bytes.

### When the link does not come up

As for spfi_encrypt (its procedure, section 8): record the link and lane states of port 1 in STAR-System, the LEDs of
the VCK190 and the reference clock (option, value read back, `results/build_info.txt`), correct what the records
show and run the script again; after the QSFP cable was unplugged or moved, reset the link of port 1 in STAR-System.
A lane of the system stays in Started for up to 3 ms per initialisation attempt, and its receiver resets at every
restart; TC-RS-HW-01 records the receiver resets once the link is up.

## 9. Records

Keep for every build and every test run, with the date:

| Item | Source |
| --- | --- |
| Commits of the repository and of the submodules | `results/build_info.txt` |
| Build results | `results/timing_summary.rpt`, `results/utilization.rpt`, `results/utilization_hierarchical.rpt`, `results/methodology.rpt`, `results/drc.rpt`, `results/invalid_constraints.xdc` |
| Board state | Reference clock: option and the value read back; LEDs after programming, version of the STAR-System software |
| Link state | When the link did not come up: the link and lane states of port 1 in STAR-System |
| Test run | Command line (profile, seed) and the complete output of the script with the `RECORD` lines of TC-RS-HW-01, the rate of TC-RS-HW-09 included, for example redirected into a file |

A failing test case is recorded with its output and the register values it reports.
