# spfi_compress on the VCK190: Hardware Test Procedure

## 1. Purpose

This procedure tests the system spfi_compress on the AMD VCK190 evaluation board against a STAR-Dundee
STAR-Ultra PCIe as the far end of the SpaceFibre link. The far end reaches every register of the system with RMAP
commands on virtual channel 0, sends the samples of test images as Space Packets on virtual channel 1 and checks the
compressed images that come back in Space Packets of 4096 bytes at most: the Space Packets of every channel must be
the chunks of its compressed images, and every compressed image must equal the image of the reference model of
OpenCompact for the configuration of its channel. The script
[`systems/spfi_compress/sw/hardware_test.py`](../../../sw/hardware_test.py) runs all test cases.

Besides the data paths, TC-RS-HW-11 to TC-RS-HW-17 provoke and check the error handling of the SpaceFibre port, the
RMAP target and the Space Packet node and the protection of their memories, and check at the end of the run that no
error register counted on its own. The procedure is the hardware test plan of the cores in the system: section 10
lists per core the requirements that the test cases exercise and those they do not, with the reason.

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

The test computer needs nothing else. The reference data come only from the reference model of OpenCompact
(`cores/opencompact/tools`) and the golden files committed in the repository; the script starts no other program and
imports only the standard library, the modules of the repository and of OpenCompact, and the Python package of
STAR-System (`sw/tests/test_lab_software.py` checks the sources). `--check-environment` checks the test computer
(section 8).

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
| Runtime configuration | `ocp0` and `ocp3` (`RuntimeConfig_g`, `RuntimeParams_g`): the image dimensions of every image, on `ocp3` also its absolute error limit value, from `NEXT_X` to `NEXT_LIMITS`, and its runtime parameters from `NEXT_SAMPLES` to `NEXT_BLOCK`, with `CONTROL.COMMIT` (OpenCompact register map); after a reset the values of the generics. TC-RS-HW-10 writes them and ends with the values of the reset; the other test cases use the values of the reset |

## 8. Tests

Run the script on the test computer from the root of the repository:

```shell
python systems/spfi_compress/sw/hardware_test.py --check-environment
python systems/spfi_compress/sw/hardware_test.py --model --profile lab --seed 1
python systems/spfi_compress/sw/hardware_test.py --device <STAR-Ultra> --profile lab --seed 1 --records <directory>
```

`--check-environment` prints one line for Python, the reference model of OpenCompact (with or without its decoder) and
the Python package of STAR-System, and ends with exit code 1 when one is missing. `--model` runs the test cases against
the software model of the system (`sw/system_model.py`) instead of the board: a dry run of the script and of the
reference model on the test computer. `--records` writes the records of the run into a JSON file in the directory
(section 9). `<STAR-Ultra>` is the name of the STAR-Ultra in STAR-System. Before the first test case the script
reads channels 0 and 1 until no packet arrives within 0.5 s and prints how many packets of an earlier run it discarded
per channel. It prints one line per test case and stops at the first failure, since the later test cases build on
the earlier ones;
TC-RS-HW-09 also prints the measured rate. `--seed` sets the images and the words that TC-RS-HW-04 writes,
`--test TC-RS-HW-nn` runs one test case and the test cases it builds on. Before it sends the images of a test case, the
script computes their compressed images with the reference model of OpenCompact, a few seconds per image.

TC-RS-HW-12 asks the operator for one step: reset the link of port 1 in STAR-System (a link reset of that port,
nothing else) while no traffic is in flight, then press Enter; the script then waits until the RMAP target answers
again. The script runs the test cases in the order of the table: TC-RS-HW-13, which takes a lane out of the link,
runs last before TC-RS-HW-17, so that a stop in it does not cost the results of the other test cases.

Every complete compressed image is also decoded with the decoder of the reference model of OpenCompact and compared
with the image sent: equal for the lossless channels, every sample within its maximum error on `ocp3`. With a version
of OpenCompact whose model has no decoder the check is skipped, and the `RECORD` lines and the records say so.

TC-RS-HW-12, 13 and 16 send one image per channel (a round of images) after the event they provoke, TC-RS-HW-14
three rounds. Before a test case that provokes errors clears the error registers, it checks that only the registers
it targets moved: every other error register of every block must still read 0.

