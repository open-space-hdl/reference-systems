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

TC-RS-HW-12, 13, 14 and 16 send a round of images after the event they provoke: in the `lab` profile one image of
the generics per channel, in the `sim` profile one image of at most 64 x 16 x 8 samples on each of channels 0 and 3,
committed through the runtime configuration before it and followed by a COMMIT of the values of the generics.
TC-RS-HW-11, 15 and 17 send no images. TC-RS-HW-18 runs the test cases before it again, with their images.

Every error path of the channel (the abort after the Data Loss Indicator, the image without its end mark, the
discarded SDUs and the counters) is thus simulated at the configuration of the device image, two channels per case.
TC-RS-HW-09 measures the sustained rate of the test equipment on hardware; in simulation it checks the images, the
Space Packets and the counters of two images per channel sent to all channels at once, and the rate it records is that
of the simulation host. TC-RS-HW-06 shows the data path with the four compressors in parallel. The procedure takes
every expected compressed image from the reference model of OpenCompact, for an image that ended with an error the
compressed image of the samples that the channel took (`aborted_image` of `sw/refsys/ocp.py`), and so knows every
Space Packet in advance: it never waits for a quiet channel of Space Packets.

The operator step of TC-RS-HW-12, the link reset at the port of the test equipment, is a step of the far end of the
bench: it writes `DL_CTRL.LINK_RESET` (with `DATA_SCRAMBLED` kept) into its own register map, waits 100 us on the
virtual channel of RMAP, where no packet may arrive, and waits until its link is initialised again (the operations
`mibw`, `quiet` and `mibp` of `stimulus.txt`, [sim/README.md](../../../sim/README.md)).

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
Packets: the records of the model pass, the procedure never waits for a quiet channel of Space Packets, and a Space
Packet that no request awaits and a lost Space Packet fail.

## Results

### GHDL

GHDL 6.0 with the behavioural model of the adapter at both ends, profile `sim`, seed 1, system clock 150 MHz
(`--sysclk-mhz 150`), runs of 2026-10-28 and 29 with the versions of the cores that the system file of v0.2.0 pins
(OpenFibre v0.1.4, OpenRMAP v0.1.1, OpenSPP v0.1.0, OpenCompact v0.3.0, Open Logic FT 4.7.0-ft.11). The test cases
run in five simulations; each starts with the reset and the link initialisation and runs the test cases that its own
build on first:

| Simulation | Test cases, in their order | Simulated time | Wall-clock time |
| --- | --- | --- | --- |
| 1 | TC-RS-HW-01 to 07 | 2287.7 us | 4.3 h |
| 2 | TC-RS-HW-01, 05, 10, 11, 12, 14, 15, 16, 13, 17 | 4277.5 us | 3.4 h |
| 3 | TC-RS-HW-05, 08 | 4471.9 us | 8.2 h |
| 4 | TC-RS-HW-05, 09 | 3189.8 us | 7.7 h |
| 5 | TC-RS-HW-01 to 05, 10, 18 | 2764.1 us | 2.1 h |

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 64.9 us | | |
| Link initialised (all lanes Active at both ends) | 91.8 us | | |
| TC-RS-HW-01 Link (with the baseline of the error registers) | 278.9 us | 10.1 min | Pass |
| TC-RS-HW-02 Identification | 104.1 us | 3.7 min | Pass |
| TC-RS-HW-03 RMAP | 167.7 us | 6.0 min | Pass |
| TC-RS-HW-04 Commands of several words | 31.3 us | 1.1 min | Pass |
| TC-RS-HW-05 Configuration | 90.9 us | 3.2 min | Pass |
| TC-RS-HW-06 Compression (one image of 64 x 64 x 48 samples per channel) | 1470.2 us | 3.8 h | Pass |
| TC-RS-HW-07 Counters | 52.9 us | 1.9 min | Pass |
| TC-RS-HW-08 Lost data (case (a) on channels 0 and 2, case (b) on channels 1 and 3) | 4289.2 us | 8.1 h | Pass |
| TC-RS-HW-09 Sustained rate (two images per channel, all channels at once) | 3007.1 us | 7.6 h | Pass |
| TC-RS-HW-10 Runtime configuration (three images on each of channels 0 and 3) | 653.2 us | 32.6 min | Pass |
| TC-RS-HW-11 SpaceFibre port | 84.6 us | 3.8 min | Pass |
| TC-RS-HW-12 Far-end link reset | 488.7 us | 22.6 min | Pass |
| TC-RS-HW-13 Lane removal | 642.3 us | 41.9 min | Pass |
| TC-RS-HW-14 Memory errors | 985.2 us | 44.8 min | Pass |
| TC-RS-HW-15 RMAP errors | 292.8 us | 9.3 min | Pass |
| TC-RS-HW-16 Space Packet errors | 553.5 us | 25.4 min | Pass |
| TC-RS-HW-17 End of run | 115.7 us | 5.9 min | Pass |
| TC-RS-HW-18 Repeated run (TC-RS-HW-01 to 05 and 10 again) | 1346.2 us | 62.3 min | Pass |

TC-RS-HW-01 and 05 are listed from simulation 1, TC-RS-HW-10 from simulation 2; in the other simulations they pass as
well.

