# System Testbenches: Far End and Runner

Every system has a testbench `systems/<system>/sim/tb/<system>_tb.vhd` that runs the test cases of its hardware test
in simulation. The testbench holds the generated top level of the system and the far end of this directory, joined by
their serial lines and fed by one reference clock and one system clock. The far end, the format of the stimuli and
records, and the runner are the same for all systems.

## Far end

`tb/rs_far_end.vhd` (library `refsys_tb`) is a SpaceFibre node that stands for the test equipment of the hardware
test: an `ofb_core` with its own physical adapter `openfibre.ofb_pa_gty`, set to auto-start (`LaneStart_g => false`,
`AutoStart_g => true`) as the port of the test equipment, so that the system starts the link. Its generics are
those of the SpaceFibre port of the system (`NumLanes_g`, `NumVc_g`, `VcInDepth_g`, from the Composer file) and
`ModelIdx_g`, the control record of the behavioural model of the adapter (default 1; the adapter of the system uses
record 0, the default of the entity). `tb/rs_tb_pkg.vhd` reads the stimulus file and writes the records.

The far end plays `stimulus.txt` in the working directory of the simulator:

| Operation | What the far end does |
| --- | --- |
| `mark <name>` | Notes the start of a test case |
| `mibw <address> <data>` | Writes a register of its own port |
| `mibp <address> <mask> <value> <limit ns>` | Reads a register of its own port until it has the value (the wait for the link) |
| `send <vc> <bytes> <packet>` | Sends a packet on a virtual channel |
| `recv <vc> <count> <limit ns>` | Waits until count more packets arrived on a virtual channel |
| `poll <vc> <limit ns> <offset> <mask> <value> <bytes> <packet>` | Sends the packet until the word at the offset of the reply has the value under the mask |
| `quiet <vc> <quiet ns> <limit ns>` | Receives on a virtual channel until no packet arrives for the quiet time; the event gives the number of packets beyond those awaited before |
| `end` | Ends the simulation |

It records every packet that it receives per virtual channel (`rx_vc<n>.txt`: time, length, EEP, bytes) and the
simulated time of its events (`events.txt`: time, kind, number of the operation, values); a limit that expires ends
the simulation with the event `timeout`.

## Stimuli and checks

`sw/refsys/sysvectors.py` writes the stimuli and checks the records; `systems/<system>/sim/vectors.py` gives it the
hardware test and the software model of the system. `write` runs the procedure of the hardware test against the
software model through a recording transport, which turns every request into operations of the far end. `check` runs
the procedure again through a replay transport, which requires every request to equal the recorded one and answers
with the packets that the system sent in the simulation; every pass criterion of the hardware test then applies to the
simulated system. The procedure receives until a channel is quiet only where the software model cannot give the number
of packets in advance.

## Runner

`tools/sysbench.py` builds and runs the testbench of a system; `systems/<system>/sim/run_xsim.py` and
`systems/<system>/sim/run_ghdl.py` call it:

- xsim (`tools/xsim.py`): the AMD GTY adapter with the transceiver model of Vivado at both ends.
- GHDL (`tools/ghdl.py`): the behavioural model of the adapter at both ends (architecture `mdl` of `ofb_pa_gty`,
  OpenFibre user guide, "System simulation with the behavioural adapter"), with the files of
  `hdl/ofb_pa_gty/compile_order_sim.txt` of OpenFibre in place of the adapter in the compile order of the Composer.

Both analyse Open Logic FT, the cores and the top level in the order of `hdl/<system>_compile_order.txt`, then the far
end and the testbench, run the simulations and check them. Every run gets a directory with the stimulus, the records,
the log, `probes.vcd` (the ready states of the transceivers and the lane states of both ends, for the analysis of a
link that does not initialise; the checks do not use it), `report.txt` and the step log of the check
(`steps.jsonl` and `steps.log`, the events of the hardware test procedure as in a lab run).

`--recheck <run directory>` simulates nothing: it checks the records of an earlier simulation again with the procedure
of the current code and writes the report, the step log and `recheck.txt` (the commit of the code, the time and the
result) into `<run directory>/recheck_<date>_<time>`. The records must come from the stimuli that the current code
writes (`sw/tests/test_stimulus_guard.py` compares them): when a request differs from the recorded one, the recheck
ends with exit code 2 and the result "REQUEST MISMATCH", since its check then says nothing about the simulation.
