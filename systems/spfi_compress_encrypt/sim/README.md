# spfi_compress_encrypt: System Testbench

The system testbench runs the [hardware test](../targets/vck190/docs/hardware_test.md) of spfi_compress_encrypt in
simulation: the same test cases, with the same requests and the same pass criteria, against the generated top level
of the system instead of the board. Every compressed image that comes back must equal the image of the reference model
of OpenCompact for the configuration of its channel, byte for byte.

## Structure

```text
+------------------------- spfi_compress_encrypt_tb -------------------------+
|                                                                            |
|  i_dut: spfi_compress_encrypt (hdl/)  i_far: refsys_tb.rs_far_end          |
|  +------------------------+         +--------------+    +---------------+  |
|  | ofb0 - pa0 (GTY)       |<------->| i_far_pa     |----| i_far_core    |  |
|  | rmap0, spp0, rxf0..3,  |  lanes  | ofb_pa_gty   |    | ofb_core      |  |
|  | ocp0..3, frm0..3,      |         +--------------+    +-------+-------+  |
|  | ocr0..3                |                               VC 0, VC 1, MIB  |
|  +------------------------+                                                |
|  sequencer:  stimulus.txt -> virtual channels and MIB of the far end       |
|  receivers:  virtual channels of the far end -> rx_vc<n>.txt, events.txt   |
|  RefClk 156.25 MHz and Clk_sys 200 MHz feed both ends                      |
+----------------------------------------------------------------------------+
```

The bench is that of spfi_encrypt with the top level of this system: the far end, the stimuli, the records and the
runners are those of all system testbenches ([sim/](../../../sim/README.md)). The simulated design is the design of
the device image: the compressors have the image size and the configurations of the Composer file (64 x 64 samples in
48 bands of 16 bits, README section "Lab configurations"). As in spfi_encrypt, the bench runs `Clk_sys` at 200 MHz by
default and at the 150 MHz of the hardware builds with `--sysclk-mhz 150`; the frequency sets only the clock of the
memory scrubbers, which are off.

## Profiles and test cases

The hardware test and the simulation share their test cases and differ in size only (`PROFILES` of
`sw/hardware_test.py`):

| Profile | Images per channel in TC-RS-HW-06 | Lost SDUs in TC-RS-HW-08 | Images per channel in TC-RS-HW-09 | Images in TC-RS-HW-10 | Use |
| --- | --- | --- | --- | --- | --- |
| `lab` | 5 (scene, stripes, noise, flat bands with steps, flat bands with spikes) | cases (a) and (b) on every channel, five images per channel | 8 | 21 on channels 0 and 3 (the sequence of OpenCompact, then the values of the generics), 1 on channels 1 and 2 | Hardware test |
| `sim` | 1 (scene) | case (a) on channels 0 and 2 (two images), case (b) on channels 1 and 3 (three images) | 2 | 3 on channels 0 and 3, the bounds: 2 x 1 x 1, 64 x 1 x 48 and 2 x 64 x 1 on channel 0; 1 x 1 x 1, 64 x 1 x 48 with the error limit value 1023 and 1 x 64 x 1 on channel 3; the runtime parameters at their high bounds, their low bounds and at random | System simulation |

Every error path of the channel (the abort after the Data Loss Indicator, the image without its end mark, the
discarded SDUs and the counters) is thus simulated at the configuration of the device image, two channels per case.
TC-RS-HW-09 measures the sustained rate of the test equipment and runs on hardware only; in simulation, TC-RS-HW-06
shows the data path with the four compressors in parallel. The procedure takes the expected compressed images from
the reference model of OpenCompact, for an image that ended with an error the compressed image of the samples that
the channel took (`aborted_image` of `sw/refsys/ocp.py`). With a version of OpenCompact whose model does not give
that image, the procedure receives after the chunks that it knows until the channel is quiet (operation `quiet` of
the far end, 100 us) and checks the header and the length of the aborted image (`aborted_problems`).

## Running

