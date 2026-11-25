# Changelog

## v0.3.1 (2026-11-25)

A hotfix of the hardware test software of spfi_compress after the lab run of v0.3.0. The HDL, the Composer files, the
cores and their pins are those of v0.3.0: a device image built from v0.3.0 serves v0.3.1, and the system simulations
of v0.3.0 stay the evidence (the hardware tests make the RMAP requests and exchanges of v0.3.0).

### Fixed

- TC-RS-HW-12 (far-end link reset) failed on hardware on error registers that a restart of the link moves by design.
  The lanes reach Active at different times, so the alignment watchdog of OpenFibre expired once during the link start
  (`ML_ALIGN_TIMEOUTS` 1 in both runs, as after the power-up link start of TC-RS-HW-01), and in one run a frame was cut
  and sent again (`DL_ERRORS.FRAME`, `DL_FRAME_COUNT` and `DL_RETRIES` 1). The images after the reset were equal to the
  model in both runs. TC-RS-HW-12 now accepts `ML_ALIGN_TIMEOUTS` and the causes of a retry (`CRC16`, `CRC8`, `FRAME`,
  `SEQUENCE` of `DL_ERRORS` and their counters), but a cause only when `DL_ERRORS` held it when the link was back,
  before the images: a cause that appears during the images still fails the test case. The second run of TC-RS-HW-12
  in TC-RS-HW-18 follows.
- TC-RS-HW-13 (lane removal) accepted a retry but not its cause: it now accepts the sequence error of the
  realignment with `DL_RETRIES` (`DL_ERRORS.SEQUENCE`, `DL_SEQ_COUNT`).
- `systest.caused`: a register that a test case expects can also have allowed bits besides the expected value.

### Changed

- TC-RS-HW-12 records `DL_ERRORS` as it was when the link was back, before the images.
- The procedures name v0.3.1 and the release 0.2.1 of the Python package openspacehdl.

## v0.3.0 (2026-11-19)

The pipeline reset in all three systems: a command over RMAP resets the processing path with the link up, tested at
rest, in the middle of an image and during traffic (TC-RS-HW-19 to 23). Every core moves to Open Logic FT 4.8.0-ft.2:
OpenRMAP v0.1.2, OpenSPP v0.2.1, OpenFibre v0.1.5, OpenCrypto v0.1.1 and OpenCompact v0.3.2. The hardware test
software of v0.2.1 is part of this release. The HDL changes, so a device image of v0.2.x does not serve v0.3.0.

### Added

- **Pipeline reset** in all three systems: the pipeline reset controller `prst0` (`olo_ft_reset_ctrl_axi` of Open Logic
  FT) on a new last port of the AXI4-Lite decoder resets the processing path on command over RMAP, without a new
  device image and with the link up. Its output is the source of the reset group `pipe` of the Composer file: `spp0`
  takes it on its data path reset `DpRst`, the FIFOs, compressors, chunkers, framers and AES-GCM cores of the system on
  `Rst`; the SpaceFibre port and its adapter, the N-Char bridge, the RMAP target, the decoder and the ECC monitor keep
  the system reset. The system requirements PR-01 to PR-12 are in the README of every system. `prst0` sits at
  0x00080000 in spfi_compress and spfi_encrypt and at 0x000C0000 in spfi_compress_encrypt; the decoders of
  spfi_compress and spfi_encrypt now take 20 address bits, so their maps repeat every 0x100000 bytes and the port
  indices above the last window answer with an error response.
- Hardware test: every run starts with the pipeline reset (TC-RS-HW-01; `--no-pipeline-reset` skips it, for diagnosis
  only), TC-RS-HW-02 checks `prst0`, and TC-RS-HW-19 tests the reset at rest in all three systems: values left inside
  and outside the processing path, the writes that `prst0` ignores, the reset values of every member, everything
  outside unchanged, a second command, then traffic with the Packet Sequence Counts from 0. In the encrypting systems
  the keys are loaded again with an invocation counter above every counter used before the reset. The second run of
  TC-RS-HW-18 skips the pipeline reset. The procedures have a new section "Pipeline reset", and "Repeated runs" needs a
  new device image only after a stop that may have left state outside the processing path.

- TC-RS-HW-20 to 23: the pipeline reset in the middle of an image (spfi_compress, spfi_compress_encrypt), with
  misconfigured compressors (the same two systems), during traffic (all three) and a fresh run of TC-RS-HW-02 to 07
  after it (all three). What comes back around a reset during traffic is judged per output APID against what the data
  sent can give: complete images exactly, an image in flight as far as its samples taken fill its chunks, at most one
  Space Packet cut by the reset (a proper prefix and the byte 0x00 of the EEP beat of OpenSPP), nothing after it. In
  the encrypting systems the keys are loaded again after a reset during traffic with the counter after the last chunk
  before the traffic plus the largest number of chunks that the data sent can give; the bound of one compressed image
  over the ranges of the runtime configuration comes from the model of OpenCompact. A received packet carries its end
  marker as the transport reports it: the system model and the system testbench report it, the run against the
  device does not, and the test judges the content.

### Changed

