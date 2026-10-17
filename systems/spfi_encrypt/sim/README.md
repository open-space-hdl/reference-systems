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
  (`NumLanes_g`, `NumVc_g`, `VcInDepth_g`). It stands for the test equipment of the hardware test and, as the port of
  the STAR-Ultra, is set to auto-start (`LaneStart_g => false`, `AutoStart_g => true`): the system starts the link
  (`LaneStart_g` of `ofb0` is true in the Composer file).
- The physical adapter of both ends is the entity `openfibre.ofb_pa_gty` that the simulation compiles: the AMD GTY
  adapter with the transceiver model of Vivado (xsim), or its behavioural model, the architecture `mdl` of OpenFibre
  (GHDL), which drives the serial pins with the 8B/10B bit stream (OpenFibre user guide, "System simulation with the
  behavioural adapter"). The bench does not depend on the variant. The adapter of the far end uses the control
  record 1 of the model (`ModelIdx_g => 1`), the adapter of the system the default record 0; the bench keeps the
  default timing of the model, that of the transceiver model.

## Flow

1. `vectors.py write` runs the procedure of `sw/hardware_test.py` against the software model of the system
   (`sw/system_model.py`) through a recording transport. The requests become the operations of the far end in
   `stimulus.txt`: the wait for the link, then per test case the packets on its virtual channels and the polls of
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

The system testbench runs in xsim with the AMD transceiver model and in GHDL with the behavioural model of the
adapter; the cores carry their own QuestaSim evidence (regressions and code coverage).

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

### GHDL with the behavioural model of the adapter

```shell
python systems/spfi_encrypt/sim/run_ghdl.py                           # all test cases in one simulation
python systems/spfi_encrypt/sim/run_ghdl.py TC-RS-HW-04               # one test case
python systems/spfi_encrypt/sim/run_ghdl.py --each                    # one simulation per test case
```

The runner needs GHDL 6.0 (`GHDL_PATH` or `ghdl` on the `PATH`). It analyses Open Logic FT, the cores and the top
level in the order of `hdl/spfi_encrypt_compile_order.txt`, with the files of
`hdl/ofb_pa_gty/compile_order_sim.txt` of OpenFibre in place of the GTY adapter, and the bench, then runs the
simulations and checks them as `run_xsim.py` does. Results go to `sim_out/ghdl/spfi_encrypt/runs/<name>/`
(`RS_GHDL_OUT` sets another directory), with the same files.

### Without a simulator

`python -m pytest systems/spfi_encrypt/sim` checks the stimuli and the checks: a Python stand-in for the bench plays
`stimulus.txt` against the software model and writes the records in the format of the bench. The tests include a
corrupted chunk, a missing packet, a wrong reply address, a request that differs from the recording and a simulation
that did not end.

## Results

Profile `sim`, seed 1, all test cases in one simulation, with the versions of the cores that the system file pins:
xsim of Vivado 2025.2 with the transceiver model at both ends, and GHDL 6.0 with the behavioural model of the adapter
at both ends.

| Step | xsim, simulated time | GHDL, simulated time | Result |
| --- | --- | --- | --- |
| Transmitters of both ends ready | 64.9 us | 60.7 us | |
| Link initialised (all lanes Active at both ends) | 92.6 us | 88.4 us | |
| TC-RS-HW-01 Link | 8.6 us | 8.1 us | Pass |
| TC-RS-HW-02 Identification | 11.4 us | 10.8 us | Pass |
| TC-RS-HW-03 RMAP | 10.1 us | 9.5 us | Pass |
| TC-RS-HW-04 Commands of several words | 25.2 us | 23.8 us | Pass |
| TC-RS-HW-05 Configuration | 83.6 us | 79.4 us | Pass |
| TC-RS-HW-06 Encryption (8 Space Packets, 12 chunks) | 30.3 us | 29.4 us | Pass |
| TC-RS-HW-07 Counters | 65.7 us | 62.2 us | Pass |
| Total | 327.4 us | 311.8 us | 7 of 7 in both |

An RMAP command and its reply take about 1.4 us with the transceiver model. The xsim simulation of the whole profile
takes about 40 minutes of wall-clock time on a workstation, after about 17 minutes of compilation and elaboration;
the GHDL simulation takes about 13 minutes, after a few seconds of analysis. `report.txt` gives the wall-clock time of
every test case. Both cores hold their link in Near-End Reset until the transmitters of their adapter are ready
(`Stat_TxReady` to `Phy_TxReady`). The lanes of both ends then reach Wait together; the lanes of `ofb0` go on to
Started at once (`LaneStart_g`), while the lanes of the far end stay in Wait until they detect the signal of `ofb0`
(`probes.vcd`): the system starts the link.

The two adapters differ where the model description of OpenFibre says they do:

- The behavioural model counts the reset sequence of the transmitters from the end of the stretched reset of the
  adapter (1000 cycles of the 200 MHz system clock after the release of `Rst` at 1 us); the transceiver model is
  ready at 64.9 us. From the ready transmitters on, the link initialisation takes the same time with both: all lanes
  are Active 26.6 us later, within 0.1 us.
- The far end detects the signal of `ofb0` at once with the model, 0.25 to 0.47 us later with the transceiver model.
- The datapath latency of the model is a little shorter: the test cases with many RMAP commands take about 5 % less
  simulated time.

## Limits

- The far end is an OpenFibre port like `ofb0`. A reading of the SpaceFibre standard that both ends share, right or
  wrong, does not show up in this simulation. The hardware test against the STAR-Ultra, an independent
  implementation, is the check of interoperability.
- The simulation does not cover the board: the CIPS, the clock generator of the reference clock and the board
  constraints are tested by the build and the hardware test of the target.
