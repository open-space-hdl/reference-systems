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
chunks of 4096 bytes. As in spfi_encrypt, the bench runs `Clk_sys` at 200 MHz and the hardware builds at 150 MHz; the
frequency sets only the clock of the memory scrubbers, which are off.

## Profiles and test cases

The hardware test and the simulation share their test cases and differ in size only (`PROFILES` of
`sw/hardware_test.py`), as in spfi_compress_encrypt:

| Profile | Images per channel in TC-RS-HW-06 | Lost SDUs in TC-RS-HW-08 | Images per channel in TC-RS-HW-09 | Use |
| --- | --- | --- | --- | --- |
| `lab` | 5 (scene, stripes, noise, flat bands with steps, flat bands with spikes) | cases (a) and (b) on every channel, five images per channel | 8 | Hardware test |
| `sim` | 1 (scene) | case (a) on channels 0 and 2 (two images), case (b) on channels 1 and 3 (three images) | 2 | System simulation |

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

The system has not run in a simulator yet. The bench, the stimuli and the checks are those of spfi_compress_encrypt,
whose simulations pass in GHDL (TC-RS-HW-05, 06 and 08 at the image size of the lab) and in xsim (TC-RS-HW-01 to 05);
the records of the software model pass the checks of this system (`python -m pytest systems/spfi_compress/sim`).

## Limits

- The far end is an OpenFibre port like `ofb0`; the hardware test against the STAR-Ultra is the check of
  interoperability (spfi_encrypt, sim/README.md, section "Limits").
- The compressors are compared with the reference model of OpenCompact, which OpenCompact verifies against the CNES
  reference software; the simulation does not decode the compressed images.