- The recovery after a failure of v0.2.1 (it reads both channels until they are quiet, checks the health of the system
  and records and clears the error registers) also makes a pipeline reset and, once TC-RS-HW-05 has passed,
  configures the system again (the hook `reconfigure` of every system). The encrypting systems load their keys at a
  counter `RECOVERY_COUNTER_GAP` above the last one.
- TC-RS-HW-15 of spfi_compress gets the status 1 for a write at 0x90000, after the last register window, in place of
  the read of the identification of `spp0` one map further on.
- The system model models `prst0` (its register block, `COUNT`, the byte enables of a write) and the reset of the
  members of the reset group.

### Fixed

- The far-end link reset of TC-RS-HW-12 and of the recovery after a failure: on the test equipment the link control of
  the STAR-Ultra PCIe Interface failed with "Could not get the link status.", because it needs channel 0 of the device,
  which the transport holds for RMAP. The transport now closes channel 0, runs the link control in a child process
  (`refsys.far_end_link`) and opens channel 0 again after it; the device frees the channel some 0.5 s after the end of
  the child process, and the transport tries for up to 5 s.
- `run.log`: the result line of a failed test case carries its reason, as the report does.

### Versions

- Open Logic FT 4.8.0-ft.2 (was 4.7.0-ft.12), the fault-tolerant fork based on Open Logic 4.8.0. Of the Open Logic
  files that the systems use, only `olo_axi_master_full` (the memory master of OpenRMAP) changes: the size width of a
  command at 8-bit AXI data; at the 32-bit data of `rmap0` its constants and functions give the values of
  4.7.0-ft.12. The compile orders list six new files of the fixed-point library (`olo_fix_log2`, `olo_fix_dds`,
  `olo_fix_cic_intp_tdm_par`, `olo_fix_fir_dec_par_chtdm` and two private files of `olo_fix_log2`), which no unit of
  the systems names. Every core of the systems is verified with 4.8.0-ft.2.
- OpenRMAP v0.1.2 (was v0.1.1): the size width of a command of the memory master of the target is two bits fewer than
  the address at every data width (`memSizeBits`, at most 24). At the 32-bit data and 32-bit addresses of `rmap0` it
  stays 24 bits.
- OpenSPP v0.2.1 (was v0.2.0): Open Logic FT 4.8.0-ft.2; the sources are those of v0.2.0.
- OpenFibre v0.1.5 (was v0.1.4): `VC_BW_OVER` and `VC_BW_UNDER` of `ofb_mib` are defined from the reset. The `sim`
  profile of the hardware tests still clears both flags before their first read in a run (`systest.clear_bw_flags`),
  so the stimuli of the system simulations stay the same.
- OpenCrypto v0.1.1 (was v0.1.0): register stages on the read paths of the key memories, the job FIFO, the data FIFO
  and before the packer of the dense output, for the setup timing at 200 MHz. The key table resolves one context with
  a generated IV per three clock cycles (was two); a job leaves the key table two clock cycles later, a data beat
  reaches the combiner one clock cycle later, and the dense output adds two clock cycles of latency (was one). Ports,
  generics, stream formats, the register map and the erasure of the key table after a reset (`NumKeys_g` + 3 clock
  cycles, PR-05) are those of v0.1.0. No check, wait or bound of the hardware tests and of the system model depends on
  these latencies: the waits poll a status within limits far above them.
- OpenCompact v0.3.2 (was v0.3.1): the predictor gives the registers that the runtime configuration and parameters
  disable a constant value, and the coders and the predictor read their memories at a defined address before the first
  image after a reset; the compressed images and the throughput are those of v0.3.1. The user guide states the output
  of `ocp_channel` at a reset during an image (requirement CH-RS-02): the words that the output has not taken are
  discarded, and the packet ends without `Out_Last`.
- Top levels generated by Open Space Composer 0.11.3-beta; the system files have the format version 3 (reset groups).

### Known issues

- The hardware test of the systems is not complete; the systems are verified by simulation (TRL 3). No implemented
  build of the systems is part of this release.
- The AMD transceiver model sometimes fails to power up in an elaborated snapshot (`DIV_VAL_CHK`); the xsim runner
  elaborates and simulates such a run again, at most three times in total, and reports every repetition.
- OpenFibre: the CDR lock bit of LANE_STATUS is no usable indication with the GTY configuration of the VCK190 designs;
  the hardware test records it without an expected value.

### Verification

- System GHDL regression: 22 of 22 groups of system simulations pass with the cores of this release (sysclk 150 MHz,
  profile `sim`, seed 1): 18 groups in CI (81 of 81 test cases) and the four longest on a host (14 of 14).
- Host tests: 591 passed, 24 skipped, with pytest 9.1.1 and with 9.0.2.
- Stimulus guard (`RS_STIMULUS_GUARD=1`): the stimuli of the 22 groups are unchanged on the release head, so the
  regression holds for the systems saved with the catalog of the core releases and for the far-end link reset in a
  child process.
- GHDL analyses all sources of the three systems (`python run.py --compile`); the exported top levels equal the
  export of the Composer at `tools/composer_ref.txt` (`tools/check_hdl.py`), and the pins of the system files equal
  the submodules (`tools/check_pins.py`).
