# spfi_compress: System Testbench

The system testbench runs the [hardware test](../targets/vck190/docs/hardware_test.md) of spfi_compress in
simulation: the same test cases, with the same requests and the same pass criteria, against the generated top level
of the system instead of the board. The Space Packets of every channel must be the chunks of its compressed images,
and every compressed image must equal the image of the reference model of OpenCompact for the configuration of its
channel, byte for byte.

## Structure

```text
+------------------------------ spfi_compress_tb -----------------------------+
|                                                                             |
|  i_dut: spfi_compress (hdl/)          i_far: refsys_tb.rs_far_end           |
|  +------------------------+         +--------------+    +---------------+   |
|  | ofb0 - pa0 (GTY)       |<------->| i_far_pa     |----| i_far_core    |   |
|  | rmap0, spp0, rxf0..3,  |  lanes  | ofb_pa_gty   |    | ofb_core      |   |
|  | ocp0..3, chunk0..3     |         +--------------+    +-------+-------+   |
|  +------------------------+                                VC 0, VC 1, MIB  |
|  sequencer:  stimulus.txt -> virtual channels and MIB of the far end        |
|  receivers:  virtual channels of the far end -> rx_vc<n>.txt, events.txt    |
|  RefClk 156.25 MHz and Clk_sys 200 MHz feed both ends                       |
+-----------------------------------------------------------------------------+
```

The bench is that of spfi_compress_encrypt with the top level of this system: the far end, the stimuli, the records
and the runners are those of all system testbenches ([sim/](../../../sim/README.md)). The simulated design is the
design of the device image: the compressors have the image size and the configurations of the Composer file (64 x 64
samples in 48 bands of 16 bits, README section "Lab configurations"), and the chunkers cut the compressed images into
chunks of 4096 bytes. As in spfi_encrypt, the bench runs `Clk_sys` at 200 MHz by default and at the 150 MHz of the
hardware builds with `--sysclk-mhz 150`; the frequency sets only the clock of the memory scrubbers, which are off.

## Profiles and test cases

The hardware test and the simulation share their test cases and differ in size only (`PROFILES` of
`sw/hardware_test.py`), as in spfi_compress_encrypt:

| Profile | Images per channel in TC-RS-HW-06 | Lost SDUs in TC-RS-HW-08 | Images per channel in TC-RS-HW-09 | Images in TC-RS-HW-10 | Use |
| --- | --- | --- | --- | --- | --- |
| `lab` | 5 (scene, stripes, noise, flat bands with steps, flat bands with spikes) | cases (a) and (b) on every channel, five images per channel | 8 | 21 on channels 0 and 3 (the sequence of OpenCompact, then the values of the generics), 1 on channels 1 and 2 | Hardware test |
| `sim` | 1 (scene) | case (a) on channels 0 and 2 (two images), case (b) on channels 1 and 3 (three images) | 2 | 3 on channels 0 and 3, the bounds: 2 x 1 x 1, 64 x 1 x 48 and 2 x 64 x 1 on channel 0; 1 x 1 x 1, 64 x 1 x 48 with the error limit value 1023 and 1 x 64 x 1 on channel 3; the runtime parameters at their high bounds, their low bounds and at random | System simulation |

Every error path of the channel (the abort after the Data Loss Indicator, the image without its end mark, the
discarded SDUs and the counters) is thus simulated at the configuration of the device image, two channels per case.
TC-RS-HW-09 measures the sustained rate of the test equipment and runs on hardware only; in simulation, TC-RS-HW-06
shows the data path with the four compressors in parallel. The procedure takes every expected compressed image from
the reference model of OpenCompact, for an image that ended with an error the compressed image of the samples that
the channel took (`aborted_image` of `sw/refsys/ocp.py`), and so knows every Space Packet in advance: it never waits
for a quiet channel.

## Running

```shell
python systems/spfi_compress/sim/run_ghdl.py TC-RS-HW-06      # TC-RS-HW-06 and the test cases it builds on
python systems/spfi_compress/sim/run_ghdl.py                  # all test cases in one simulation
python systems/spfi_compress/sim/run_xsim.py                  # xsim with the AMD transceiver model
```

The runners and their results are those of spfi_encrypt ([its sim/README.md](../../spfi_encrypt/sim/README.md),
section "Running"), with the directories `sim_out/ghdl/spfi_compress/` and `vivado_out/xsim/spfi_compress/`. Before
the simulation, `vectors.py write` computes the compressed image of every test image with the reference model of
OpenCompact, a few seconds per image.

`python -m pytest systems/spfi_compress/sim` checks the stimuli and the checks without a simulator, on the system of
the Composer file with smaller images and chunks of 256 bytes, so that every compressed image takes several Space
Packets: the records of the model pass, the procedure never waits for a quiet channel, and a Space Packet that no
request awaits and a lost Space Packet fail.

## Results

### GHDL

