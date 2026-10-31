# spfi_encrypt: System Testbench

The system testbench runs the [hardware test](../targets/vck190/docs/hardware_test.md) of spfi_encrypt in simulation:
the same test cases TC-RS-HW-01 to 07 and 18, with the same requests and the same pass criteria, against the generated
top level of the system instead of the board.

## Structure

```text
+----------------------------- spfi_encrypt_tb ------------------------------+
|                                                                            |
|  i_dut: spfi_encrypt (hdl/)         i_far: refsys_tb.rs_far_end           |
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
- The bench runs `Clk_sys` at 200 MHz by default and, with `--sysclk-mhz 150`, at the 150 MHz of the hardware builds
  (`SysClkKhz_g`). The frequency enters the generated top level only as `ClkFreqHz_g`, the clock of the memory
  scrubbers, which the system leaves off (`ScrubPeriod_g` 0.0).
- The far end is the far end of all system testbenches ([sim/](../../../sim/README.md)): a second SpaceFibre port,
  an `ofb_core` with its own physical adapter, joined to the system by the serial lines, lane i to lane i in both
  directions. It has the generics of `ofb0` of the Composer file (`NumLanes_g`, `NumVc_g`, `VcInDepth_g`). It stands
  for the test equipment of the hardware test and, as the port of the STAR-Ultra, is set to auto-start
  (`LaneStart_g => false`, `AutoStart_g => true`): the system starts the link (`LaneStart_g` of `ofb0` is true in the
  Composer file).
- The physical adapter of both ends is the entity `openfibre.ofb_pa_gty` that the simulation compiles: the AMD GTY
  adapter with the transceiver model of Vivado (xsim), or its behavioural model, the architecture `mdl` of OpenFibre
  (GHDL), which drives the serial pins with the 8B/10B bit stream (OpenFibre user guide, "System simulation with the
  behavioural adapter"). The bench does not depend on the variant. The adapter of the far end uses the control
  record 1 of the model (`ModelIdx_g => 1`), the adapter of the system the default record 0; the bench keeps the
  default timing of the model, that of the transceiver model.

## Flow

The steps are those of all system testbenches (`sw/refsys/sysvectors.py`, [sim/](../../../sim/README.md)):

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
python systems/spfi_encrypt/sim/run_xsim.py --refclk-mhz 100 TC-RS-HW-01   # reference clock 100 MHz
python systems/spfi_encrypt/sim/run_xsim.py --refclk-mhz 100 --sysclk-mhz 150   # clocks of the lab image
```

The runner needs AMD Vivado 2025.2 (`VIVADO_PATH` or `vivado` on the `PATH`). It creates the transceiver wizard
instance `ofb_gtw` with the script of OpenFibre and exports its simulation sources, compiles Open Logic FT, the cores
and the top level in the order of `hdl/spfi_encrypt_compile_order.txt`, the far end and the bench, elaborates the
bench, runs the simulations and checks them (`tools/sysbench.py`). Results go to
`vivado_out/xsim/spfi_encrypt/runs/<name>/` (`RS_XSIM_OUT` sets another directory): the stimulus, the records, the
log, `probes.vcd` and `report.txt`. `probes.vcd` holds the ready states of the transceivers and the lane states of
both ends, for the analysis of a link that does not initialise; the checks do not use it. The tools that the runner
starts write their temporary files (`TEMP`, `TMP`, `TMPDIR`) to `tmp/` in their working directory below
`vivado_out/xsim/spfi_encrypt/`, not to the temporary directory of the user.

`--refclk-mhz` sets the frequency of the transceiver reference clock, 156.25 MHz (default) or 100 MHz, the two
frequencies of the VCK190 targets: the runner creates `ofb_gtw` for it in its own directory (`ip_refclk_100`) and
the bench drives `RefClk` of both ends at it (`RefClkKhz_g`); the run directory gets the suffix `_refclk_100`.
The line rate and the lane clock are the same at both frequencies. The behavioural model of GHDL takes only
156.25 MHz: its lane clock is the reference clock.

`--sysclk-mhz` sets the frequency of the system clock of the bench, 200 MHz (default) or 150 MHz, the frequency of the
hardware builds (`SysClkKhz_g`), in xsim and in GHDL; the run directory gets the suffix `_sysclk_150`. With
`--refclk-mhz 100 --sysclk-mhz 150` the simulation has the clocks of the device image of the default build.

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
`hdl/ofb_pa_gty/compile_order_sim.txt` of OpenFibre in place of the GTY adapter, the far end and the bench, then runs
the simulations and checks them as `run_xsim.py` does. Results go to `sim_out/ghdl/spfi_encrypt/runs/<name>/`
(`RS_GHDL_OUT` sets another directory), with the same files; GHDL also writes its temporary files to `tmp/` in its
working directory.