```shell
python systems/spfi_compress_encrypt/sim/run_ghdl.py TC-RS-HW-06      # TC-RS-HW-06 and the test cases it builds on
python systems/spfi_compress_encrypt/sim/run_ghdl.py                  # all test cases in one simulation
python systems/spfi_compress_encrypt/sim/run_xsim.py                  # xsim with the AMD transceiver model
```

The runners and their results are those of spfi_encrypt ([its sim/README.md](../../spfi_encrypt/sim/README.md),
section "Running"), with the directories `sim_out/ghdl/spfi_compress_encrypt/` and
`vivado_out/xsim/spfi_compress_encrypt/`. Before the simulation, `vectors.py write` computes the compressed image of
every test image with the reference model of OpenCompact, a few seconds per image.

`python -m pytest systems/spfi_compress_encrypt/sim` checks the stimuli and the checks without a simulator, on the
system of the Composer file with smaller images: the records of the model pass, the procedure never waits for a
quiet channel, and a chunk that no request awaits fails. With a version of OpenCompact whose model does not give the
aborted images, the tests also check that the procedure receives until the channel is quiet only in TC-RS-HW-08, and
that a chunk too many in the window and a channel that does not become quiet fail.

## Results

### GHDL

GHDL 6.0 with the behavioural model of the adapter at both ends, profile `sim`, seed 1, system clock 150 MHz
(`--sysclk-mhz 150`), runs of 2026-10-28 and 29 with the versions of the cores that the system file of v0.2.0 pins
(OpenFibre v0.1.4, OpenRMAP v0.1.1, OpenSPP v0.1.0, OpenCrypto v0.1.0, OpenCompact v0.3.0, Open Logic FT
4.7.0-ft.11). The test cases run in two simulations; each starts with the reset and the link initialisation:

| Simulation | Test cases, in their order | Simulated time | Wall-clock time |
| --- | --- | --- | --- |
| 1 | TC-RS-HW-01 to 06, 10 | 2584.7 us | 5.4 h |
| 2 | TC-RS-HW-01 to 05, 10, 18 | 2154.5 us | 2.0 h |

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 64.9 us | | |
| Link initialised (all lanes Active at both ends) | 91.8 us | | |
| TC-RS-HW-01 Link | 15.1 us | 0.6 min | Pass |
| TC-RS-HW-02 Identification (with the status registers of the four compressors) | 90.5 us | 3.7 min | Pass |
| TC-RS-HW-03 RMAP | 10.7 us | 0.4 min | Pass |
| TC-RS-HW-04 Commands of several words | 26.8 us | 1.1 min | Pass |
| TC-RS-HW-05 Configuration | 149.6 us | 6.4 min | Pass |
| TC-RS-HW-06 Compression and encryption (one image of 64 x 64 x 48 samples per channel, 228 chunks) | 1471.4 us | 4.5 h | Pass |
| TC-RS-HW-10 Runtime configuration (three images on each of channels 0 and 3) | 728.7 us | 40.4 min | Pass |
| TC-RS-HW-18 Repeated run (TC-RS-HW-01 to 05 and 10 again) | 1041.4 us | 60.3 min | Pass |

TC-RS-HW-01 to 05 and 10 are listed from simulation 1; in simulation 2 they pass as well.

- The four compressed images of TC-RS-HW-06, one per configuration (c01, c25, c27, c26), equal the images of the
  reference model of OpenCompact byte for byte, header included, and every chunk authenticates and decrypts with the
  key of its channel.
- The four compressors work in parallel: at 150 MHz one compressor takes 1.31 ms for its image at one sample per
  cycle, and TC-RS-HW-06 takes 1.47 ms for one image per channel.
- In TC-RS-HW-10 every chunk authenticates and decrypts with the key of its channel. `ocp0` and `ocp3` hold the
  values of the reset before the first COMMIT, and `ocp1` and `ocp2` ignore the runtime registers and a COMMIT.
  Every compressed image equals the image of the reference model for the values and the runtime parameters committed
  before it, header included, and after every image `IMAGE_X` to `LIMITS` show the values that the compressor took,
  with no configuration pending. Every COMMIT with a value out of range is refused with `CFG_ERROR` while the
  configuration stays pending.
