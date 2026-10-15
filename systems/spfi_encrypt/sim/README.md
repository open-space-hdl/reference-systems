# spfi_encrypt: System Testbench

The system testbench runs the [hardware test](../targets/vck190/docs/hardware_test.md) of spfi_encrypt in
simulation: the same test cases TC-RS-HW-01 to 07, with the same requests and the same pass criteria, against the
generated top level of the system instead of the board.

## Structure

```text
+----------------------------- spfi_encrypt_tb ------------------------------+
|                                                                            |
|  i_dut: spfi_encrypt (hdl/)         far end                                |
|  +-----------------------+          +--------------+    +---------------+  |
|  | ofb0 - pa0 (GTY)      |<-------->| i_far_pa     |----| i_far_core    |  |
|  | rmap0, spp0, ocr0..3  |  lanes   | ofb_pa_gty   |    | ofb_core      |  |
|  +-----------------------+          +--------------+    +-------+-------+  |
|                                                           VC 0, VC 1, MIB  |
|  sequencer:  stimulus.txt -> virtual channels and MIB of the far end       |
|  receivers:  virtual channels of the far end -> rx_vc<n>.txt, events.txt   |
|  RefClk 156.25 MHz and Clk_sys 200 MHz feed both ends                      |
+----------------------------------------------------------------------------+
```

- The system is the generated top level `spfi_encrypt` of `hdl/`. The bench drives and observes its top-level ports
  only: the reset, the system clock, the reference clock and the serial lines.
- The far end is a second SpaceFibre port: an `ofb_core` with its own physical adapter, joined to the system by the
  serial lines, lane i to lane i in both directions. It has the generics of `ofb0` of the Composer file
  (`NumLanes_g`, `NumVc_g`, `VcInDepth_g`). It stands for the test equipment of the hardware test: it starts the link
  through its MIB, as the STAR-Ultra does, while `ofb0` keeps its reset values (AutoStart).
- The physical adapter of both ends is the entity `openfibre.ofb_pa_gty` that the simulation compiles: the AMD GTY
  adapter with the transceiver model of Vivado, or a pin-compatible model of OpenFibre. The bench does not depend on
  the variant.

## Flow

1. `vectors.py write` runs the procedure of `sw/hardware_test.py` against the software model of the system
   (`sw/system_model.py`) through a recording transport. The requests become the operations of the far end in
   `stimulus.txt`: the start of the link, then per test case the packets on its virtual channels and the polls of
   registers that the procedure waits for.
2. The bench plays `stimulus.txt` and records every packet that the far end receives (`rx_vc<n>.txt`) and the
   simulated time of its events (`events.txt`).
3. `vectors.py check` runs the procedure again through a replay transport. Every request must equal the recorded one,
   and the transport answers with the packets that the system sent in the simulation. All pass criteria of the
   hardware test apply, the decryption and the tag check of every chunk with AES-GCM included. `report.txt` gives the
   result, the simulated time and the wall-clock time of every test case.

The far end does what the routing switch of the test equipment does with the path addresses: a command leaves
without its path address, and the reply address in front of a reply is checked and removed. Channel 0 of the hardware
test is virtual channel 0 (RMAP), channel 1 is virtual channel 1 (Space Packets), as the Composer file connects them.

## Profiles and test cases

The hardware test and the simulation share their test cases and differ in size only (`PROFILES` of
`sw/hardware_test.py`):

| Profile | Space Packets in TC-RS-HW-06 | Use |
| --- | --- | --- |
| `lab` | 40 | Hardware test |
| `sim` | 8 | System simulation |

Both profiles start TC-RS-HW-06 with the boundary cases: 1 byte, one byte less than a chunk, a chunk, one byte more
than a chunk, and the longest Space Packet that the receive buffer of `spp0` holds; the further packets have random
lengths. A test case can run on its own: the selection adds the test cases it builds on (TC-RS-HW-06 needs the
configuration of TC-RS-HW-05, TC-RS-HW-07 the configuration and the traffic). Every simulation starts with the reset
and the link initialisation.

## Running

### xsim with the AMD transceiver model

```shell
python systems/spfi_encrypt/sim/run_xsim.py                           # all test cases in one simulation
python systems/spfi_encrypt/sim/run_xsim.py TC-RS-HW-04               # one test case
python systems/spfi_encrypt/sim/run_xsim.py --each                    # one simulation per test case
```

The runner needs AMD Vivado 2025.2 (`VIVADO_PATH` or `vivado` on the `PATH`). It creates the transceiver wizard
instance `ofb_gtw` with the script of OpenFibre and exports its simulation sources, compiles Open Logic FT, the cores
and the top level in the order of `hdl/spfi_encrypt_compile_order.txt` and the bench, elaborates the bench, runs the
simulations and checks them. Results go to `vivado_out/xsim/spfi_encrypt/runs/<name>/` (`RS_XSIM_OUT` sets another
directory): the stimulus, the records, the log, `probes.vcd` and `report.txt`. `probes.vcd` holds the ready states of
the transceivers and the lane states of both ends, for the analysis of a link that does not initialise; the checks do
not use it.

The transceiver model sometimes fails to power up in an elaborated snapshot (`DIV_VAL_CHK`, see the verification
report of `ofb_pa_gty` in OpenFibre). The runner elaborates and simulates such a run again, at most three times in
total, and reports every repetition.

### GHDL and QuestaSim

The regular regression with GHDL and QuestaSim follows when OpenFibre provides a pin-compatible model of
`ofb_pa_gty`; the bench and the checks stay as they are.

### Without a simulator

`python -m pytest systems/spfi_encrypt/sim` checks the stimuli and the checks: a Python stand-in for the bench plays
`stimulus.txt` against the software model and writes the records in the format of the bench. The tests include a
corrupted chunk, a missing packet, a wrong reply address, a request that differs from the recording and a simulation
that did not end.

## Limits

- The far end is an OpenFibre port like `ofb0`. A reading of the SpaceFibre standard that both ends share, right or
  wrong, does not show up in this simulation. The hardware test against the STAR-Ultra, an independent
  implementation, is the check of interoperability.
- The simulation does not cover the board: the CIPS, the clock generator of the reference clock and the board
  constraints are tested by the build and the hardware test of the target.