GHDL 6.0 with the behavioural model of the adapter at both ends, profile `sim`, seed 1, runs of 2026-10-20 with the
versions of the cores that the system file pins. TC-RS-HW-06 and TC-RS-HW-08 run in two simulations; each starts with
the reset, the link initialisation and TC-RS-HW-05.

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 64.9 us | | |
| Link initialised (all lanes Active at both ends) | 92.6 us | | |
| TC-RS-HW-05 Configuration | 62.3 us | 2.6 to 3.1 min | Pass |
| TC-RS-HW-06 Compression (one image of 64 x 64 x 48 samples per channel, 228 Space Packets) | 1276.0 us | 4.2 h | Pass |
| TC-RS-HW-08 Lost data (case (a) on channels 0 and 2, case (b) on channels 1 and 3, 392 Space Packets) | 3576.6 us | 7.5 h | Pass |

- The four compressed images of TC-RS-HW-06, one per configuration (c01, c25, c27, c26), equal the images of the
  reference model of OpenCompact byte for byte, header included: 260,208, 248,520, 252,152 and 165,424 bytes in 64,
  61, 62 and 41 Space Packets. Every Space Packet carries the APID of its channel and the next Packet Sequence Count
  of that APID, and every Space Packet of an image but the last holds 4096 bytes.
- The four compressors work in parallel: TC-RS-HW-06 takes 1.28 ms for one image per channel, where one compressor
  takes 0.98 ms for its image at one sample per cycle. The chunkers in place of the framers and AES-GCM cores leave
  the time of the test case unchanged (1276.9 us in spfi_compress_encrypt).
- In TC-RS-HW-08 every image that ended with an error equals the compressed image of the samples that its channel
  took (`aborted_image` of `sw/refsys/ocp.py`, with the model of OpenCompact): in case (a) the samples of Space
  Packets 1 to 24 and 26 (102,325 of 196,608 samples; 138,600 and 140,724 bytes in 34 and 35 Space Packets on
  channels 0 and 2), in case (b) those of the image and the first 144 samples of the next one. The other images
  equal the model, the Packet Sequence Counts of every output APID continue without a gap, and the counters of every
  channel and `RX_LOSS` of `spp0` match the case of the channel.

Run of 2026-10-26 at the system clock of the lab, 150 MHz (`--sysclk-mhz 150`), with the runtime configuration of
`ocp0` and `ocp3`: TC-RS-HW-01 to 06 and 10 in one simulation. The runs above use the system clock of the bench before
`--sysclk-mhz`, 200 MHz.

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Link initialised (all lanes Active at both ends) | 91.8 us | | |
| TC-RS-HW-01 Link | 15.1 us | 0.5 min | Pass |
| TC-RS-HW-02 Identification | 84.5 us | 2.7 min | Pass |
| TC-RS-HW-03 RMAP | 10.7 us | 0.3 min | Pass |
| TC-RS-HW-04 Commands of several words | 31.3 us | 1.0 min | Pass |
| TC-RS-HW-05 Configuration | 70.0 us | 2.2 min | Pass |
| TC-RS-HW-06 Compression (one image of 64 x 64 x 48 samples per channel, 228 Space Packets) | 1470.0 us | 3.6 h | Pass |
| TC-RS-HW-10 Runtime configuration (three images on each of channels 0 and 3, one image on each of channels 1 and 2) | 345.9 us | 12.8 min | Pass |

- At 150 MHz one compressor takes 1.31 ms for its image at one sample per cycle; TC-RS-HW-06 takes 1.47 ms for one
  image per channel.
- In TC-RS-HW-10 every compressed image equals the image of the reference model of OpenCompact for the values
  committed before it, header included. Every COMMIT with a value out of range is refused with `CFG_ERROR` while the
  configuration stays pending, `ocp1` and `ocp2` ignore the runtime registers, and after the last image `ocp0` and
  `ocp3` hold the values of the reset.

### xsim

xsim of Vivado 2025.2 with the transceiver model at both ends, profile `sim`, seed 1, TC-RS-HW-01 to 05 in one
simulation, run of 2026-10-20 with the versions of the cores that the system file pins:

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 64.9 us | | |
| Link initialised (all lanes Active at both ends) | 92.6 us | | |
| TC-RS-HW-01 Link | 8.6 us | 1.2 min | Pass |
| TC-RS-HW-02 Identification (with the configuration registers of the four compressors) | 74.2 us | 12.0 min | Pass |
| TC-RS-HW-03 RMAP | 10.1 us | 1.6 min | Pass |
| TC-RS-HW-04 Commands of several words (with the test window of `rmap0`) | 29.4 us | 4.6 min | Pass |
| TC-RS-HW-05 Configuration | 65.7 us | 10.5 min | Pass |
| Total | 280.5 us | 38.7 min | 5 of 5 |

The transceiver model gives the same link initialisation as the behavioural model. The compression test cases at
the image size of the lab run in GHDL (above).

## Limits

- The far end is an OpenFibre port like `ofb0`; the hardware test against the STAR-Ultra is the check of
  interoperability (spfi_encrypt, sim/README.md, section "Limits").
- The compressors are compared with the reference model of OpenCompact, which OpenCompact verifies against the CNES
  reference software; the simulation does not decode the compressed images.