- In simulation 2 TC-RS-HW-18 runs TC-RS-HW-01 to 05 and 10 a second time, without a reset. TC-RS-HW-05 finds the
  Packet Sequence Counts that the first pass left in the send APID table, 4 on APID 0 and 3 on APID 3, and the
  runtime registers of `ocp0` and `ocp3` with the values of their last COMMIT; it records both and commits the values
  of the generics. TC-RS-HW-02 records the values of the last image of the first pass in `IMAGE_X` to `LIMITS`. Every
  test case passes again.
- Recorded values (no pass criteria): after the link-up every lane counts 0 receiver resets and no initialisation
  timeout and has CDR lock.

TC-RS-HW-08 at the image size of the lab is the run of 2026-10-18, with OpenFibre, OpenRMAP, OpenSPP, OpenCrypto and
OpenCompact v0.1.0 and Open Logic FT 4.7.0-ft.8, at the system clock of the bench before `--sysclk-mhz`, 200 MHz.
spfi_compress, which shares the path of the channels into the compressors, ran its TC-RS-HW-08 with the cores of
this release ([its sim/README.md](../../spfi_compress/sim/README.md)). The simulation starts with the reset, the link
initialisation and TC-RS-HW-05:

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Link initialised (all lanes Active at both ends) | 92.6 us | | |
| TC-RS-HW-05 Configuration | 114.3 us | 6.0 min | Pass |
| TC-RS-HW-08 Lost data (case (a) on channels 0 and 2, case (b) on channels 1 and 3, 392 chunks) | 3644.1 us | 9.3 h | Pass |

In TC-RS-HW-08 every image that ended with an error equals the compressed image of the samples that its channel took
(`aborted_image` of `sw/refsys/ocp.py`, with the model of OpenCompact): in case (a) the samples of Space Packets 1 to
24 and 26, in case (b) those of the image and the first 144 samples of the next one. The other images equal the
model, and the counters of every channel and `RX_LOSS` of `spp0` match the case of the channel.

### xsim

xsim of Vivado 2025.2 with the transceiver model at both ends, profile `sim`, seed 1, reference clock 100 MHz and
system clock 150 MHz (`--refclk-mhz 100 --sysclk-mhz 150`, the clocks of the default device image), TC-RS-HW-01 to 05
in one simulation, run of 2026-10-30 with the versions of the cores that the system file of v0.2.0 pins:

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 66.6 us | | |
| Link initialised (all lanes Active at both ends) | 92.5 us | | |
| TC-RS-HW-01 Link | 16.4 us | 1.9 min | Pass |
| TC-RS-HW-02 Identification (with the status registers of the four compressors) | 95.2 us | 11.0 min | Pass |
| TC-RS-HW-03 RMAP | 11.2 us | 1.3 min | Pass |
| TC-RS-HW-04 Commands of several words | 28.2 us | 3.3 min | Pass |
| TC-RS-HW-05 Configuration | 156.9 us | 18.2 min | Pass |
| Total | 400.2 us | 42.2 min | 5 of 5 |

With the reference clock of 100 MHz the transmitters of the transceiver model are ready 1.7 us later than those of
the behavioural model at 156.25 MHz, and the link is initialised 0.7 us later. The test cases take about 5 % more
simulated time than in GHDL: the datapath latency of the behavioural model is a little shorter than that of the
transceiver model (model description of OpenFibre, section "Limits"). Recorded values: after the link-up every lane
counts 0 receiver resets and no initialisation timeout and has CDR lock. The compression test cases at the image size
of the lab run in GHDL (above).

## Limits

- The far end is an OpenFibre port like `ofb0`; the hardware test against the STAR-Ultra is the check of
  interoperability (spfi_encrypt, sim/README.md, section "Limits").
- The compressors are compared with the reference model of OpenCompact, which OpenCompact verifies against the CNES
  reference software; the simulation does not decode the compressed images.