- Vivado rtl elaboration of the three systems with both reference clocks (si570, 8a34001): 6 of 6 without an error,
  with the warning classes of v0.2.0.
- GTY xsim: not run, the PHY path and its inputs are unchanged since v0.2.0.

## v0.2.1 (2026-11-18)

A hotfix of the hardware test software, after the lab run of spfi_compress on 2026-11-17. The HDL, the Composer files,
the cores and their pins are those of v0.2.0. A device image built from v0.2.0 stays valid, and the system
simulations of v0.2.0 stay the evidence: their stimuli are unchanged.

### Hardware test

- **No operator step.** TC-RS-HW-12 asked the operator to reset the link of the STAR-Ultra in STAR-System. The run
  holds the device while it goes on, so the configuration software could not reach it. The test equipment now resets
  the link itself:
  - the SpaceFibre port of the STAR-Ultra is held in link reset for 0.1 s through the library of the STAR-Ultra PCIe
    Interface, then released;
  - the test waits until the link of the port is ready again with its lanes;
  - it records what the port showed and checks that its link was down while held.

  `--far-end-port` names the port (default 1). `--check-environment` checks the Python package of the STAR-Ultra
  PCIe Interface and its library together with the package of STAR-System.
- **Log.**
  - A passing check makes no event; the result of a test case counts its passing checks.
  - A wait for a register value is one event with its polls and the time it took. Its failure names the register
    and its fields, the value read, the time and the polls.
  - After a failure the test reads the link, every lane and the error registers that hold a value and records them
    (report section "State after a failure").
  - `run.log` holds every event but the register reads and writes, which `steps.jsonl` keeps.
  - Discarded packets and RMAP retries are notes of the log.
- **After a failure the run goes on.** Before the next test case it recovers:
  1. it reads both channels until they are quiet;
  2. it checks that RMAP answers and the link is initialised with all lanes, once with a far-end link reset if not;
  3. it records and clears the error registers.

  The systems of this release have no pipeline reset: what a failure leaves in the processing path stays and can make
  the next test case fail as well. A test case whose prerequisites did not pass is not run. A system that stays
  unhealthy stops the run. `--stop-at-first-failure` replaces `--keep-going` and stops at the first failure.
- **Rate of TC-RS-HW-09.** It timed the reference model, the transfer and the checks together. The transport now
  prepares a transfer before it is timed (one transmit operation with every packet, one receive operation) and times
  from the first submission to the arrival of the last packet. The images, the expected compressed images and the
  packets are built before; the received packets are converted and checked after. The record gives the rate of the
  samples and of the link packets in both directions. TC-RS-HW-09 of spfi_compress no longer claims OpenFibre
  CORE-PF-01: the system takes less than the capacity of the link.
- **Evidence of a run.**
  - `--image` records the name, SHA-256 and `build_info.txt` of the programmed device image.
  - The report counts the test cases by result. Its requirements are headed "Requirements exercised per test case".
  - A test case that provokes errors records the expected ones, the allowed ones that moved and the others apart.
  - TC-RS-HW-02 records the values that a compressor at its generics shows.
  - The working tree counts its changed entries and records the ignored ones apart.
- **System model.** The base system model gets `receive` (no packet waits), which the recovery reads with.

### Verification

- Host tests: 543 passed, 11 skipped.
- Stimulus guard (`RS_STIMULUS_GUARD=1`): the stimuli of the system simulations of the evidence are unchanged.
- The hardware tests of the three systems pass against their system models with the lab profile.
- The far-end link reset is tested against a stand-in for the library of the STAR-Ultra PCIe Interface; it runs on the
  device in the next lab run.

## v0.2.0 (2026-10-31)

The third system, spfi_compress; OpenCompact v0.3.0 with runtime configuration and parameters on two channels;
OpenFibre v0.1.4, OpenRMAP v0.1.1 and Open Logic FT 4.7.0-ft.11; the system clock of 150 MHz; the hardware tests as
pytest tests with a step log, and repeated runs on the same device.

### Systems

- **spfi_compress**: a SpaceFibre node that compresses images with CCSDS 123.0-B-2 in four channels,
  spfi_compress_encrypt without its encryption. A chunker of Open Logic (`olo_base_chunker`) cuts every compressed
  image into chunks of 4096 bytes, and every chunk leaves as one Space Packet with the APID of its channel; every
  image starts in a new Space Packet, and the Space Packets carry no mark of the end of an image.
  - Composer file and generated top level, the register windows without gaps.
  - Target: AMD VCK190 at the system clock of 150 MHz, as spfi_compress_encrypt.
  - Hardware test TC-RS-HW-01 to 18, with the Space Packets of every channel compared one by one with the chunks of
    the compressed images of the reference model of OpenCompact, the aborted images included.
  - System testbench in GHDL and xsim, with its stimuli and checks verified against the software model of the system:
    TC-RS-HW-01 to 18 pass in GHDL at the system clock of 150 MHz (TC-RS-HW-06, 08 and 09 with images of the size of
    the lab), TC-RS-HW-01 to 05 in xsim with the AMD transceiver model at the clocks of the default device image.

### Versions