The expected compressed images come from the reference model of OpenCompact for the generics of the compressor of
every channel in the Composer file; the expected images and counters after lost Space Packets come from the error
cases of the OpenCompact channel (OpenCompact, specification of `ocp_channel`, section 3), which the script models.

| Test | Steps | Pass criterion |
| --- | --- | --- |
| TC-RS-HW-01 Link | As spfi_encrypt; then the baseline of the error registers of every block (section 10): read, the registers that hold a value printed as a `RECORD` line, cleared and read again | Link initialised, all lanes of `ofb0` active; after the clear every error register reads 0, `RXERR_COUNT` of every lane at most its first value |
| TC-RS-HW-02 Identification | Read the identification register of every register window; read the configuration registers of the status interface of `ocp0` to `ocp3` (`GENERICS` to `BLOCK`, and `LIMITS`); read `GENERICS` and `BUFFERS` of `rmap0`, `GENERICS`, `SERVICES`, `TX_BUFFER` and `RX_BUFFER` of `spp0`, `GENERICS`, `DL_STATUS`, `ML_STATUS` and `LANE_STATUS` of every lane of `ofb0` | `ID` of every register bank with an identification = its `SPFI_COMPRESS_<BANK>_ID`, `ocp0` to `ocp3` included (0x4F435003); `INFO` of `edac0` gives its channels and counter width; every configuration register of a compressor, `LIMITS` included, gives the generics of that compressor in the Composer file; the generics registers of `rmap0`, `spp0` and `ofb0` give their generics in the Composer file (0x00040041, 0x00200100; 0x03020484, 0x000F000F, 8192, 8192; 0x00000402); `DL_STATUS.LINK_STATE` 3 and `TX_READY` 1; `ML_STATUS`: all four lanes sending and receiving, `ALIGN_STATE` 2, no bypass; every lane Active |
| TC-RS-HW-03 RMAP | As spfi_encrypt; then the error registers | Every value reads back; the wrong key gives the status "invalid key" (3); `rmap0` counts it (`TGT_CNT_ERR2.KEY` 1, `EVENTS.TGT_ERROR`) and no other error register moved; cleared |
| TC-RS-HW-04 Commands of several words | Read the configuration registers of `ocp0` (`ID` to `BLOCK`, 48 bytes) with one command and word by word; write `WIN_CTRL`, `WIN_BASE` and `WIN_LAST` of the test window with one verified command, read them word by word, write back the values read before | Both reads give the same words; the window was disabled before; the three registers hold the written values; the window is restored |
| TC-RS-HW-05 Configuration | Receive APID table of `spp0` (APID 0x010 + i to port i, continuity check on); the packet and loss counters of `spp0` and the image, SDU and memory error counters of every compressor cleared | Table entries read back with the port, the enable and the continuity check; no table overrun |
| TC-RS-HW-06 Compression | Per channel one image of every kind (five images), the four channels interleaved Space Packet by Space Packet | Every compressed image comes back in Space Packets with the APID of its channel and consecutive sequence counts; the Space Packets of every channel are the chunks of its compressed images in order, of 4096 bytes and the rest, every image starting in a new Space Packet; every Space Packet is telemetry without a secondary header, with the Sequence Flags '11' and the User Application value 0 in its CPTP packet; every compressed image equals the image of the reference model, its header included, and decodes to the image sent |
| TC-RS-HW-07 Counters | Read the counters of `spp0`, of the status interfaces of the compressors and of `edac0` | Received and sent Space Packets match TC-RS-HW-06; `IMAGES_DONE` of every compressor is 5; `RX_LOSS`, `IMAGES_ABORTED`, `SDU_ERRORS`, `SDUS_DISCARDED` and the memory error counters of the compressors and of `edac0` are 0 |
| TC-RS-HW-08 Lost data | Counters cleared, then per channel five images: (a) image 1 without its 25th Space Packet (of 49), image 2 intact; (b) image 3 without its last Space Packet, images 4 and 5 intact. The script leaves the Space Packets out and skips their sequence counts | (a) The Space Packet after the gap carries the Data Loss Indicator; image 1 ends after the samples of Space Packets 1 to 24 and 26 with an error, Space Packets 27 to 49 are discarded; image 2 equals the model. (b) Space Packet 1 of image 4 carries the Data Loss Indicator; its first 144 samples complete image 3, which ends with an error and equals the model of these samples; the rest of image 4 is discarded and gives no output; image 5 equals the model. Every image, the aborted ones included, comes back as its chunks and equals the compressed image of the model (for an aborted image: of the samples taken). Per channel `IMAGES_DONE` 2, `IMAGES_ABORTED` 2, `SDU_ERRORS` 2, `SDUS_DISCARDED` 23 + 48 = 71; `RX_LOSS` of `spp0` 8 |
| TC-RS-HW-09 Sustained rate | Counters cleared, then eight images per channel, all channels at once, as fast as the test equipment sends | No loss, every compressed image equals the model, the counters match; the rate of the samples, compressed bytes and images is recorded. It is limited by the test equipment and the host as long as they send less than the system takes (9.6 Gbit/s of samples for compressed images of at most 32 bits per sample, [README](../../../README.md#throughput), section Throughput) |
| TC-RS-HW-10 Runtime configuration | `ocp0` and `ocp3` have the runtime configuration and the runtime parameters (`RuntimeConfig_g`, `RuntimeParams_g`), `ocp1` and `ocp2` do not. Counters cleared. Read `CONTROL` to `NEXT_LIMITS` and `NEXT_SAMPLES` to `NEXT_BLOCK` of `ocp0` and `ocp3`; write `NEXT_X` to `NEXT_BLOCK` and `CONTROL.COMMIT` of `ocp1` and `ocp2`. Then 22 images on each of channels 0 and 3, every one after its values in `NEXT_X` to `NEXT_LIMITS` (one command), `NEXT_SAMPLES` to `NEXT_BLOCK` (one command) and `CONTROL.COMMIT`: the seeded sequence of the runtime parameter testbench of OpenCompact (first the bounds of every dimension, 1 or 2 to 64 and 48, of the error limit value of channel 3, 0 to 1023, and of every parameter in use, then images at random, then an image of the maximum dimensions with log2 t_inc 4 and vmin = vmax = -6), then the values of the README. After the first COMMIT, COMMITs with one value out of range: NX 1 (channel 0 only), NX 65, NY 0, NZ 49, the absolute error limit value 1024 (channel 3 only), log2 t_inc 3, vmax below vmin, gamma* equal to gamma0, Umax 33, K 15 (channel 0 only) and M above NZ (channel 0, when NZ is below 48), each followed by a write of 1 to `STATUS.CFG_ERROR`. With the first image, one image of the generics on each of channels 1 and 2 | `ocp0` and `ocp3` read the values of the reset (`CONTROL` and `STATUS` 0, `NEXT_X` to `NEXT_Z` 64, 64 and 48, `NEXT_LIMITS` 0 and 12, `NEXT_SAMPLES` to `NEXT_BLOCK` the parameters of the generics); `ocp1` and `ocp2` read 0 in the eleven registers and keep `IMAGE_X` to `LIMITS`. Every valid COMMIT gives `STATUS.CFG_PENDING` 1; every COMMIT with a value out of range gives `STATUS.CFG_ERROR` 1 with `CFG_PENDING` still 1, and the register reads back the value written; every compressed image comes back as its chunks, equals the image of the model for its values, header included, and decodes to the image sent; after every image `IMAGE_X` to `IMAGE_Z`, `SAMPLES`, `ORDER`, `WEIGHTS`, `ADAPTIVE`, `BLOCK` and `LIMITS` show its values and `STATUS` is 0. The counters match: `IMAGES_DONE` 22 on channels 0 and 3 and 1 on channels 1 and 2, no error |
| TC-RS-HW-11 SpaceFibre port | Write and read back `IRQ_MASK` (its documented fields), `DL_BC_INTERVAL`, `VC_IDLE_LIMIT`, `ECC_SELECT` and `LANE_INIT_TIMEOUT` of `ofb0`, each with a random value, its complement and all ones in its writable bits (`LANE_INIT_TIMEOUT` with bit 23 set, so that a lane never waits shorter than after the reset), then its value of the reset; `VC_CFG.VN` of both virtual channels with a random number, then its value of the reset | Every register reads its value of the reset before and every value written after; `VN` of VC 1 reads back, `VN` of VC 0 reads 0; every register restored |
| TC-RS-HW-12 Far-end link reset | Operator step: link reset of port 1 in STAR-System, no traffic in flight. The script waits until RMAP answers again, reads `DL_ERRORS`, `DL_STATUS`, `ML_STATUS` and every lane, then sends one image per channel | `DL_ERRORS.FAR_END_LINK_RESET` 1, no other bit of `DL_ERRORS`; link initialised with all lanes; the images equal the model, the counters match; the registers of the lanes (events, timeouts and receiver resets, `RXERR_COUNT`), `ML_MISALIGNED` and `DL_RETRIES` of the restart are recorded, no other error register moved; cleared |
| TC-RS-HW-14 Memory errors | `ECC_INJECT` of `ofb0`, `rmap0` and `spp0` (section 10): single errors into `ofb0` channels 0 to 3 and 6 to 8, `rmap0` channels 0 to 2, and the receive data buffer, descriptor and table and the send data buffer and descriptor of channel 0 and the send table of `spp0`, then one image per channel; double errors into `ofb0` channels 6 and 7, each followed by one image per channel; a double error into the write buffer of `rmap0`, followed by a verified write of `WIN_BASE` of window 3; a double error into the receive descriptor of `spp0`, followed by a Space Packet of APID 0x021 | The images equal the model, the counters match. Single errors: every monitor channel counts its error, `SEC` exactly 1 on `ofb0` channels 2, 6, 7 and 8, `rmap0` channels 0 to 2 and `spp0` monitor channels 0, 1, 4 and 5, 1 or 2 on `ofb0` channels 0 and 3 (one buffer per virtual channel), at least 1 on `ofb0` channel 1 (retry buffer with its scrubber) and on the read or scrubber channel of each table of `spp0`; no uncorrectable error. Double errors: `DED` 1 on `ofb0` channels 6 and 7 with the link up; the verified write ends with the status 1 (`TGT_CNT_ERR1.GENERAL` 1, `DED` 1 on `rmap0` channel 0); the Space Packet is discarded with the code 0x1B (`ERR_RX6.BUFFER` 1, `DED` 1 on monitor channel 5). The retries and the error counters of the data link layer are recorded; no other error register moved; cleared |
| TC-RS-HW-15 RMAP errors | Commands built byte by byte (`sw/refsys/rmaptest.py`): target logical address 0x55; wrong key on a write; both; command code 0110; data CRC error of a verified and of a non-verified write; early EOP and too much data of a verified write; a verified write of 260 bytes; a read-modify-write of 3 bytes; Extended Address 1. Without a reply: header CRC error, a header that ends early, a reply packet, a reserved packet type, a CPTP packet on virtual channel 0. Executed: a non-verified write, a write without a reply, single-address write and read of two words, write and read of no data, a read-modify-write of `IRQ_ENA` of `edac0`, reads with a Reply Address field of 8 and 12 bytes, a read at 0x80000. Windows: window 3 opened for `rmap0`, window 0 restricted to verified writes, then to no writes, restored through window 3, window 3 closed | The statuses 12, 3 (register unchanged), 12, 2, 4 (unchanged), 5 and 6 (unchanged), 9 (unchanged), 11, 10 and 4 (data written); no reply to the discarded packets; the executed commands as specified, the read at 0x80000 with the identification of `spp0` (the decoder has 8 ports, so the map repeats every 0x80000 bytes); the non-verified and then the verified write outside window 3 refused with 10, window 0 restored. `TGT_LAST` shows every command with its status, instruction, transaction identifier and address, unchanged by the discarded packets; the counters of `rmap0` equal the sum of the cases, no other error register moved; cleared |
| TC-RS-HW-16 Space Packet errors | Space Packets on virtual channel 1 that `spp0` discards (`sw/refsys/spptest.py`): Protocol Identifier 3, reserved byte 0x5A, a Space Packet of 6 bytes, version 1, a Packet Data Length that does not match, a Space Packet of 8193 bytes, the idle APID 0x7FF, APID 0x030 with its table entry disabled, Sequence Flags '01'. Then one image per channel with: the input APID of channel 0 preset to `LAST` 16382 and its output APID to the count 16383, the input APID of channel 3 without the continuity check and a skipped count, `CONFIG.ROUND_ROBIN` 1. Tables and `CONFIG` restored | Each discard: `LAST_ERROR` with its code (0x11, 0x12, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A), port and APID, its counter in `ERR_RX1` to `ERR_RX6`; `RX_PACKETS` 0 and the counters of the compressors unchanged. The images equal the model: the sequence counts wrap from 16383 to 0 without the Data Loss Indicator, the skipped count gives none without the check; no other error register moved; cleared |
| TC-RS-HW-13 Lane removal | With the link idle, `LANE_CTRL.TX_EN` and `RX_EN` of lane 3 of `ofb0` cleared; one image per channel; both set again; one image per channel | `ML_STATUS` with lanes 0 to 2 sending and receiving, aligned, lane 3 not Active; the images equal the model; lane 3 Active again and `ML_STATUS` with all lanes; the images equal the model; `ML_MISALIGNED`, `DL_RETRIES` and the registers of lane 3 are recorded, no other error register moved; cleared |
| TC-RS-HW-17 End of run | Read every error register of every block (section 10) and every lane | Every error register reads 0 since the baseline of TC-RS-HW-01, `RXERR_COUNT` of every lane at most its baseline; the values, the lanes and the rate of TC-RS-HW-09 are recorded |

TC-RS-HW-10 ends with the values of the reset on channels 0 and 3. After a run that stopped in TC-RS-HW-10, program
the device again before the next run: the runtime registers and the values taken may still hold the values of an image
of its sequence, which TC-RS-HW-02 and TC-RS-HW-10 would report. The same holds after a stop in TC-RS-HW-11 (a register
of `ofb0` may hold a test value), TC-RS-HW-13 (lane 3 may be out of the link), TC-RS-HW-14 (an injected error may
wait in its buffer), TC-RS-HW-15 (window 0 may be restricted) and TC-RS-HW-16 (a table entry or `CONFIG` may hold a
test value).

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
| Test computer | The output of `--check-environment` |
| Test run | Command line (profile, seed) and the complete output of the script with its `RECORD` lines, for example redirected into a file |
| Records file | The JSON file of `--records`: the date and time of the run, the profile and seed, the result of every test case, the `RECORD` lines (the lanes, the baseline and the values of the error registers, the rate of TC-RS-HW-09, the decoding check) and per compressed image its test case, channel, dimensions, length, SHA-256 and the result of the decoding check; the bytes of an image only when it fails. It holds no host name and no path |

A failing test case is recorded with its output and the register values it reports.

## 10. Coverage of the cores

The tables list per core the requirements that the test cases exercise on the board and those they do not, with the
reason. The IDs are those of the specifications of each core, written without the group of their row (`IF-01` in the
row LN of OpenFibre is `LN-IF-01`). "Traffic" stands for every test case with RMAP commands or images. The system
uses the configurations of its Composer file: a requirement of a feature that the system does not use, or that it
uses at the values of the reset only, is listed as not exercised.

### Error registers

TC-RS-HW-01 takes the baseline and TC-RS-HW-17 checks these registers (`sw/refsys/errorstate.py`); a test case that
provokes errors checks them before it clears them:

| Block | Error registers | Recorded only |
| --- | --- | --- |
| `ofb0` | `DL_ERRORS`; `DL_RETRIES`, `DL_CRC16_COUNT`, `DL_CRC8_COUNT`, `DL_FRAME_COUNT`, `DL_SEQ_COUNT`; `VC_INPUT_OVERFLOW`, `VC_CREDIT_OVERFLOW`, `VC_FRAMING_ERROR`, `VC_RX_FRAMING_ERROR`; `ML_MISALIGNED`, `ML_ALIGN_TIMEOUTS`, `NI_RX_EMPTY_COUNT`; per lane `LANE_EVENTS`, `LANE_TIMEOUT_COUNT` and `LANE_STATUS.RXERR_COUNT` (a leaky counter: at most its baseline); `ECC_STATUS` and `ECC_COUNT` of channels 0 to 8 | `DL_ERRORS.BC_DISCARD` (broadcasts of the far end, which the system does not take), `VC_BW_OVER`, `VC_BW_UNDER`, `LANE_REASONS` |
| `rmap0` | `TGT_CNT_HDR`, `TGT_CNT_ERR1` to `TGT_CNT_ERR6`, `PKT_CNT.DISCARDED`, the error bits of `EVENTS`, `ECC_STATUS` and `ECC_COUNT` of channels 0 to 2 | `TGT_CNT_OK`, `TGT_CNT_OK2`, `PKT_CNT.USER`, `EVENTS.TGT_CMD` |
| `spp0` | `ERR_TX1` to `ERR_TX4`, `ERR_RX1` to `ERR_RX6`, the error bits of `EVENTS`, the overrun bits of `TABLE_STATUS`, `ECC_STATUS` and `ECC_COUNT` of monitor channels 0 to 7 | `LAST_ERROR`, `EVENTS.RX_LOSS` (TC-RS-HW-08 checks `RX_LOSS`) |
| `edac0` | `IRQ_STATUS`, the counters of channels 0 to 7 | |
| `ocp0` to `ocp3` | `ECC_CORRECTED`, `ECC_UNCORRECTABLE` | `IMAGES_ABORTED`, `SDU_ERRORS`, `SDUS_DISCARDED` (checked against the model in every test case with images) |

### OpenFibre (`ofb0`: 4 lanes, 2 virtual channels, LaneStart, broadcast port tied off, Irq open)

| Group | Exercised (test cases) | Not exercised (reason) |
| --- | --- | --- |
| LN | IF-01..06, INIT-01..07/10/11, TX-01..03, RX-01..04/06/07 (TC-RS-HW-01, traffic); INIT-03 restart (TC-RS-HW-12, 13) | INIT-08/09 (standby, loss of signal: no standby of the far end), TX-04, RX-05 (overflow), LB-01/02 (loopback, not used) |
| ML | IF, LM-08/11/14..17, DS-01..09/11, ENC-01..06, DEC-01..05, AL-01..08/10, CO-01..06 (traffic); LM-11..14 and AL-07..09: a lane leaves and rejoins (TC-RS-HW-13) | A lane that leaves during traffic (TC-RS-HW-13 takes it out between rounds of images); bypass, TxOnly and RxOnly lanes, hot-redundant lanes (LM-01/07/09/10/13, BP, DS-10/12/13); ENC-08 |
| DL | IF-01/03..05, VO-01/03..05, CR-01, MAC-01, TS-01..03/05, FA-01/03/04, SQ-01/03, ER-01/02/05/08/09, WI-01/02/04, SR, RE-01/02, VI-01/02/05/06, LR-01/02 (traffic); LR-01..05, VO-02, VI-04, ER-06, FA-05 (TC-RS-HW-12); RE-03, ER-03, SQ-02: a frame sent again after a double error (TC-RS-HW-14) | Broadcast (BO, BI-01, FA-02, WI-03: port tied off); quality of service QS-01..07 and CM at their values of the reset; TS-04/06 (no frame corruption by the far end); ST-01 |
| NI | IF-01/03, TX-01/02, RX-01 (traffic) | TX-03/04, RX-02..04, IF-02, SC-01 (no schedule set) |
| MG | IF, CF-01..03, PA-01..03, ST-01/02/04..07, VN-01, ED-01..03 (TC-RS-HW-01, 02, 11, 12, 14, 17) | ST-03 interrupt (Irq open: `IRQ_MASK` storage only), PR-01..03 PRBS, PL-01/02 loopback |
| PA | IF-01..04, SE-01/02, SY-01, CC-01, CK-01, RS-01..04, ST-01..03 (TC-RS-HW-01, traffic); RS-04 at a restart (TC-RS-HW-12, 13) | LB-01, PR-01, the fault paths RS-05/06; MD (requirements of the simulation model) |
| CORE | IF-01, CK-01/02, RS-01, CC-01, ML-01, PL-02/04..08, SY-01/04 (traffic); PF-01 at the rate of the far end (TC-RS-HW-09); SY-02 (TC-RS-HW-13); ED-01/02 (TC-RS-HW-14) | SY-03, FT-01, PL-01/03; CC-02 cannot occur (CoreClk = LaneClk); PF-01 at the limit of the link |
| Memories (`ECC_INJECT`) | Single errors on channels 0 to 3 and 6 to 8; double errors on channels 6 and 7 (TC-RS-HW-14) | Channels 4 and 5 (broadcast buffers: port tied off); double errors on channels 0 to 3 (a link reset or packets that end with an EEP, which the transport does not report) |

### OpenRMAP (`rmap0`: target only; Initiator_g, Passthrough_g and ExtAuth_g false; Irq open)

| Group | Exercised (test cases) | Not exercised (reason) |
| --- | --- | --- |
| TG-IF, TG-RX, TG-TX | IF-01..03, RX-01, RX-05, TX-01..04 (traffic); RX-02/03/06 discards, TX-02 Reply Address fields of 8 and 12 bytes (TC-RS-HW-15) | IF-04 (ExtAuth_g false); IF-05 (indication port open: seen through `TGT_LAST`); RX-04 and every case with an EEP (the transport sends packets with an EOP only) |
| TG-AU | AU-01..08 (TC-RS-HW-03, 15) | The cases with an EEP |
| TG-EX | EX-01/04 (traffic); EX-02/03/05/07/08 (TC-RS-HW-15); EX-06 double error (TC-RS-HW-14) | EX-06 with an error response of the memory: no address of spfi_compress gives one (the decoder has 8 ports, so every address reaches a register window and the map repeats every 0x80000 bytes; the register maps answer every access) |
| CO | IF-01, DM-01/05, MX-01, NO-01 (traffic); DM-04 a CPTP packet on the channel of RMAP (TC-RS-HW-15) | IF-02 (no passthrough), DM-03 (no initiator) |
| MG | IF-01/02, RF-01/03..05/07 (TC-RS-HW-02, 03, 04, 15); ED-01/02 (TC-RS-HW-14) | RF-06 interrupt (Irq open) |
| IN | | All: the system has no initiator |
| Memories (`ECC_INJECT`) | Single errors on channels 0 to 2; a double error on channel 0 (TC-RS-HW-14) | Double errors on channels 1 and 2 (a read reply that ends with an EEP) |

### OpenSPP (`spp0`: Octet String Service on every port, one destination, path defaults, Irq and Err open)

| Group | Exercised (test cases) | Not exercised (reason) |
| --- | --- | --- |
| RE | IF-01..03, RC-01, DM-01..03, EX-01 (traffic, TC-RS-HW-08); RC-02, DM-01 discards, DM-02 Sequence Flags (TC-RS-HW-16); FT-01 (TC-RS-HW-14) | The EEP code 0x10; a secondary header on input |
| EN | RX-01/02, TX-01/02, IF-02 (traffic); RX-03 discards of the CPTP header (TC-RS-HW-16) | The Target Logical Address check (`TLA_CHECK` 0); EEP |
| SE | IF-01/02/04, BF-01, AS-01/02/04 (traffic; the header fields in TC-RS-HW-06); MX-01 round robin (TC-RS-HW-16); AS-05 (TC-RS-HW-14) | The rejection at `MAX_LENGTH` (0x01: an output chunk would be lost); the Packet Service (IF-03, BF-03, AS-03); BF-04 (the user signals of the send ports are tied) |
| MG | TB-01/02, CN-01/02, CF-01..03, EV-01, FT-01/02 (TC-RS-HW-05, 14, 16) | Interrupt (Irq open) |
| CO, TB, PK | SY-01/02/04/08 and TB-FN-01: the counts wrap from 16383 to 0, the tables preset, round robin (TC-RS-HW-16) | Services and paths that the system does not configure |
| Memories (`ECC_INJECT`) | Single errors into the receive data buffer, descriptor and table and the send data buffer, descriptor and table; a double error into the receive descriptor (TC-RS-HW-14) | Double errors into the send data buffers (packets that end with an EEP), the send descriptors and tables (an output chunk lost) and the receive data buffer and table |

### OpenCompact (`ocp0` to `ocp3`: the four configurations of the system README)

| Group | Exercised (test cases) | Not exercised (reason) |
| --- | --- | --- |
| Predictor PR | IF-01/03, FR-01, LS-02/04, PD-03, QU-01, MP-01, TP-01/02, RC-01..03 (TC-RS-HW-06 to 10); RP-01 (M of every image on `ocp0`), RP-02 (log2 t_inc, vmin and vmax of every image) (TC-RS-HW-10) | D other than 16 and signed samples, P other than 3, wide column sums (LS-03), custom weights and exponent offsets, relative and band-dependent limits, other damping and offset, the stride order (TP-03, FR-02, RC-04): not in the four configurations |
| Coder EC | SA-01..03, HY-01..04, BA-01..03, IF-01, RC-01 (TC-RS-HW-06 to 10); RP-01 (Umax, gamma0, gamma\* and K of every image on `ocp0`, Umax, gamma0 and gamma\* on `ocp3`; TC-RS-HW-10) | Restricted codes, J other than 16, the accumulator table; RP-02 (r of every image: the block-adaptive `ocp2` has no runtime parameters) |
| Core CO | IF-01/02, HD-01/02/04..08, PK-01..03, FR-02..05/08/09, RC-01..03/05, RP-01/03 (TC-RS-HW-06, 08, 10) | IF-03/04 memory errors (no injection path in the system); FR-06 (Ev_ImageError open); FR-07 (periodic updating); RS-01 reset during an image; RC-07 and RP-02 (the MIB refuses values out of range instead); RC-06 resources; CF (elaboration) |
| Channel CH, input adapter IA | CH-IF-01..04, FN-01..03, RC-01, RP-01 (TC-RS-HW-10), the error scenarios of a lost SDU and of a lost last SDU (TC-RS-HW-08); IA-IF, FN-01..05, ER-01..03 | The other scenarios of the channel table (faulty SDUs, early and missing end marks); container sizes other than 2 bytes; a reset at any time |
| MIB MB | IF-02, ID-01, GN-01, CN-01/02, RC-01..05, RP-01..04 (TC-RS-HW-02, 07, 10) | Counter saturation, unused addresses |
| Memories PR-FT, EC-FT | The counters read 0 (TC-RS-HW-07, 17) | Correction on the board: the system has no path to inject errors into the compressors |

The decoding check adds a second check to the comparison with the model: the compressed images of the board decode
to the images sent, equal on the lossless channels and within the maximum error of every sample on `ocp3`.

### Open Logic FT (entities in the system and inside the cores)

| Entity | Where | Exercised (test cases) | Not exercised (reason) |
| --- | --- | --- | --- |
| `olo_ft_fifo_sync` | `rxf0` to `rxf3`, the buffers of the compressors, the retry buffer of `ofb0`, `rmap0`, the descriptors of `spp0` | Data path (traffic); correction and counting in `ofb0`, `rmap0`, `spp0` (TC-RS-HW-14) | Correction in `rxf0` to `rxf3` and in the compressors (no injection path) |
| `olo_ft_fifo_async` (SyncTmr_g) | The crossings of `ofb0` between the lane clock and `Clk_sys` | Data path, crossings; correction (TC-RS-HW-14) | TMR voting (not observable) |
| `olo_ft_fifo_packet` | The receive buffer of `ofb0`, the send and receive data of `spp0` | Data path; correction (TC-RS-HW-14) | |
| `olo_ft_ram_sdp_scrub` | The retry buffer RAM of `ofb0`, the APID tables of `spp0` | Data path; repair counted by the scrubber (TC-RS-HW-14) | |
| `olo_ft_ram_sdp` | The memories of the predictors and coders of the compressors | Data path, every image equals the model | Correction (no injection path) |
| `olo_ft_axi_master_full` | `rmap0` | Every RMAP command | An error response: no address of spfi_compress gives one (OpenRMAP, TG-EX) |
| `olo_ft_ecc_monitor` (inside the cores) | `ofb0` (9 channels), `rmap0` (3), `spp0` (8) | Counters and the channel select (TC-RS-HW-14, 17) | `DedSticky`, interrupts (open) |
| `olo_ft_ecc_monitor_axi` | `edac0` | `INFO`, `IRQ_ENA` (TC-RS-HW-02, 03, 15), the counters read 0 and clear (TC-RS-HW-01, 17) | Counting (no injection into the compressors and `rxf0` to `rxf3`); Irq (open) |
| `olo_base_cc_*`, `olo_base_reset_gen`, `olo_intf_sync` (SyncTmr_g) | `ofb0`, `pa0`, the top level | Used by every test case | TMR voting |