### Without a simulator

`python -m pytest systems/spfi_encrypt/sim` checks the stimuli and the checks: a Python stand-in for the bench plays
`stimulus.txt` against the software model and writes the records in the format of the bench. The tests include a
corrupted chunk, a missing packet, a wrong reply address, a request that differs from the recording and a simulation
that did not end.

## Results

Profile `sim`, seed 1, system clock 150 MHz (`--sysclk-mhz 150`), runs of 2026-10-28, 29 and 31 with the versions of
the cores that the system file of v0.2.0 pins (OpenFibre v0.1.4, OpenRMAP v0.1.1, OpenSPP v0.1.0 and OpenCrypto
v0.1.0, Open Logic FT 4.7.0-ft.11): xsim of Vivado 2025.2 with the transceiver model at both ends and the reference
clock of the default device image, 100 MHz (`--refclk-mhz 100`), and GHDL 6.0 with the behavioural model of the
adapter at both ends, whose lane clock is the reference clock of 156.25 MHz. Each simulation starts with the reset
and the link initialisation:

| Simulation | Test cases, in their order | Simulated time | Wall-clock time |
| --- | --- | --- | --- |
| xsim | TC-RS-HW-01 to 05 | 254.8 us | 25.1 min |
| GHDL 1 | TC-RS-HW-01 to 07 | 347.8 us | 9.0 min |
| GHDL 2 | TC-RS-HW-01 to 07, 18 | 603.9 us | 15.8 min |

| Step | xsim, simulated time | GHDL, simulated time | Result |
| --- | --- | --- | --- |
| Transmitters of both ends ready | 66.6 us | 64.9 us | |
| Link initialised (all lanes Active at both ends) | 92.5 us | 91.8 us | |
| TC-RS-HW-01 Link | 16.4 us | 15.1 us | Pass |
| TC-RS-HW-02 Identification | 12.7 us | 12.1 us | Pass |
| TC-RS-HW-03 RMAP | 11.2 us | 10.7 us | Pass |
| TC-RS-HW-04 Commands of several words | 28.2 us | 26.8 us | Pass |
| TC-RS-HW-05 Configuration | 93.9 us | 89.6 us | Pass |
| TC-RS-HW-06 Encryption (8 Space Packets, 12 chunks) | not run | 32.3 us | Pass |
| TC-RS-HW-07 Counters | not run | 69.5 us | Pass |
| TC-RS-HW-18 Repeated run (TC-RS-HW-01 to 07 again, GHDL 2) | not run | 256.0 us | Pass |

The GHDL times of TC-RS-HW-01 to 07 are those of GHDL 1; in GHDL 2 they are the same. The xsim run of this release
covers the link, RMAP and the configuration (TC-RS-HW-01 to 05), as for the other systems; TC-RS-HW-06 and 07 passed
in xsim with the cores of v0.1.0 (release v0.1.0). In TC-RS-HW-18 every test case passes again on the same system,
without a reset. Recorded values (no pass criteria): after the link-up every lane counts 0 receiver resets and no
initialisation timeout and has CDR lock, in both simulators.

The xsim simulation takes 25 minutes of wall-clock time on a workstation, after about 16 minutes of compilation and
elaboration; the GHDL simulation of TC-RS-HW-01 to 07 takes 9 minutes, after a few seconds of analysis. `report.txt`
gives the wall-clock time of every test case. In both simulations the lanes of both ends leave their reset once the
transmitters of their adapter are ready, and pass Disabled, Wait and Started at the same times at both ends
(`probes.vcd`). With the behavioural model all lanes of both ends reach Active together at 91.1 us; with the
transceiver model lanes 0 and 1 reach Active at 92.37 us and lanes 2 and 3 19 ns later, at both ends. The sequencer of
the bench, which polls the status of the far end, sees the initialised link at 91.8 and 92.5 us. The test cases
take about 5 % more simulated time with the transceiver model: the datapath latency of the behavioural model is a
little shorter than that of the transceiver model (model description of OpenFibre, section "Limits").

## Limits

- The far end is an OpenFibre port like `ofb0`. A reading of the SpaceFibre standard that both ends share, right or
  wrong, does not show up in this simulation. The hardware test against the STAR-Ultra, an independent
  implementation, is the check of interoperability.
- The simulation does not cover the board: the CIPS, the clock generator of the reference clock and the board
  constraints are tested by the build and the hardware test of the target.