- OpenCompact v0.3.0. Since v0.1.0:
  - v0.1.1: `ocp_predictor` corrected for the AMD Vivado simulator, with unchanged behaviour in every simulator.
  - v0.2.0: the runtime configuration of the image dimensions and of the band-independent error limit values from
    registers (`RuntimeConfig_g`; on `ocp0` and `ocp3`, see Changed). Register map version 2 (ID 0x4F435002) adds
    CONTROL, STATUS, NEXT_X, NEXT_Y, NEXT_Z, NEXT_LIMITS, LIMITS and the field RUNTIME_CONFIG of GENERICS; the
    existing registers keep their offsets and fields. New source `ocp_image_start.vhd`; Open Logic FT 4.7.0-ft.9.
  - v0.3.0: the runtime parameters of every image from registers (`RuntimeParams_g`, only with `RuntimeConfig_g`; on
    `ocp0` and `ocp3`, see Changed): the user-defined data, the sub-frame interleaving depth M, the weight update
    parameters and the parameters of the entropy coder, carried in the header of the image. Register map version 3
    (ID 0x4F435003) adds NEXT_SAMPLES, NEXT_ORDER, NEXT_WEIGHTS, NEXT_ADAPTIVE, NEXT_BLOCK and the field
    RUNTIME_PARAMS of GENERICS; with the runtime parameters SAMPLES, ORDER, WEIGHTS, ADAPTIVE and BLOCK show the values
    of the last image. With the generic false the compressed images and the throughput are those of v0.2.0. The
    reference model has a decoder (`ocp_model.decompress`), which the hardware test of spfi_compress uses to decode
    every complete compressed image.

  See the release notes of OpenCompact
  [v0.1.1](https://github.com/open-space-hdl/opencompact/releases/tag/v0.1.1),
  [v0.2.0](https://github.com/open-space-hdl/opencompact/releases/tag/v0.2.0) and
  [v0.3.0](https://github.com/open-space-hdl/opencompact/releases/tag/v0.3.0).
- OpenFibre v0.1.4. Since v0.1.0:
  - v0.1.1: with three or four lanes, a lane that becomes active after the others no longer causes a realignment
    after the link has started, which showed as a sequence error and retries at both ends without loss of data;
    NoSignal is always de-asserted, since the transceiver cannot detect a lost signal at this line rate.
  - v0.1.2: the GTY adapter resets the receive elastic buffer of a lane after an overflow or underflow and once per
    lane start.
  - v0.1.3: the GTY adapter resets the entire receiver of a lane, the other lanes undisturbed, when the lane restarts
    (the reset AMD AM002 recommends after the far end powers up, after electrical idle and after connecting the
    line), holds the CDR while the receiver reports electrical idle, and uses the transceiver settings of the AMD
    documentation more closely. Register map version 10 (ID 0x0FB1000A) adds LANE_INIT_TIMEOUT, PA_CTRL and the
    status, events and counter of the receiver reset in fields that version 9 reserved.
  - v0.1.4: the error recovery buffer registers every admitted data word with its row write and the commit of its
    item (one core clock cycle more from admission to transmission), which closes the timing of the lane clock in the
    reference design; Open Logic FT 4.7.0-ft.11.

  See the release notes of OpenFibre
  [v0.1.1](https://github.com/open-space-hdl/openfibre/releases/tag/v0.1.1),
  [v0.1.2](https://github.com/open-space-hdl/openfibre/releases/tag/v0.1.2),
  [v0.1.3](https://github.com/open-space-hdl/openfibre/releases/tag/v0.1.3) and
  [v0.1.4](https://github.com/open-space-hdl/openfibre/releases/tag/v0.1.4). The generic `LaneJoinHoldWords_g` of
  `ofb_core` keeps its default in every system: 8192 lane clock cycles, below the alignment watchdog
  `AlignTimeoutWords_g` of 10000.
- OpenRMAP v0.1.1, a maintenance release on Open Logic FT 4.7.0-ft.11 with the sources of v0.1.0: the memory master
  of the target takes the next chunk of a non-verified write while the bytes of the current one pass; see the
  [release notes of OpenRMAP v0.1.1](https://github.com/open-space-hdl/openrmap/releases/tag/v0.1.1).
- Open Logic FT 4.7.0-ft.11 (was 4.7.0-ft.8), the Open Logic library of the block catalog. Of the Open Logic files
  that the systems compile, four change: `olo_base_crc` (OpenFibre: `In_Last` and `In_Be` only for an accepted beat),
  `olo_axi_master_full`, `olo_ft_axi_master_full` and `olo_ft_private_fifo_stream` (the memory master of OpenRMAP;
  the last file is new in the compile orders). OpenFibre v0.1.4 and OpenRMAP v0.1.1 are verified with 4.7.0-ft.11;
  OpenSPP and OpenCrypto with 4.7.0-ft.6, and the Open Logic files they use are the same in 4.7.0-ft.8 and
  4.7.0-ft.11; OpenCompact v0.2.0 with 4.7.0-ft.9, and the Open Logic files it uses are the same in 4.7.0-ft.9 and
  4.7.0-ft.11. The scoped constraint of the read path of the distributed RAM (`olo_base_ram_sdp.tcl`) also
  matches the read register after a synthesis retiming.
- Top levels generated by Open Space Composer 0.9.12-beta with the block catalog 13caffc27875
  (`tools/composer_ref.txt`), which adds the rate and size data of `olo_base_chunker`, OpenFibre v0.1.4, OpenRMAP
  v0.1.1, Open Logic FT 4.7.0-ft.11 and OpenCompact v0.3.0. The generated files of every system change in the catalog
  and the commits of their headers and compile orders, the new sources `ofb_pa_gty_lane_rst.vhd` of the adapter and
  `olo_ft_private_fifo_stream.vhd` of Open Logic in the compile order, the generics of `ofb_core` and `ofb_pa_gty`
  (the lane settings under Changed, `LaneJoinHoldWords_g` and `RxBufReset_g` at their defaults), the control and
  status signals of the receiver reset between `ofb0` and `pa0`, the identification 0x0FB1000A of `ofb0` in the header
  of the register banks, and the integration notes of OpenFibre in the compile order (the two reference clock
  frequencies, `Phy_NoSignal`, the receiver reset). In spfi_compress and spfi_compress_encrypt they also change in
  `RuntimeConfig_g` and `RuntimeParams_g` of `ocp0` to `ocp3` (true on `ocp0` and `ocp3`, see Changed), the new
  source `ocp_image_start.vhd` of OpenCompact in the compile order, the identification 0x4F435003 of `ocp0` to `ocp3`,
  and the qualifier of the ECC flags of the FIFOs `rxf0` to `rxf3` at `edac0`: a flag counts only in a cycle in which
  the FIFO delivers its word (`Out_Valid` and `Out_Ready`), so that a corrected word held under back-pressure counts
  once and not once per stalled cycle. The system files are saved by 0.9.12-beta and keep the layout version 1 of the
  netlist stored in a system file (`resolved.version`), so that a program that reads it can refuse a layout it does
  not know.

### Changed

- spfi_compress and spfi_compress_encrypt: the compressors of channels 0 and 3 have the runtime configuration of
  OpenCompact v0.2.0 (`RuntimeConfig_g`). Over RMAP, the image dimensions of both channels and the absolute error
  limit value of channel 3 change from image to image without a new device image; channels 1 and 2 keep the
  configuration of their generics. After a reset all channels compress as before. The hardware test TC-RS-HW-10
  (`sw/refsys/ocptest.py`) commits the values of every image, checks the refusal of values out of range and compares
  every image with the model image of its values; the system simulation runs it with three images of the bounds per
  channel.
- spfi_compress and spfi_compress_encrypt: the compressors of channels 0 and 3 also have the runtime parameters of
  OpenCompact v0.3.0 (`RuntimeParams_g`). Over RMAP, the user-defined data, M, the weight update parameters and the
  parameters of the entropy coder change from image to image with the image dimensions; after a reset they are those
  of the generics. On these channels the upper bound on the compressed image is its maximum over the ranges of the
  parameters (at Umax 32 and gamma\* 4), which lowers the throughput budget of channel 0 to 1.62 Gbit/s and of
  channel 3 to 1.13 Gbit/s at input packets of 1024 bytes (system READMEs, section Throughput).
  TC-RS-HW-10 gives every image of channels 0 and 3 its parameters (the sequence of the runtime parameter testbench
  of OpenCompact: the bounds of every parameter in use, then values at random), checks the refusal of parameters out
  of range and the values taken, and compares every image with the model image of its values: 22 images per channel
  on hardware, three in the system simulation. In the system simulation the time limit of an exchange grows by the
  clock cycles in which the slowest compressor works on its images (throughput model of OpenCompact).
- Lane initialisation of every system as in the OpenFibre reference design for the VCK190: `pa0` resets the receiver
  of a lane at every restart of the lane (`RxRstGapCycles_g` = 0), and the initialisation timeout of the lanes is
  468750 words, 3 ms (`InitTimeoutWords_g` of `ofb0`). The timeout deviates from ECSS-E-ST-50-11C 5.5.2.6b.1 and
  5.5.2.7b.1, as in the reference design; the README of every system states it. The bounds of the waits of `pa0` are
  scaled to the 150 MHz of `Clk_sys`, so that their times are those of the reference design (`RxRstDoneCycles_g`
  22500000, `CdrWaitCycles_g` 15000). The control and status ports of the receiver reset connect `ofb0` and `pa0`.
- Hardware test: TC-RS-HW-01 records for every lane of `ofb0` the completed receiver resets, the initialisation
  timeouts and the receiver status (`RECORD` lines, no pass criterion); `report.txt` of the system simulation lists
  them.
- spfi_compress, hardware test plan of the cores: TC-RS-HW-01 takes the baseline of the error and memory error
  registers of every block (`sw/refsys/errorstate.py`) and TC-RS-HW-17 checks at the end of the run that none counted
  since; TC-RS-HW-02 also checks the generics registers of the SpaceFibre port, the RMAP target and the Space Packet
  node and the initialised link, TC-RS-HW-06 the header fields of the Space Packets sent. New test cases: TC-RS-HW-11
  (registers of the SpaceFibre port), TC-RS-HW-12 (link reset from the far end, an operator step), TC-RS-HW-13 (a
  lane leaves the link and rejoins), TC-RS-HW-14 (single and double memory errors injected into the SpaceFibre port,
  the RMAP target and the Space Packet node), TC-RS-HW-15 (RMAP commands that the target refuses, discards or treats
  specially, `sw/refsys/rmaptest.py`) and TC-RS-HW-16 (Space Packets that the node discards, sequence counts that
  wrap, round robin, `sw/refsys/spptest.py`). A test case that provokes errors checks that only the registers it
  targets moved. The procedure lists per core what the test cases exercise and what they do not (section 10). The
  system model follows the RMAP target (`sw/refsys/rmapmodel.py`), the AXI4-Lite decoder (above its ports the map
  repeats, or the decoder answers DECERR at a port index without a port), the receive checks of the Space Packet
  node and the register semantics of the cores.
- Hardware test of spfi_compress: `--model` runs it against the system model, `--check-environment` checks the test
  computer, `--records` writes the records of a run (every compressed image with its hash, the recorded values with
  their test case), which `tools/render_records.py` renders into the tables of a report. Every complete compressed
  image is decoded with the decoder of the reference model of OpenCompact when the pinned version has it. The test
  computer needs Python, pytest and the far-end software; `sw/tests/test_lab_software.py` checks that the software
  of the hardware tests starts no other program than Git, which records the state of the working tree.
- Hardware tests of the three systems as pytest tests (`systems/<system>/sw/hwtest`, plugin `sw/refsys/hwplugin.py`,
  pytest 9.0 or later, below 10): one test per test case, in the order of the procedure. A run stops at the first
  failure and reports the later test cases as not run (`--keep-going` runs those whose dependencies passed, for
  diagnosis); `--tc` selects test cases with those they build on. Every run writes a new directory below
  `--results`, outside the repository:
  - the step log (`steps.jsonl` and `run.log`, `sw/refsys/steplog.py`): every step, register access (with the name
    of the register, an element of a register array as `LANE_STATUS(3)`), checked value with its expected value,
    configuration of a compressor, image (samples, SDUs, compressed size, bits per sample,
    ratio, chunks, decoding check with the largest error), exchange of Space Packets and recorded value;
  - the JUnit XML report of pytest with the requirements of every test case;
  - the records file and its report (`tools/render_records.py`), with the test cases not run and the requirements.
  The run records the versions, the commits and the state of the working tree of the repository and its submodules
  at its start and at its end (`git status --porcelain --ignored`); run it with `python -B`, so that Python writes
  no bytecode into the repository. spfi_compress carries the requirements of section 10 of its procedure as markers
  of its test cases. `hardware_test.py --device` and `--model` run the same pytest tests. The logging makes no RMAP
  request of its own: the stimuli of the system simulations are those of the test before it.
- System simulations: the check writes the step log of the procedure (`steps.jsonl`, `steps.log`) next to
  `report.txt`, and `run_ghdl.py` and `run_xsim.py` take `--recheck <run directory>`, which checks the records of an
  earlier simulation again with the procedure of the current code, without a simulation.
- `sw/refsys/`: the shared parts of the hardware tests and the system model also serve a system without framers and
  AES-GCM cores: the key material of the test context is optional, the identification and the key slots cover the
  channels with an AES-GCM core, and a system gives the Packet Data Fields that a channel sends (`send()` of the
  system model).
- `sw/refsys/`: the words of the status interface of OpenCompact follow its register map version 2: the field
  RUNTIME_CONFIG of GENERICS, and LIMITS with the band-independent error limit values of the generics after a reset.
  TC-RS-HW-02 of spfi_compress and spfi_compress_encrypt also reads LIMITS of every compressor.
- `tools/check_pins.py` accepts a core verified with a newer Open Logic FT tag than the tag of the systems when every
  Open Logic file that the core uses is the same in both tags: the files that the files of the core in the compile
  order of the system name, closed over the references of Open Logic to itself. It prints the core, both tags and the
  number of files compared, and otherwise names the files that differ; with `--offline` it compares them in the
  clone of the offline package, whose bundle of Open Logic FT carries the tags. With Open Logic FT 4.7.0-ft.11 no
  core of the systems is verified with a newer tag than the systems use.
- `tools/vivado/check_constraints.tcl` also stops the implementation on the critical warning `olo_base_ram_sdp-1`, by
  which the Open Logic constraint script reports a distributed RAM without a first read data register to constrain
  (4.7.0-ft.11); `get_msg_config` counts it under the id `olo_base_ram_sdp 1`.
- VCK190 targets of every system: after an implementation, `tcl/build.tcl` reports the timing verdict of the design
  on the console and in `results/build_info.txt`: "TIMING MET: WNS x ns, WHS y ns", or "TIMING NOT MET: ..." when the
  worst setup or hold slack is below 0. The verdict does not stop the build.
- `tools/make_offline_bundle.py` also writes `build.bat` into the offline package: on Windows it clones the
  repository from the bundles next to it (when the clone directory does not exist yet), checks the submodules and
  the pins (with `python`, else the launcher `py -3`, or `RS_PYTHON`), sets `RS_VIVADO_OUT` and builds one system
  with the words of `build.tcl`. A clone at another commit stops it. `README.txt` describes it, with the defaults of
  the directories, and gives the two ways out of a clone at another commit: rename it, or set `RS_DIR` and
  `RS_VIVADO_OUT` to new directories named after the commit of the package (the lines for cmd and PowerShell).
- Both systems: system clock 150 MHz for the hardware bring-up (Composer file, generated top level and
  constraints, `clk_sys_mhz` of the VCK190 `tcl/build.tcl`), with the throughput figures of spfi_compress_encrypt at
  150 MHz. The CIPS derives the clock from its 1200 MHz PLL by the divisor 8.
- VCK190 targets of both systems: `tcl/build.tcl` takes the frequency of the transceiver reference clock, 100 MHz or
  156.25 MHz, for the transceiver wizard instance and the clock constraint. The default image takes the oscillator
  Si570 at 100 MHz, its frequency after every power-up (UG1366), and needs no setting in the System Controller. The
  images of v0.1.0 are built with `-tclargs si570 156.25` and `-tclargs 8a34001`.
- VCK190 hardware test procedure: the methodology report of an implemented build holds two TIMING-54 (critical
  warning) for the clock pair exceptions of the system constraints, which are read scoped to the system. The
  procedure lists them as expected: the lane clock exists only inside the system.
- System testbenches: the system clock is a generic of the bench (`SysClkKhz_g`), and the runners take
  `--sysclk-mhz 150` for the 150 MHz of the hardware builds, in xsim and in GHDL (default 200 MHz, as before). With
  `--refclk-mhz 100 --sysclk-mhz 150` the xsim has the clocks of the default device image.
- System testbenches: the tools that the xsim and GHDL runners start write their temporary files to `tmp/` in their
  working directory below the output directory of the runner, not to the temporary directory of the user, which the
  C code that xelab compiles for a snapshot could fill.
- VCK190 hardware test procedure of spfi_encrypt, section "When the link does not come up": how the lane states of
  the far end show how far the lane initialisation got.

### Known issues

- The hardware test of the systems is open; the systems are verified by simulation (TRL 3).
- No implemented build of spfi_compress or spfi_compress_encrypt at the system clock of 150 MHz with OpenFibre v0.1.4
  is part of this release; the build of the hardware test shows its timing.
- spfi_compress_encrypt: the system simulation of TC-RS-HW-08 (lost data) at the image size of the lab is the run of
  2026-10-18, at the bench clock of 200 MHz with the cores of v0.1.0. spfi_compress, which shares the channel path,
  ran TC-RS-HW-08 again with the cores of this release at 150 MHz.
- The systems have no reset of their data path that the hardware test could command. Consecutive complete runs need
  no new device image; after a run that stopped early, the device is programmed again (section "Repeated runs" of
  the procedures).
- The AMD transceiver model sometimes fails to power up in an elaborated snapshot (`DIV_VAL_CHK`); the xsim runner
  elaborates and simulates such a run again, at most three times in total, and reports every repetition.
- OpenFibre v0.1.4 in simulation: `VC_BW_OVER` and `VC_BW_UNDER` of the SpaceFibre port hold 'U' until the status of
  the core has crossed into the management clock domain, after the lane clock of the simulated transceiver starts. A
  read puts 'U' into the RMAP reply, whose frame is then sent again without end. The `sim` profile of the hardware test
  of spfi_compress clears both flags in TC-RS-HW-01 before their first read; the `lab` profile reads them as they are.
  The hardware is not affected: the flags start at 0. Details in the README of the system testbench of spfi_compress.
- OpenFibre v0.1.4: the CDR lock bit of LANE_STATUS is no usable indication with the GTY configuration of the VCK190
  designs; the hardware test records it without an expected value.

### Fixed

- Hardware tests: `--check-environment` of spfi_encrypt reported the reference model of OpenCompact as missing and
  ended with exit code 1 on every test computer. The check took the model from the module path, which only the
  systems with a compressor extend. It now checks the items that a run of the system needs: the reference model of
  OpenCompact for the systems with a compressor, `pycryptodome` for the systems with OpenCrypto (new item) and the
  Python package of the far-end software for a run against the device. `--check-environment --target model` checks
  for a run against the system model, which needs no far-end software. Section 8 of every procedure shows the lines
  of the check.
- Hardware test of spfi_compress and spfi_compress_encrypt: a second run on the same device without a new device
  image failed. The Space Packet node continues the Packet Sequence Count of every output APID from run to run, and
  the script expected the Space Packets of every run to start at count 0 (TC-RS-HW-06); the compressors with the
  runtime configuration keep the values of the last COMMIT and of the last image, which the script expected to be
  those of the reset (TC-RS-HW-02 and 10). TC-RS-HW-05 now reads the counts from the send APID table and commits the
  values of the generics when the runtime registers hold others, both recorded, and TC-RS-HW-02 accepts the values of
  an image of an earlier run, values in range. The new TC-RS-HW-18 of every system runs the test cases again on the
  same device. Consecutive complete runs need no new device image; after a run that stopped early the device is
  programmed again (section "Repeated runs" of the procedures).
- `run.py` takes QuestaSim from `VUNIT_MODELSIM_PATH` or from `vsim` on the PATH and stops with a message when
  neither is there; `build.bat` of the offline package takes Vivado from `VIVADO` or from the PATH. Neither has the
  installation directory of a particular computer as its default any more.
- VCK190 targets of every system: `tcl/build.tcl` puts the text of `build_info.txt` together first, writes it with
  one `puts` (UTF-8, LF line ends) and stops when the file does not have the size of the text after it is closed.
  Once, in the step `rtl`, the file stayed empty and its lines reached `vivado.log` instead; `build_info.txt` is a
  record of every build and of the hardware test.
- VCK190 target READMEs of both systems: the maximum delays of the clock crossings in the system constraints are
  6.4 ns, the period of the lane clock, since the system clock is 150 MHz.
- VCK190 targets of both systems: the implementation run uses the strategy Performance_Explore, without the
  physical optimization after routing of Performance_ExplorePostRoutePhysOpt. In that step Vivado 2025.2 stopped
  once with an access violation, after a complete routing, and the build ended without a device image.
- The system testbench of spfi_compress_encrypt runs in xsim with the AMD transceiver model; TC-RS-HW-01 to 05 pass
  (known issue of v0.1.0).
- The timing exceptions of the clock crossings between the lane clock and the system clock apply in the builds of
  both systems. The system XDC of Open Space Composer 0.9.6-beta and earlier found the lane clock at the pin
  `i_pa0/LaneClk`, which synthesis renames; both exceptions then matched no clock, and the crossings were timed as
  synchronous paths and failed timing. The system XDC of 0.9.7-beta finds the lane clock at the net of its signal
  (`pa0_LaneClk_s`).
- VCK190 targets of both systems: the implementation run stops before `opt_design` when a constraint did not apply
  (`tools/vivado/check_constraints.tcl`): an object query that matched nothing, or the constraint file of a module
  that is not in the netlist. Vivado reports these as warnings and continues, and the timing summary then lacks the
  constraint. Synthesis runs without incremental reuse of an earlier netlist.
- VCK190 targets of every system: `tcl/build.tcl` sets `XILINX_LOCAL_USER_DATA=no` for the runs of `launch_runs`,
  and `build.bat` of the offline package also for Vivado itself (AMD AR 63253). On a fresh installation of Vivado the
  runs that start at the same time create the user directory of the Tcl Store together, and one of them can fail at
  start-up with Common 17-1257 ("Failed to create directory") and "tclapp::load_apps failed".

## v0.1.0 (2026-10-18)

First release of the reference systems.

### Systems

- **spfi_encrypt**: a SpaceFibre node with an RMAP target and four AES-GCM encryption channels for Space Packets.
  - Target: AMD VCK190, with the transceiver reference clock from the clock generator 8A34001 or the oscillator
    Si570.
  - Hardware test procedure and script against a STAR-Ultra PCIe.
  - System testbench in xsim with the AMD transceiver model and in GHDL with the behavioural adapter model of
    OpenFibre.
- **spfi_compress_encrypt**: a SpaceFibre node that compresses images with CCSDS 123.0-B-2 and encrypts the
  compressed images with AES-GCM.
  - Four OpenCompact channels, each with one of the configurations verified by OpenCompact, for images of 64 x 64
    samples in 48 bands of 16 bits.
  - A FIFO in front of every compressor, so that the four compressors take the samples in parallel from the shared
    receive buffer of OpenSPP.
  - Target: AMD VCK190, as for spfi_encrypt.
  - Hardware test TC-RS-HW-01 to 09, with every compressed image compared byte by byte with the reference model of
    OpenCompact.
  - System testbench in GHDL, with TC-RS-HW-06 and TC-RS-HW-08 at the lab image size.

### Shared parts

- `sw/refsys/`: RMAP, Space Packets, OpenCrypto chunks, OpenCompact images and channel model, register maps, facts of
  a system file, transports, and the parts of the hardware tests, system models and system-simulation stimuli that
  both systems share.
- `sim/`: the far end of the system testbenches.
- `tools/`:
  - `sysbench.py`, `ghdl.py` and `xsim.py`: the runners of the system testbenches;
  - `check_pins.py` and `check_hdl.py`: the checks of the core versions and of the generated top levels;
  - `make_offline_bundle.py`: the offline package of a build.

### Versions

- Cores: OpenFibre v0.1.0, OpenRMAP v0.1.0, OpenSPP v0.1.0, OpenCrypto v0.1.0 and OpenCompact v0.1.0.
- Open Logic FT 4.7.0-ft.8. The cores are released with Open Logic FT 4.7.0-ft.6; the systems build them with
  4.7.0-ft.8, and the system simulations ran with 4.7.0-ft.8.
- Top levels generated by Open Space Composer 0.9.5-beta (`tools/composer_ref.txt`).

### Known issues

- The system testbench of spfi_compress_encrypt does not run in xsim: the AMD Vivado simulator stops at time 0 in
  `ocp_predictor` of OpenCompact v0.1.0; see the
  [release notes of OpenCompact v0.1.0](https://github.com/open-space-hdl/opencompact/releases/tag/v0.1.0). GHDL and
  synthesis are not affected.
- Neither system has been tested on hardware yet.
