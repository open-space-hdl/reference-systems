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
48 bands of 16 bits, README section "Lab configurations").

## Profiles and test cases

The hardware test and the simulation share their test cases and differ in size only (`PROFILES` of
`sw/hardware_test.py`):

| Profile | Images per channel in TC-RS-HW-06 | Lost SDUs in TC-RS-HW-08 | Images per channel in TC-RS-HW-09 | Use |
| --- | --- | --- | --- | --- |
| `lab` | 5 (scene, stripes, noise, flat bands with steps, flat bands with spikes) | cases (a) and (b) on every channel, five images per channel | 8 | Hardware test |
| `sim` | 1 (scene) | case (a) on channels 0 and 2 (two images), case (b) on channels 1 and 3 (three images) | 2 | System simulation |

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

GHDL 6.0 with the behavioural model of the adapter at both ends, profile `sim`, seed 1, runs of 2026-10-18 with
OpenFibre, OpenRMAP, OpenSPP, OpenCrypto and OpenCompact v0.1.0 and Open Logic FT 4.7.0-ft.8. OpenCompact v0.1.1,
which the system file pins, changes only the types of the array fields of `ocp_predictor`, with the same behaviour in
every simulator; the stimuli and the expected images are the same. TC-RS-HW-06 and TC-RS-HW-08 run in two
simulations; each starts with the reset, the link initialisation and TC-RS-HW-05.

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of both ends ready | 64.9 us | | |
| Link initialised (all lanes Active at both ends) | 92.6 us | | |
| TC-RS-HW-05 Configuration | 114.3 us | 6.0 min | Pass |
| TC-RS-HW-06 Compression and encryption (one image of 64 x 64 x 48 samples per channel, 228 chunks) | 1276.9 us | 4.5 h | Pass |
| TC-RS-HW-08 Lost data (case (a) on channels 0 and 2, case (b) on channels 1 and 3, 392 chunks) | 3644.1 us | 9.3 h | Pass |

- The four compressed images of TC-RS-HW-06, one per configuration (c01, c25, c27, c26), equal the images of the
  reference model of OpenCompact byte for byte, header included, and every chunk authenticates and decrypts with the
  key of its channel.
- The four compressors work in parallel: TC-RS-HW-06 takes 1.28 ms for one image per channel, where one compressor
  takes 0.98 ms for its image at one sample per cycle.
- In TC-RS-HW-08 every image that ended with an error equals the compressed image of the samples that its channel
  took (`aborted_image` of `sw/refsys/ocp.py`, with the model of OpenCompact): in case (a) the samples of Space
  Packets 1 to 24 and 26, in case (b) those of the image and the first 144 samples of the next one. The other images
  equal the model, and the counters of every channel and `RX_LOSS` of `spp0` match the case of the channel.

### xsim

xsim of Vivado 2025.2 with the transceiver model at both ends, profile `sim`, seed 1, TC-RS-HW-01 to 05 in one
simulation, run of 2026-10-19 with the versions of the cores that the system file pins (OpenCompact v0.1.1):

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 64.9 us | | |
| Link initialised (all lanes Active at both ends) | 92.6 us | | |
| TC-RS-HW-01 Link | 8.6 us | 1.6 min | Pass |
| TC-RS-HW-02 Identification (with the status registers of the four compressors) | 79.9 us | 14.5 min | Pass |
| TC-RS-HW-03 RMAP | 10.1 us | 1.6 min | Pass |
| TC-RS-HW-04 Commands of several words | 25.1 us | 3.9 min | Pass |
| TC-RS-HW-05 Configuration | 120.3 us | 18.4 min | Pass |
| Total | 336.5 us | 50.3 min | 5 of 5 |

The transceiver model gives the same link initialisation as the behavioural model. The compression test cases at
the image size of the lab run in GHDL (above).

## Limits

- The far end is an OpenFibre port like `ofb0`; the hardware test against the STAR-Ultra is the check of
  interoperability (spfi_encrypt, sim/README.md, section "Limits").
- The compressors are compared with the reference model of OpenCompact, which OpenCompact verifies against the CNES
  reference software; the simulation does not decode the compressed images.
