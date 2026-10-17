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
|  | rmap0, spp0, ocp0..3,  |  lanes  | ofb_pa_gty   |    | ofb_core      |  |
|  | frm0..3, ocr0..3       |         +--------------+    +-------+-------+  |
|  +------------------------+                               VC 0, VC 1, MIB  |
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

| Profile | Images per channel in TC-RS-HW-06 | Images per channel in TC-RS-HW-09 | Use |
| --- | --- | --- | --- |
| `lab` | 5 (scene, stripes, noise, flat bands with steps, flat bands with spikes) | 8 | Hardware test |
| `sim` | 1 (scene) | 2 | System simulation |

TC-RS-HW-08 sends five images per channel in both profiles. The procedure takes the expected compressed images from
the reference model of OpenCompact; for an image that ended with an error, the model gives no length, so after the
chunks that it knows the procedure receives until the channel is quiet (operation `quiet` of the far end, 100 us).

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
system of the Composer file with smaller images: the records of the model pass, the procedure receives until the
channel is quiet only in TC-RS-HW-08, and a chunk too many in the window, a chunk after the window and a channel that
does not become quiet fail.

## Results

Not yet simulated.

## Limits

- The far end is an OpenFibre port like `ofb0`; the hardware test against the STAR-Ultra is the check of
  interoperability (spfi_encrypt, sim/README.md, section "Limits").
- The compressors are compared with the reference model of OpenCompact, which OpenCompact verifies against the CNES
  reference software; the simulation does not decode the compressed images.