- The four compressed images of TC-RS-HW-06, one per configuration (c01, c25, c27, c26), equal the images of the
  reference model of OpenCompact byte for byte, header included. Every Space Packet carries the APID of its channel
  and the next Packet Sequence Count of that APID, and every Space Packet of an image but the last holds 4096 bytes.
- The four compressors work in parallel: at 150 MHz one compressor takes 1.31 ms for its image at one sample per
  cycle, and TC-RS-HW-06 takes 1.47 ms for one image per channel.
- The decoder of the reference model decodes the 4 compressed images of TC-RS-HW-06 and the 6 complete compressed
  images of TC-RS-HW-10 to the images sent (lossless: equal; near-lossless: within the maximum error).
- In TC-RS-HW-08 every image that ended with an error equals the compressed image of the samples that its channel
  took (`aborted_image` of `sw/refsys/ocp.py`, with the model of OpenCompact). The other images equal the model, the
  Packet Sequence Counts of every output APID continue without a gap, and the counters of every channel and
  `RX_LOSS` of `spp0` match the case of the channel.
- In TC-RS-HW-09 the 8 images (3,145,728 bytes of samples, 1,869,076 bytes compressed) equal the model, with no
  loss. The rate that the procedure records in simulation is that of the simulation host; the rate of the system is
  measured on hardware only.
- In TC-RS-HW-10 `ocp0` and `ocp3` hold the values of the reset before the first COMMIT, and `ocp1` and `ocp2`
  ignore the runtime registers and a COMMIT. Every compressed image equals the image of the reference model for the
  values and the runtime parameters committed before it, header included, and after every image `IMAGE_X` to
  `LIMITS` show the values that the compressor took, with no configuration pending. Every COMMIT with a value out of
  range is refused with `CFG_ERROR` while the configuration stays pending.
- TC-RS-HW-12, 13, 14 and 16 run after TC-RS-HW-10 and send their images with the values of the generics committed;
  every reply matches the system model. TC-RS-HW-15 reads at 0x00080000, where the register map repeats, the
  identification of `spp0` with status 0 (section 10 of the
  [hardware test procedure](../targets/vck190/docs/hardware_test.md)).
- In simulation 5 TC-RS-HW-18 runs TC-RS-HW-01 to 05 and 10 a second time, without a reset. TC-RS-HW-05 finds the
  Packet Sequence Counts that the first pass left in the send APID table, 4 on APID 0 and 3 on APID 3, and the
  runtime registers of `ocp0` and `ocp3` with the values of their last COMMIT; it records both and commits the values
  of the generics. TC-RS-HW-02 records the values of the last image of the first pass in `IMAGE_X` to `LIMITS`. Every
  test case passes again, the Space Packets of the second pass continue the counts, and the decoder decodes the 6
  complete images of each pass.
- Recorded values (no pass criteria): after the link-up every lane counts 0 receiver resets and has CDR lock; after
  the link reset of TC-RS-HW-12 every lane counts 3 receiver resets and no initialisation timeout; after the rejoin of
  TC-RS-HW-13 lane 3 counts 1 receiver reset and the other lanes none; at the end of simulation 2 every lane counts 0
  receiver resets and has CDR lock.

### xsim

xsim of Vivado 2025.2 with the transceiver model at both ends, profile `sim`, seed 1, reference clock 100 MHz and
system clock 150 MHz (`--refclk-mhz 100 --sysclk-mhz 150`, the clocks of the default device image), TC-RS-HW-01 to 05
in one simulation, run of 2026-10-30 with the versions of the cores that the system file of v0.2.0 pins:

| Step | Simulated time | Wall-clock time | Result |
| --- | --- | --- | --- |
| Transmitters of the far end ready | 66.6 us | | |
| Link initialised (all lanes Active at both ends) | 92.5 us | | |
| TC-RS-HW-01 Link (with the baseline of the error registers) | 293.0 us | 33.7 min | Pass |
| TC-RS-HW-02 Identification | 109.4 us | 12.5 min | Pass |
| TC-RS-HW-03 RMAP | 175.9 us | 20.2 min | Pass |
| TC-RS-HW-04 Commands of several words | 32.9 us | 3.8 min | Pass |
| TC-RS-HW-05 Configuration | 95.3 us | 11.0 min | Pass |
| Total | 799.1 us | 87.7 min | 5 of 5 |

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
  reference software; the check of the simulation decodes the complete compressed images of TC-RS-HW-06 and
  TC-RS-HW-10 with the decoder of the reference model.

## Known issue: the bandwidth flags of OpenFibre v0.1.4 in simulation

In simulation, `VC_BW_OVER` and `VC_BW_UNDER` of `ofb0` (register map of OpenFibre, 0x048 and 0x04C) hold 'U' after
the reset. The register map sets these sticky flags from the status of the core, which crosses into the management
clock domain only after the lane clock of the simulated transceiver has started (at about 65 us). A read of the flags
before they are cleared puts 'U' into the RMAP reply: the CRCs of the frame at both ends of the link differ, the frame
is sent again without end and the reply never arrives, while the link stays up.

In the `sim` profile the hardware test therefore writes both registers with ones (write-one-to-clear) in TC-RS-HW-01,
before the baseline of the error registers reads them (`clear_bw_flags` of the profile, `BW_FLAGS` of
`sw/hardware_test.py`). The `lab` profile does not write them: on hardware the flags start at 0, and the baseline
records their values as they are. The hardware is not affected. OpenFibre corrects this in a later version; this
system keeps v0.1.4.
