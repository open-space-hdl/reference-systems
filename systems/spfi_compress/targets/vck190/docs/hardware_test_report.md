# spfi_compress on the VCK190: Hardware Test Report

This report records the hardware test of spfi_compress on the AMD VCK190 that serves as the TRL 4 evidence of the
cores in the system: OpenFibre, OpenCompact, OpenRMAP, OpenSPP and the Open Logic FT entities. The procedure and the
test plan are [hardware_test.md](hardware_test.md); its section 10 lists per core the requirements that the test cases
exercise on the board and those they do not, with the reason. The tables of sections 4 and 5 and of Appendix A are
rendered from the records of the run (`tools/render_records.py`).

## 1. Item under test and run

| Item | Value |
| --- | --- |
| Run | 2026-11-25, 15:57:35 to 16:07:15, profile `lab`, seed 1, target `device` |
| Installation | openspacehdl 0.2.1 from PyPI, `export --git` (section 3 of the procedure); working tree clean at the start and at the end of the run |
| Repository | reference-systems v0.3.1, commit `8322da71c432727966782efe941bc7a1ffb2e0d4` |
| Cores (submodule tags) | OpenFibre v0.1.5, OpenCompact v0.3.2, OpenRMAP v0.1.2, OpenSPP v0.2.1, Open Logic FT 4.8.0-ft.2 (en_cl_fix open-logic-2.4.0) |
| Device image | `spfi_compress_vck190_top.pdi`, 7086080 bytes, SHA-256 `79d67184c2569e59b39daf24ee2794af16e52f72260a7d454d03254629cbe58c` |
| Build | To be completed from the build records of this device image: commit of the build, Vivado version, reference clock option |
| Test computer | Windows 11, Python 3.14.0, pytest 9.0.2, pycryptodome 3.23.0 |

The repository carries OpenCrypto v0.1.1 as a further submodule; spfi_compress does not contain it.

## 2. Build results

To be completed from the build records of the device image of section 1 (`results/timing_summary.rpt` and
`results/utilization_hierarchical.rpt`, section 9 of the procedure): the timing verdict with WNS and WHS of `Clk_sys`
and of the lane clock, and the utilisation per core.

## 3. Set-up

- The AMD VCK190 evaluation board, boot mode JTAG, the device image of section 1 programmed with the Vivado Hardware
  Manager; the reference clock of the option of the build (section 5 of the procedure).
- The far end: a commercial SpaceFibre interface in a PCIe test computer, one port of four lanes at 6.25 Gbit/s,
  8B/10B, scrambling on, connected to QSFP1 of the board. The test computer runs `sw/hardware_test.py` and reaches the
  system only over this link: RMAP on virtual channel 0, Space Packets on virtual channel 1.

## 4. Results per test case

23 test cases ran: 23 passed, none failed, none was left out. TC-RS-HW-18 ran the test cases before it a second time
on the same device, without the pipeline reset of TC-RS-HW-01 and without a new device image; all passed again.

| Test case | Function | Result |
| --- | --- | --- |
| TC-RS-HW-01 | Link, pipeline reset, baseline of the error registers | Pass |
| TC-RS-HW-02 | Identification registers, configuration of the compressors | Pass |
| TC-RS-HW-03 | RMAP reads and writes, wrong key | Pass |
| TC-RS-HW-04 | RMAP commands of several words | Pass |
| TC-RS-HW-05 | Configuration of `spp0` | Pass |
| TC-RS-HW-06 | Compression, five images per channel, channels interleaved | Pass |
| TC-RS-HW-07 | Counters | Pass |
| TC-RS-HW-08 | Lost data (a Space Packet and the last Space Packet of an image) | Pass |
| TC-RS-HW-09 | Sustained rate, eight images per channel, all channels at once | Pass |
| TC-RS-HW-10 | Runtime configuration and runtime parameters of `ocp0` and `ocp3` | Pass |
| TC-RS-HW-11 | Registers of the SpaceFibre port | Pass |
| TC-RS-HW-12 | Far-end link reset | Pass |
| TC-RS-HW-13 | Lane removal and rejoin | Pass |
| TC-RS-HW-14 | Memory errors injected through `ECC_INJECT` | Pass |
| TC-RS-HW-15 | RMAP errors | Pass |
| TC-RS-HW-16 | Space Packet errors | Pass |
| TC-RS-HW-17 | End of run: every error register | Pass |
| TC-RS-HW-18 | Repeated run on the same device | Pass |
| TC-RS-HW-19 | Pipeline reset at rest | Pass |
| TC-RS-HW-20 | Pipeline reset in the middle of an image | Pass |
| TC-RS-HW-21 | Pipeline reset with misconfigured compressors | Pass |
| TC-RS-HW-22 | Pipeline reset during traffic | Pass |
| TC-RS-HW-23 | Test cases TC-RS-HW-02 to 07 after the pipeline reset | Pass |

Appendix A lists the requirements that every test case exercised and every recorded value.

## 5. Records

| Record | Value |
| --- | --- |
| Lanes at link start (TC-RS-HW-01) | Every lane: 6 receiver resets and 5 initialisation timeouts before the link came up, `RXRESETDONE` 1 |
| Far-end link reset (TC-RS-HW-12) | The far end held the link reset for 0.100 s: 4 active lanes before, 0 and link not ready while held, ready with 4 lanes 0.014 s after the release; `DL_ERRORS.FAR_END_LINK_RESET` set; per lane 37 to 39 receiver resets and 35 to 36 initialisation timeouts during the reset |
| Lane removal (TC-RS-HW-13) | Lane 3 left and rejoined (11 receiver resets, 10 initialisation timeouts); `ML_MISALIGNED` 2 |
| Rate (TC-RS-HW-09) | 32 images, 12582912 bytes of samples, 7039684 bytes compressed, in 0.011 s from the first submission to the last packet received: 9.184 Gbit/s of samples; 9.196 Gbit/s of link packets to the system and 5.151 Gbit/s from it |
| Memory errors (TC-RS-HW-14) | Single errors corrected and counted on `ofb0` channels 0 to 3 and 6 to 8, `rmap0` channels 0 to 2 and `spp0` channels 0, 1, 2, 4, 5 and 7; double errors detected and counted on `ofb0` channels 6 and 7 (one frame sent again: `DL_RETRIES` 1), `rmap0` channel 0 and `spp0` channel 5 |
| Decoding check | 352 compressed images of the run decoded with the model decoder, none left undecoded: equal to the images sent on `ocp0` to `ocp2`, within the maximum error of every sample on `ocp3` |
| End of run (TC-RS-HW-17) | Every error register 0 since the baseline, apart from the fields recorded only (section 10 of the procedure); every lane 0 receiver resets since the baseline |

## 6. Deviations and anomalies

| ID | Observation | Assessment |
| --- | --- | --- |
| D1 | `ML_ALIGN_TIMEOUTS` of `ofb0` reads 1 after the link start (TC-RS-HW-01, before the baseline clears it) and after the far-end link reset (TC-RS-HW-12): the alignment watchdog of the Multi-Lane layer expired once while the lanes came up. The link then reached four lanes and carried the traffic of the run without a further expiry (TC-RS-HW-17). | A simulation of the start with the transceiver models of the device does not reproduce the expiry; its cause lies outside the simulated models, in the start of the analogue channel. The test software accepts one expiry per link start (reference-systems v0.3.1). |
| D2 | `LANE_STATUS.CDR_LOCK` of every lane reads 0 while the lanes carry traffic. | Open in OpenFibre (issue 57). The lanes carry the traffic of the run without errors, so the reading concerns the status indication only. Recorded, no pass criterion. |
| D3 | `LANE_REASONS` of every lane shows another value at every read (two equal bytes). | Recorded only (section 10 of the procedure); no pass criterion depends on it. |
| D4 | Receiver resets and initialisation timeouts at the link start and during the far-end link reset (section 5). | The receivers restart while the far end does not send, as specified for the initialisation timeout of the lanes; recorded. |
| D5 | `VC_BW_UNDER` and `VC_BW_OVER` of `ofb0` hold a value. | The bandwidth limits of the virtual channels are at their reset values, which the system does not use; recorded only. |
| D6 | The requirements of TC-RS-HW-06 in the test software of the run name `OFB:DL-VI-06`, which OpenFibre does not have. | A wrong entry in the list of the test software; the requirements audit of section 7 found it. The list and section 10 of the procedure name `DL-VI-01/02/05` from reference-systems after v0.3.1. |
| D7 | The run found no `build_info.txt` next to the device image. | The identity of the device image is its SHA-256 (section 1); the build records complete sections 1 and 2. |

Earlier runs of the campaign on the same procedure, before the test software reached its present state:

| Run | Installation | Result | Cause |
| --- | --- | --- | --- |
| 2026-11-17 | openspacehdl 0.1.0, reference-systems v0.2.0 | TC-RS-HW-01 to 11 pass, TC-RS-HW-12 fails, the rest not run | The far-end link reset was an operator step that the test could not verify; the run stopped at the first failure. The test software now resets the far end itself and continues after a failure. |
| 2026-11-19 | openspacehdl 0.1.1, reference-systems v0.2.1 | 16 of 18 pass | The call of the test software that resets the far end failed. |
| 2026-11-25, 12:58 | openspacehdl 0.2.0, reference-systems v0.3.0 | 21 of 23 pass | TC-RS-HW-12 and 18 checked the error registers after the far-end link reset without the expiry of D1 and the retry that a restart of the link causes. reference-systems v0.3.1 accepts both. |
| 2026-11-25, 15:57 | openspacehdl 0.2.1, reference-systems v0.3.1 | 23 of 23 pass | This report |

The HDL of the system and of its cores is the same in reference-systems v0.3.0 and v0.3.1.

## 7. Coverage per core

Every requirement of the four cores with requirement IDs is verified by a test case of its regression in simulation
(`docs/compliance.md` of the core at the tag of section 1: no requirement without a test case). The run exercised on
the board the requirements of Appendix A; section 10 of the procedure gives per group the requirements that the board
does not exercise, with the reason.

| Core (tag) | Requirements | Exercised on the board | Not exercised on the board, main reasons |
| --- | --- | --- | --- |
| OpenFibre (v0.1.5) | 297 | 180 | Broadcast (port tied off), quality of service at its reset values, PRBS and loopback, standby and loss of signal, bypass and redundant lanes, the requirements of the package and of the standalone board design |
| OpenCompact (v0.3.2) | 143 | 90 | Configurations that the four channels do not use (sample depth and sign, prediction bands, weights, relative and band-dependent limits, the stride order, restricted codes), memory correction (no injection path), the package |
| OpenRMAP (v0.1.2) | 82 | 43 | The initiator (18 requirements) and the passthrough: the system has a target only; EEP cases (the transport ends every packet with an EOP); the package |
| OpenSPP (v0.2.1) | 132 | 46 | The Packet Service and the packetizer (22), the address and command paths that the system does not configure, the rejection at `MAX_LENGTH`, EEP cases, the package |

Per group (exercised on the board / requirements):

- OpenFibre: CORE 17/23, DL 47/79, LN 24/32, MG 18/27, ML 53/72, NI 5/12, PA 16/32, PKG 0/11, VCK 0/9.
- OpenCompact: CH 10/14, CO 25/44, EC 13/18, IA 12/13, MB 15/17, PK 0/5, PR 15/32.
- OpenRMAP: CO 6/13, IN 0/18, MG 9/12, PKG 0/4, TG 28/35.
- OpenSPP: AD 0/6, CM 0/8, CO 10/28, EN 6/9, MG 10/14, PK 0/5, PT 0/22, RE 10/14, SE 9/18, TB 1/8.

Open Logic FT has no requirement IDs. Its entities in the system and inside the cores and what the run exercised of
each are in the table "Open Logic FT" of section 10 of the procedure: data path and ECC correction of the FIFOs and
RAMs of `ofb0`, `rmap0` and `spp0` (TC-RS-HW-14), the scrubbers, the AXI master of `rmap0`, the pipeline reset
controller and the ECC monitors; TMR voting is not observable on the board.

## 8. Conclusion per core

The run passed every test case on the board, with the deviations of section 6, none of which failed a pass criterion.
It is the TRL 4 hardware evidence of each core for the functions that ran on the board:

| Core | Functions on the board |
| --- | --- |
| OpenFibre v0.1.5 | A SpaceFibre port of four lanes and two virtual channels at 6.25 Gbit/s per lane against an independent far end: link start, data and RMAP traffic at a sustained 9.2 Gbit/s of samples, far-end link reset, lane removal and rejoin, retry after a double memory error, management interface and error registers, ECC of its memories |
| OpenCompact v0.3.2 | Four CCSDS 123.0-B-2 channels: lossless with the sample-adaptive coder in the orders BIP and BSQ, lossless with the block-adaptive coder, near-lossless with an absolute error limit and the hybrid coder; runtime configuration and parameters, lost data, reset of an image in progress; every image equal to the model and decoded by the model decoder |
| OpenRMAP v0.1.2 | The RMAP target: write, read and verified commands with authorisation, the error statuses of the target (TC-RS-HW-03, 04 and 15), ECC of its memories |
| OpenSPP v0.2.1 | The Octet String Service on every port with one destination: CPTP encapsulation, Space Packet send and receive with the APID tables, sequence counts and continuity checks, discards of malformed packets, reset of the data path, ECC of its memories |
| Open Logic FT 4.8.0-ft.2 | The FIFOs, RAMs and scrubbers with ECC correction exercised in `ofb0`, `rmap0` and `spp0`, the clock-domain crossings of `ofb0`, the AXI master of `rmap0`, the pipeline reset controller and the ECC monitors |

The technology readiness level of each core is stated in its repository, with this report as its evidence.

## Appendix A. Records of the run

Rendered from `records_spfi_compress_20261125_155735.json` with `tools/render_records.py`, unchanged.

<!-- markdownlint-disable MD013 -->

### Run

| Item | Value |
| --- | --- |
| System | spfi_compress |
| Run | 2026-11-25 15:57:35 to 2026-11-25 16:07:15 |
| Target | device |
| Profile, seed | lab, 1 |
| Device image | spfi_compress_vck190_top.pdi, SHA-256 `79d67184c2569e59b39daf24ee2794af16e52f72260a7d454d03254629cbe58c` |

### Results per test case

23 test cases: 23 passed, 0 failed, 0 not run.

| Test case | Result | Error |
| --- | --- | --- |
| TC-RS-HW-01 | Pass |  |
| TC-RS-HW-02 | Pass |  |
| TC-RS-HW-03 | Pass |  |
| TC-RS-HW-04 | Pass |  |
| TC-RS-HW-05 | Pass |  |
| TC-RS-HW-06 | Pass |  |
| TC-RS-HW-07 | Pass |  |
| TC-RS-HW-08 | Pass |  |
| TC-RS-HW-09 | Pass |  |
| TC-RS-HW-10 | Pass |  |
| TC-RS-HW-11 | Pass |  |
| TC-RS-HW-12 | Pass |  |
| TC-RS-HW-14 | Pass |  |
| TC-RS-HW-15 | Pass |  |
| TC-RS-HW-16 | Pass |  |
| TC-RS-HW-19 | Pass |  |
| TC-RS-HW-20 | Pass |  |
| TC-RS-HW-21 | Pass |  |
| TC-RS-HW-22 | Pass |  |
| TC-RS-HW-23 | Pass |  |
| TC-RS-HW-13 | Pass |  |
| TC-RS-HW-17 | Pass |  |
| TC-RS-HW-18 | Pass |  |

### Requirements exercised per test case

A test case exercises these requirements of the cores on the board; its checks are its pass criteria. Every requirement is verified against its own criterion by the regression of its core.

| Test case | Result | Requirements |
| --- | --- | --- |
| TC-RS-HW-01 | Pass | OFB:LN-IF-01..06, OFB:LN-INIT-01..07/10/11, OFB:LN-TX-01..03, OFB:LN-RX-01..04/06/07, OFB:PA-IF-01..04, OFB:PA-SE-01/02, OFB:PA-SY-01, OFB:PA-CC-01, OFB:PA-CK-01, OFB:PA-RS-01..04, OFB:PA-ST-01..03, OFB:MG-IF, OFB:MG-CF-01..03, OFB:MG-PA-01..03, OFB:MG-ST-01/02/04..07, OFB:MG-VN-01, OFB:MG-ED-01..03 |
| TC-RS-HW-02 | Pass | OFB:MG-IF, OFB:MG-CF-01..03, OFB:MG-PA-01..03, OFB:MG-ST-01/02/04..07, OFB:MG-VN-01, OFB:MG-ED-01..03, ORMAP:MG-IF-01/02, ORMAP:MG-RF-01/03..05/07, OCP:MB-IF-02, OCP:MB-ID-01, OCP:MB-GN-01, OCP:MB-CN-01/02, OCP:MB-RC-01..05, OCP:MB-RP-01..04 |
| TC-RS-HW-03 | Pass | ORMAP:TG-IF-01..03, ORMAP:TG-RX-01, ORMAP:TG-RX-05, ORMAP:TG-TX-01..04, ORMAP:TG-EX-01/04, ORMAP:CO-IF-01, ORMAP:CO-DM-01/05, ORMAP:CO-MX-01, ORMAP:CO-NO-01, ORMAP:TG-AU-01..08, ORMAP:MG-IF-01/02, ORMAP:MG-RF-01/03..05/07 |
| TC-RS-HW-04 | Pass | ORMAP:MG-IF-01/02, ORMAP:MG-RF-01/03..05/07 |
| TC-RS-HW-05 | Pass | SPP:MG-TB-01/02, SPP:MG-CN-01/02, SPP:MG-CF-01..03, SPP:MG-EV-01, SPP:MG-FT-01/02 |
| TC-RS-HW-06 | Pass | OFB:LN-IF-01..06, OFB:LN-INIT-01..07/10/11, OFB:LN-TX-01..03, OFB:LN-RX-01..04/06/07, OFB:PA-IF-01..04, OFB:PA-SE-01/02, OFB:PA-SY-01, OFB:PA-CC-01, OFB:PA-CK-01, OFB:PA-RS-01..04, OFB:PA-ST-01..03, OFB:ML-IF, OFB:ML-LM-08/11/14..17, OFB:ML-DS-01..09/11, OFB:ML-ENC-01..06, OFB:ML-DEC-01..05, OFB:ML-AL-01..08/10, OFB:ML-CO-01..06, OFB:DL-IF-01/03..05, OFB:DL-VO-01/03..05, OFB:DL-CR-01, OFB:DL-MAC-01, OFB:DL-TS-01..03/05, OFB:DL-FA-01/03/04, OFB:DL-SQ-01/03, OFB:DL-ER-01/02/05/08/09, OFB:DL-WI-01/02/04, OFB:DL-SR, OFB:DL-RE-01/02, OFB:DL-VI-01/02/05/06, OFB:DL-LR-01/02, OFB:NI-IF-01/03, OFB:NI-TX-01/02, OFB:NI-RX-01, OFB:CORE-IF-01, OFB:CORE-CK-01/02, OFB:CORE-RS-01, OFB:CORE-CC-01, OFB:CORE-ML-01, OFB:CORE-PL-02/04..08, OFB:CORE-SY-01/04, SPP:RE-IF-01..03, SPP:RE-RC-01, SPP:RE-DM-01..03, SPP:RE-EX-01, SPP:EN-RX-01/02, SPP:EN-TX-01/02, SPP:EN-IF-02, SPP:SE-IF-01/02/04, SPP:SE-BF-01, SPP:SE-AS-01/02/04, OCP:PR-IF-01/03, OCP:PR-FR-01, OCP:PR-LS-02/04, OCP:PR-PD-03, OCP:PR-QU-01, OCP:PR-MP-01, OCP:PR-TP-01/02, OCP:PR-RC-01..03, OCP:EC-SA-01..03, OCP:EC-HY-01..04, OCP:EC-BA-01..03, OCP:EC-IF-01, OCP:EC-RC-01, OCP:CO-IF-01/02, OCP:CO-HD-01/02/04..08, OCP:CO-PK-01..03, OCP:CO-FR-02..05/08/09, OCP:CO-RC-01..03/05, OCP:CO-RP-01/03, OCP:IA-IF, OCP:IA-FN-01..05, OCP:IA-ER-01..03 |
| TC-RS-HW-07 | Pass | OCP:PR-IF-01/03, OCP:PR-FR-01, OCP:PR-LS-02/04, OCP:PR-PD-03, OCP:PR-QU-01, OCP:PR-MP-01, OCP:PR-TP-01/02, OCP:PR-RC-01..03, OCP:EC-SA-01..03, OCP:EC-HY-01..04, OCP:EC-BA-01..03, OCP:EC-IF-01, OCP:EC-RC-01, OCP:MB-IF-02, OCP:MB-ID-01, OCP:MB-GN-01, OCP:MB-CN-01/02, OCP:MB-RC-01..05, OCP:MB-RP-01..04 |
| TC-RS-HW-08 | Pass | SPP:RE-IF-01..03, SPP:RE-RC-01, SPP:RE-DM-01..03, SPP:RE-EX-01, OCP:PR-IF-01/03, OCP:PR-FR-01, OCP:PR-LS-02/04, OCP:PR-PD-03, OCP:PR-QU-01, OCP:PR-MP-01, OCP:PR-TP-01/02, OCP:PR-RC-01..03, OCP:EC-SA-01..03, OCP:EC-HY-01..04, OCP:EC-BA-01..03, OCP:EC-IF-01, OCP:EC-RC-01, OCP:CO-IF-01/02, OCP:CO-HD-01/02/04..08, OCP:CO-PK-01..03, OCP:CO-FR-02..05/08/09, OCP:CO-RC-01..03/05, OCP:CO-RP-01/03, OCP:IA-IF, OCP:IA-FN-01..05, OCP:IA-ER-01..03 |
| TC-RS-HW-09 | Pass | OCP:PR-IF-01/03, OCP:PR-FR-01, OCP:PR-LS-02/04, OCP:PR-PD-03, OCP:PR-QU-01, OCP:PR-MP-01, OCP:PR-TP-01/02, OCP:PR-RC-01..03, OCP:EC-SA-01..03, OCP:EC-HY-01..04, OCP:EC-BA-01..03, OCP:EC-IF-01, OCP:EC-RC-01 |
| TC-RS-HW-10 | Pass | OCP:PR-IF-01/03, OCP:PR-FR-01, OCP:PR-LS-02/04, OCP:PR-PD-03, OCP:PR-QU-01, OCP:PR-MP-01, OCP:PR-TP-01/02, OCP:PR-RC-01..03, OCP:EC-SA-01..03, OCP:EC-HY-01..04, OCP:EC-BA-01..03, OCP:EC-IF-01, OCP:EC-RC-01, OCP:PR-RP-01, OCP:PR-RP-02, OCP:EC-RP-01, OCP:CO-IF-01/02, OCP:CO-HD-01/02/04..08, OCP:CO-PK-01..03, OCP:CO-FR-02..05/08/09, OCP:CO-RC-01..03/05, OCP:CO-RP-01/03, OCP:CH-IF-01..04, OCP:CH-FN-01..03, OCP:CH-RC-01, OCP:CH-RP-01, OCP:MB-IF-02, OCP:MB-ID-01, OCP:MB-GN-01, OCP:MB-CN-01/02, OCP:MB-RC-01..05, OCP:MB-RP-01..04 |
| TC-RS-HW-11 | Pass | OFB:MG-IF, OFB:MG-CF-01..03, OFB:MG-PA-01..03, OFB:MG-ST-01/02/04..07, OFB:MG-VN-01, OFB:MG-ED-01..03 |
| TC-RS-HW-12 | Pass | OFB:LN-INIT-03, OFB:DL-LR-01..05, OFB:DL-VO-02, OFB:DL-VI-04, OFB:DL-ER-06, OFB:DL-FA-05, OFB:PA-RS-04, OFB:MG-IF, OFB:MG-CF-01..03, OFB:MG-PA-01..03, OFB:MG-ST-01/02/04..07, OFB:MG-VN-01, OFB:MG-ED-01..03 |
| TC-RS-HW-13 | Pass | OFB:LN-INIT-03, OFB:ML-LM-11..14, OFB:ML-AL-07..09, OFB:PA-RS-04, OFB:CORE-SY-02 |
| TC-RS-HW-14 | Pass | OFB:DL-RE-03, OFB:DL-ER-03, OFB:DL-SQ-02, OFB:CORE-ED-01/02, ORMAP:TG-EX-06, ORMAP:MG-ED-01/02, SPP:RE-FT-01, SPP:SE-AS-05, OFB:MG-IF, OFB:MG-CF-01..03, OFB:MG-PA-01..03, OFB:MG-ST-01/02/04..07, OFB:MG-VN-01, OFB:MG-ED-01..03, SPP:MG-TB-01/02, SPP:MG-CN-01/02, SPP:MG-CF-01..03, SPP:MG-EV-01, SPP:MG-FT-01/02 |
| TC-RS-HW-15 | Pass | ORMAP:TG-RX-02/03/06, ORMAP:TG-TX-02, ORMAP:TG-AU-01..08, ORMAP:TG-EX-02/03/05/07/08, ORMAP:TG-EX-06, ORMAP:CO-DM-04, ORMAP:MG-IF-01/02, ORMAP:MG-RF-01/03..05/07 |
| TC-RS-HW-16 | Pass | SPP:RE-RC-02, SPP:RE-DM-01, SPP:RE-DM-02, SPP:EN-RX-03, SPP:SE-MX-01, SPP:CO-SY-01/02/04/08, SPP:TB-FN-01, SPP:MG-TB-01/02, SPP:MG-CN-01/02, SPP:MG-CF-01..03, SPP:MG-EV-01, SPP:MG-FT-01/02 |
| TC-RS-HW-17 | Pass | OFB:MG-IF, OFB:MG-CF-01..03, OFB:MG-PA-01..03, OFB:MG-ST-01/02/04..07, OFB:MG-VN-01, OFB:MG-ED-01..03 |
| TC-RS-HW-19 | Pass | SPP:CO-RS-01/02/12, OCP:CO-RS-01, OCP:CH-RS-01, OCP:MB-RS-01, SPP:MG-TB-01/02, SPP:MG-CN-01/02, SPP:MG-CF-01..03, SPP:MG-EV-01, SPP:MG-FT-01/02, OCP:MB-IF-02, OCP:MB-ID-01, OCP:MB-GN-01, OCP:MB-CN-01/02, OCP:MB-RC-01..05, OCP:MB-RP-01..04 |
| TC-RS-HW-20 | Pass | SPP:CO-RS-01/02/12, SPP:CO-RS-09..11, OCP:CO-RS-01, OCP:CH-RS-01, OCP:IA-ER-04, OCP:MB-RS-01 |
| TC-RS-HW-21 | Pass | OCP:CH-RS-01, OCP:MB-RS-01, OCP:MB-IF-02, OCP:MB-ID-01, OCP:MB-GN-01, OCP:MB-CN-01/02, OCP:MB-RC-01..05, OCP:MB-RP-01..04 |
| TC-RS-HW-22 | Pass | SPP:CO-RS-01/02/12, SPP:CO-RS-09..11, OCP:CO-RS-01, OCP:CH-RS-01, OCP:IA-ER-04, SPP:MG-TB-01/02, SPP:MG-CN-01/02, SPP:MG-CF-01..03, SPP:MG-EV-01, SPP:MG-FT-01/02 |
| TC-RS-HW-23 | Pass | SPP:CO-RS-01/02/12, OCP:CH-RS-01, OCP:MB-RS-01, OCP:MB-IF-02, OCP:MB-ID-01, OCP:MB-GN-01, OCP:MB-CN-01/02, OCP:MB-RC-01..05, OCP:MB-RP-01..04 |

### Recorded values

#### TC-RS-HW-01

- ofb0 lane 0: RX_RESETS 6, initialisation timeouts 5, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- ofb0 lane 1: RX_RESETS 6, initialisation timeouts 5, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- ofb0 lane 2: RX_RESETS 6, initialisation timeouts 5, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- ofb0 lane 3: RX_RESETS 6, initialisation timeouts 5, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- pipeline reset at the start of the run: prst0 STATUS.COUNT 1, BUSY 0 at poll 1
- error registers after link-up, before the baseline clears them (no pass criterion): rmap0.EVENTS 0x00000004, ofb0.VC_BW_UNDER 0x00000003, ofb0.ML_MISALIGNED 0x00000003, ofb0.ML_ALIGN_TIMEOUTS 0x00000001, ofb0.LANE_EVENTS(0) 0x00000042, ofb0.LANE_REASONS(0) 0x00004F4F, ofb0.LANE_TIMEOUT_COUNT(0) 0x00060005, ofb0.LANE_EVENTS(1) 0x0000004A, ofb0.LANE_REASONS(1) 0x00001A1A, ofb0.LANE_TIMEOUT_COUNT(1) 0x00060005, ofb0.LANE_EVENTS(2) 0x00000043, ofb0.LANE_REASONS(2) 0x00005757, ofb0.LANE_TIMEOUT_COUNT(2) 0x00060005, ofb0.LANE_EVENTS(3) 0x0000004A, ofb0.LANE_REASONS(3) 0x0000AEAE, ofb0.LANE_TIMEOUT_COUNT(3) 0x00060005

#### TC-RS-HW-02

- ocp0 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 0, REL 0, user 0, m 48, tinc 6, vmin -1, vmax 3, umax 18, g0 1, gstar 6, k 4, r 0
- ocp3 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 12, REL 0, user 0, m 1, tinc 6, vmin -1, vmax 3, umax 16, g0 2, gstar 6, k 0, r 0

#### TC-RS-HW-03

- TC-RS-HW-03 error registers: expected rmap0.TGT_CNT_ERR2 0x00000001, rmap0.EVENTS 0x00000005; allowed to move: none; others: ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00007A7A, ofb0.LANE_REASONS(1) 0x00008585, ofb0.LANE_REASONS(2) 0x00001717, ofb0.LANE_REASONS(3) 0x00001B1B

#### TC-RS-HW-05

- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-06

- model decoder: 20 compressed images decode to the images sent (lossless: equal; near-lossless: within the maximum error)

#### TC-RS-HW-09

- rate: 32 images, 12582912 bytes of samples, 7039684 bytes compressed; transfer 0.011 s from the first submission to the last packet received: 9.184 Gbit/s of samples, 9.196 Gbit/s of link packets to the system and 5.151 Gbit/s from it. Not timed: the images and packets built before, the conversion of the 1736 packets received (0.115 s) and the checks after

#### TC-RS-HW-10

- model decoder: 46 compressed images decode to the images sent (lossless: equal; near-lossless: within the maximum error)

#### TC-RS-HW-12

- far-end link reset: SpaceFibre port 1 of the device: 4 active lanes before the link reset, 0 and link not ready while it was held for 0.100 s, ready with 4 lanes 0.014 s after its release
- after the far-end link reset, before the images: ofb0 DL_ERRORS 0x00000020 (FAR_END_LINK_RESET 1)
- after the far-end link reset: ofb0 lane 0: RX_RESETS 39, initialisation timeouts 36, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the far-end link reset: ofb0 lane 1: RX_RESETS 37, initialisation timeouts 35, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the far-end link reset: ofb0 lane 2: RX_RESETS 37, initialisation timeouts 35, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the far-end link reset: ofb0 lane 3: RX_RESETS 37, initialisation timeouts 35, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- TC-RS-HW-12 error registers: expected ofb0.DL_ERRORS 0x00000020; allowed to move: ofb0.LANE_EVENTS(0) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(0) 0x00270024, ofb0.LANE_EVENTS(1) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(1) 0x00250023, ofb0.LANE_EVENTS(2) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(2) 0x00250023, ofb0.LANE_EVENTS(3) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(3) 0x00250023, ofb0.ML_MISALIGNED 0x00000006, ofb0.ML_ALIGN_TIMEOUTS 0x00000001; others: spp0.EVENTS 0x0000001C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00002727, ofb0.LANE_REASONS(1) 0x00005858, ofb0.LANE_REASONS(2) 0x00001111, ofb0.LANE_REASONS(3) 0x00000D0D

#### TC-RS-HW-14

- TC-RS-HW-14 single errors error registers: expected ofb0.ECC_COUNT(2) 0x00000001, ofb0.ECC_COUNT(6) 0x00000001, ofb0.ECC_COUNT(7) 0x00000001, ofb0.ECC_COUNT(8) 0x00000001, rmap0.ECC_COUNT(0) 0x00000001, rmap0.ECC_COUNT(1) 0x00000001, rmap0.ECC_COUNT(2) 0x00000001, spp0.ECC_COUNT(0) 0x00000001, spp0.ECC_COUNT(1) 0x00000001, spp0.ECC_COUNT(4) 0x00000001, spp0.ECC_COUNT(5) 0x00000001, ofb0.ECC_STATUS 0x00010000, rmap0.EVENTS 0x00000044, spp0.EVENTS 0x0000003C; allowed to move: ofb0.ECC_COUNT(0) 0x00000002, ofb0.ECC_COUNT(1) 0x00000002, ofb0.ECC_COUNT(3) 0x00000002, spp0.ECC_COUNT(2) 0x00000001, spp0.ECC_COUNT(7) 0x00000001; others: ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000A9A9, ofb0.LANE_REASONS(1) 0x00005151, ofb0.LANE_REASONS(2) 0x00002F2F, ofb0.LANE_REASONS(3) 0x0000A9A9
- TC-RS-HW-14 double errors error registers: expected ofb0.ECC_COUNT(6) 0x00010000, ofb0.ECC_COUNT(7) 0x00010000, ofb0.ECC_STATUS 0x000000C0, rmap0.ECC_COUNT(0) 0x00010000, rmap0.ECC_STATUS 0x00000001, rmap0.TGT_CNT_ERR1 0x00000001, rmap0.EVENTS 0x00000085, spp0.ECC_COUNT(5) 0x00010000, spp0.ECC_STATUS 0x00000020, spp0.ERR_RX6 0x00010000, spp0.EVENTS 0x0000005E; allowed to move: ofb0.DL_RETRIES 0x00000001; others: ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00005757, ofb0.LANE_REASONS(1) 0x0000D9D9, ofb0.LANE_REASONS(2) 0x00005B5B, ofb0.LANE_REASONS(3) 0x0000F1F1

#### TC-RS-HW-15

- TC-RS-HW-15 error registers: expected rmap0.TGT_CNT_ERR6 0x00010002, rmap0.TGT_CNT_ERR2 0x00020001, rmap0.TGT_CNT_ERR1 0x00020001, rmap0.TGT_CNT_ERR3 0x00010001, rmap0.TGT_CNT_ERR4 0x00010000, rmap0.TGT_CNT_ERR5 0x00010003, rmap0.TGT_CNT_HDR 0x00010001, rmap0.PKT_CNT 0x00010000, rmap0.EVENTS 0x00000107; allowed to move: none; others: spp0.EVENTS 0x0000001C, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000DFDF, ofb0.LANE_REASONS(1) 0x00000404, ofb0.LANE_REASONS(2) 0x00006565, ofb0.LANE_REASONS(3) 0x00008A8A

#### TC-RS-HW-16

- TC-RS-HW-16 error registers: expected spp0.ERR_RX1 0x00010000, spp0.ERR_RX2 0x00000001, spp0.ERR_RX3 0x00010001, spp0.ERR_RX4 0x00010001, spp0.ERR_RX5 0x00010001, spp0.ERR_RX6 0x00000001, spp0.EVENTS 0x0000001E; allowed to move: none; others: rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00004141, ofb0.LANE_REASONS(1) 0x0000FAFA, ofb0.LANE_REASONS(2) 0x0000DCDC, ofb0.LANE_REASONS(3) 0x00003636

#### TC-RS-HW-19

- before the pipeline reset: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- before the pipeline reset: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- before the pipeline reset: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- before the pipeline reset: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- pipeline reset of TC-RS-HW-19: prst0 STATUS.COUNT 2, BUSY 0 at poll 1
- after the pipeline reset: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- outside the processing path after the pipeline reset: rmap0 TGT_CNT_OK 0x072E0424 (0x068B0400 before), error registers rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00003E3E, ofb0.LANE_REASONS(1) 0x0000E2E2, ofb0.LANE_REASONS(2) 0x00008282, ofb0.LANE_REASONS(3) 0x00000505
- pipeline reset of TC-RS-HW-19, second command: prst0 STATUS.COUNT 3, BUSY 0 at poll 1
- after the pipeline reset: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- outside the processing path after the pipeline reset: rmap0 TGT_CNT_OK 0x07C70442 (0x068B0400 before), error registers rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00003232, ofb0.LANE_REASONS(1) 0x0000B1B1, ofb0.LANE_REASONS(2) 0x00004444, ofb0.LANE_REASONS(3) 0x0000D9D9
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-20

- before the pipeline reset: APID 0: 32 Space Packets equal to the model, of 32 that the data sent can give
- before the pipeline reset: APID 1: 30 Space Packets equal to the model, of 30 that the data sent can give
- before the pipeline reset: APID 2: 30 Space Packets equal to the model, of 30 that the data sent can give
- before the pipeline reset: APID 3: 18 Space Packets equal to the model, of 18 that the data sent can give
- pipeline reset of TC-RS-HW-20: prst0 STATUS.COUNT 4, BUSY 0 at poll 1
- after the pipeline reset: APID 0: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: APID 1: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: APID 2: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: APID 3: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: no Space Packet cut by the pipeline reset
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-21

- ocp0 before the pipeline reset: NX 32, NY 64, NZ 48, ABS 0, REL 0, user 0, m 48, tinc 6, vmin -1, vmax 3, umax 18, g0 1, gstar 6, k 4, r 0 pending, COMMIT with NEXT_X 0x00000001 refused (CFG_ERROR)
- ocp3 before the pipeline reset: NX 32, NY 64, NZ 48, ABS 12, REL 0, user 0, m 1, tinc 6, vmin -1, vmax 3, umax 16, g0 2, gstar 6, k 0, r 0 pending, COMMIT with NEXT_X 0x00000041 refused (CFG_ERROR)
- pipeline reset of TC-RS-HW-21: prst0 STATUS.COUNT 5, BUSY 0 at poll 1
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-22

- TC-RS-HW-22: the pipeline reset after 4 of 196 SDUs
- pipeline reset of TC-RS-HW-22, during traffic: prst0 STATUS.COUNT 6, BUSY 0 at poll 1
- TC-RS-HW-22: APID 0: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: APID 1: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: APID 2: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: APID 3: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: no Space Packet cut by the pipeline reset
- after the pipeline reset during traffic: spp0 RX_PACKETS 0, TX_PACKETS 0, RX_LOSS 0
- pipeline reset of TC-RS-HW-22, second command: prst0 STATUS.COUNT 7, BUSY 0 at poll 1
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0
- TC-RS-HW-22 error registers: expected none; allowed to move: none; others: spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000FCFC, ofb0.LANE_REASONS(1) 0x00001313, ofb0.LANE_REASONS(2) 0x0000D4D4, ofb0.LANE_REASONS(3) 0x00007171

#### TC-RS-HW-23

- pipeline reset of TC-RS-HW-23: prst0 STATUS.COUNT 8, BUSY 0 at poll 1

#### TC-RS-HW-23 TC-RS-HW-02

- ocp0 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 0, REL 0, user 0, m 48, tinc 6, vmin -1, vmax 3, umax 18, g0 1, gstar 6, k 4, r 0
- ocp3 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 12, REL 0, user 0, m 1, tinc 6, vmin -1, vmax 3, umax 16, g0 2, gstar 6, k 0, r 0

#### TC-RS-HW-23 TC-RS-HW-03

- TC-RS-HW-03 error registers: expected rmap0.TGT_CNT_ERR2 0x00000001, rmap0.EVENTS 0x00000005; allowed to move: none; others: ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000CCCC, ofb0.LANE_REASONS(1) 0x00007E7E, ofb0.LANE_REASONS(2) 0x00008484, ofb0.LANE_REASONS(3) 0x00009090

#### TC-RS-HW-23 TC-RS-HW-05

- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-23 TC-RS-HW-06

- model decoder: 20 compressed images decode to the images sent (lossless: equal; near-lossless: within the maximum error)

#### TC-RS-HW-13

- after the rejoin: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the rejoin: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the rejoin: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the rejoin: ofb0 lane 3: RX_RESETS 11, initialisation timeouts 10, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- TC-RS-HW-13 error registers: expected none; allowed to move: ofb0.LANE_EVENTS(3) 0x00000042, ofb0.LANE_TIMEOUT_COUNT(3) 0x000B000A, ofb0.ML_MISALIGNED 0x00000002; others: spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00000E0E, ofb0.LANE_REASONS(1) 0x0000C1C1, ofb0.LANE_REASONS(2) 0x0000D7D7, ofb0.LANE_REASONS(3) 0x00004C4C

#### TC-RS-HW-17

- TC-RS-HW-09: 32 images, 12582912 bytes of samples, 7039684 bytes compressed; transfer 0.011 s from the first submission to the last packet received: 9.184 Gbit/s of samples, 9.196 Gbit/s of link packets to the system and 5.151 Gbit/s from it. Not timed: the images and packets built before, the conversion of the 1736 packets received (0.115 s) and the checks after
- error registers at the end of the run: spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000F9F9, ofb0.LANE_REASONS(1) 0x00002020, ofb0.LANE_REASONS(2) 0x0000BDBD, ofb0.LANE_REASONS(3) 0x00005454
- end of the run: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- end of the run: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- end of the run: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- end of the run: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0

#### TC-RS-HW-18 TC-RS-HW-01

- ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- pipeline reset at the start of the run: skipped
- error registers after link-up, before the baseline clears them (no pass criterion): spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00001010, ofb0.LANE_REASONS(1) 0x00004747, ofb0.LANE_REASONS(2) 0x00006868, ofb0.LANE_REASONS(3) 0x00002B2B

#### TC-RS-HW-18 TC-RS-HW-02

- prst0 STATUS.COUNT 8, no pipeline reset in this run before TC-RS-HW-02
- ocp0 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 0, REL 0, user 0, m 48, tinc 6, vmin -1, vmax 3, umax 18, g0 1, gstar 6, k 4, r 0
- ocp3 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 12, REL 0, user 0, m 1, tinc 6, vmin -1, vmax 3, umax 16, g0 2, gstar 6, k 0, r 0

#### TC-RS-HW-18 TC-RS-HW-03

- TC-RS-HW-03 error registers: expected rmap0.TGT_CNT_ERR2 0x00000001, rmap0.EVENTS 0x00000005; allowed to move: none; others: spp0.EVENTS 0x0000000C, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00006F6F, ofb0.LANE_REASONS(1) 0x00000C0C, ofb0.LANE_REASONS(2) 0x00000C0C, ofb0.LANE_REASONS(3) 0x00002626

#### TC-RS-HW-18 TC-RS-HW-05

- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 374
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 380
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 361
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 244

#### TC-RS-HW-18 TC-RS-HW-06

- model decoder: 20 compressed images decode to the images sent (lossless: equal; near-lossless: within the maximum error)

#### TC-RS-HW-18 TC-RS-HW-09

- rate: 32 images, 12582912 bytes of samples, 7039684 bytes compressed; transfer 0.011 s from the first submission to the last packet received: 9.121 Gbit/s of samples, 9.132 Gbit/s of link packets to the system and 5.115 Gbit/s from it. Not timed: the images and packets built before, the conversion of the 1736 packets received (0.109 s) and the checks after

#### TC-RS-HW-18 TC-RS-HW-10

- model decoder: 46 compressed images decode to the images sent (lossless: equal; near-lossless: within the maximum error)

#### TC-RS-HW-18 TC-RS-HW-12

- far-end link reset: SpaceFibre port 1 of the device: 4 active lanes before the link reset, 0 and link not ready while it was held for 0.100 s, ready with 4 lanes 0.018 s after its release
- after the far-end link reset, before the images: ofb0 DL_ERRORS 0x00000020 (FAR_END_LINK_RESET 1)
- after the far-end link reset: ofb0 lane 0: RX_RESETS 40, initialisation timeouts 37, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the far-end link reset: ofb0 lane 1: RX_RESETS 40, initialisation timeouts 37, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the far-end link reset: ofb0 lane 2: RX_RESETS 40, initialisation timeouts 37, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the far-end link reset: ofb0 lane 3: RX_RESETS 40, initialisation timeouts 37, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- TC-RS-HW-12 error registers: expected ofb0.DL_ERRORS 0x00000020; allowed to move: ofb0.LANE_EVENTS(0) 0x00000042, ofb0.LANE_TIMEOUT_COUNT(0) 0x00280025, ofb0.LANE_EVENTS(1) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(1) 0x00280025, ofb0.LANE_EVENTS(2) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(2) 0x00280025, ofb0.LANE_EVENTS(3) 0x00000043, ofb0.LANE_TIMEOUT_COUNT(3) 0x00280025, ofb0.ML_MISALIGNED 0x00000004, ofb0.ML_ALIGN_TIMEOUTS 0x00000001; others: spp0.EVENTS 0x0000001C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00005A5A, ofb0.LANE_REASONS(1) 0x00002525, ofb0.LANE_REASONS(2) 0x00007B7B, ofb0.LANE_REASONS(3) 0x0000D0D0

#### TC-RS-HW-18 TC-RS-HW-14

- TC-RS-HW-14 single errors error registers: expected ofb0.ECC_COUNT(2) 0x00000001, ofb0.ECC_COUNT(6) 0x00000001, ofb0.ECC_COUNT(7) 0x00000001, ofb0.ECC_COUNT(8) 0x00000001, rmap0.ECC_COUNT(0) 0x00000001, rmap0.ECC_COUNT(1) 0x00000001, rmap0.ECC_COUNT(2) 0x00000001, spp0.ECC_COUNT(0) 0x00000001, spp0.ECC_COUNT(1) 0x00000001, spp0.ECC_COUNT(4) 0x00000001, spp0.ECC_COUNT(5) 0x00000001, ofb0.ECC_STATUS 0x00010000, rmap0.EVENTS 0x00000044, spp0.EVENTS 0x0000003C; allowed to move: ofb0.ECC_COUNT(0) 0x00000002, ofb0.ECC_COUNT(1) 0x00000002, ofb0.ECC_COUNT(3) 0x00000002, spp0.ECC_COUNT(3) 0x00000001, spp0.ECC_COUNT(7) 0x00000001; others: ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00006767, ofb0.LANE_REASONS(1) 0x00005C5C, ofb0.LANE_REASONS(2) 0x00001B1B, ofb0.LANE_REASONS(3) 0x00003737
- TC-RS-HW-14 double errors error registers: expected ofb0.ECC_COUNT(6) 0x00010000, ofb0.ECC_COUNT(7) 0x00010000, ofb0.ECC_STATUS 0x000000C0, rmap0.ECC_COUNT(0) 0x00010000, rmap0.ECC_STATUS 0x00000001, rmap0.TGT_CNT_ERR1 0x00000001, rmap0.EVENTS 0x00000085, spp0.ECC_COUNT(5) 0x00010000, spp0.ECC_STATUS 0x00000020, spp0.ERR_RX6 0x00010000, spp0.EVENTS 0x0000005E; allowed to move: ofb0.DL_RETRIES 0x00000001; others: ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000E4E4, ofb0.LANE_REASONS(1) 0x00002626, ofb0.LANE_REASONS(2) 0x00003434, ofb0.LANE_REASONS(3) 0x00009393

#### TC-RS-HW-18 TC-RS-HW-15

- TC-RS-HW-15 error registers: expected rmap0.TGT_CNT_ERR6 0x00010002, rmap0.TGT_CNT_ERR2 0x00020001, rmap0.TGT_CNT_ERR1 0x00020001, rmap0.TGT_CNT_ERR3 0x00010001, rmap0.TGT_CNT_ERR4 0x00010000, rmap0.TGT_CNT_ERR5 0x00010003, rmap0.TGT_CNT_HDR 0x00010001, rmap0.PKT_CNT 0x00010000, rmap0.EVENTS 0x00000107; allowed to move: none; others: spp0.EVENTS 0x0000001C, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00009595, ofb0.LANE_REASONS(1) 0x0000C3C3, ofb0.LANE_REASONS(2) 0x00000707, ofb0.LANE_REASONS(3) 0x00002121

#### TC-RS-HW-18 TC-RS-HW-16

- TC-RS-HW-16 error registers: expected spp0.ERR_RX1 0x00010000, spp0.ERR_RX2 0x00000001, spp0.ERR_RX3 0x00010001, spp0.ERR_RX4 0x00010001, spp0.ERR_RX5 0x00010001, spp0.ERR_RX6 0x00000001, spp0.EVENTS 0x0000001E; allowed to move: none; others: rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000CACA, ofb0.LANE_REASONS(1) 0x00002B2B, ofb0.LANE_REASONS(2) 0x00009999, ofb0.LANE_REASONS(3) 0x00007777

#### TC-RS-HW-18 TC-RS-HW-19

- before the pipeline reset: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- before the pipeline reset: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- before the pipeline reset: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- before the pipeline reset: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- pipeline reset of TC-RS-HW-19: prst0 STATUS.COUNT 9, BUSY 0 at poll 1
- after the pipeline reset: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- outside the processing path after the pipeline reset: rmap0 TGT_CNT_OK 0x13310ADA (0x128E0AB6 before), error registers rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00002626, ofb0.LANE_REASONS(1) 0x00003333, ofb0.LANE_REASONS(2) 0x0000FBFB, ofb0.LANE_REASONS(3) 0x00002121
- pipeline reset of TC-RS-HW-19, second command: prst0 STATUS.COUNT 10, BUSY 0 at poll 1
- after the pipeline reset: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the pipeline reset: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- outside the processing path after the pipeline reset: rmap0 TGT_CNT_OK 0x13CA0AF8 (0x128E0AB6 before), error registers rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00004C4C, ofb0.LANE_REASONS(1) 0x0000CCCC, ofb0.LANE_REASONS(2) 0x00001B1B, ofb0.LANE_REASONS(3) 0x00002A2A
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-18 TC-RS-HW-20

- before the pipeline reset: APID 0: 32 Space Packets equal to the model, of 32 that the data sent can give
- before the pipeline reset: APID 1: 30 Space Packets equal to the model, of 30 that the data sent can give
- before the pipeline reset: APID 2: 30 Space Packets equal to the model, of 30 that the data sent can give
- before the pipeline reset: APID 3: 18 Space Packets equal to the model, of 18 that the data sent can give
- pipeline reset of TC-RS-HW-20: prst0 STATUS.COUNT 11, BUSY 0 at poll 1
- after the pipeline reset: APID 0: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: APID 1: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: APID 2: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: APID 3: 0 Space Packets equal to the model, of 0 that the data sent can give
- after the pipeline reset: no Space Packet cut by the pipeline reset
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-18 TC-RS-HW-21

- ocp0 before the pipeline reset: NX 32, NY 64, NZ 48, ABS 0, REL 0, user 0, m 48, tinc 6, vmin -1, vmax 3, umax 18, g0 1, gstar 6, k 4, r 0 pending, COMMIT with NEXT_X 0x00000001 refused (CFG_ERROR)
- ocp3 before the pipeline reset: NX 32, NY 64, NZ 48, ABS 12, REL 0, user 0, m 1, tinc 6, vmin -1, vmax 3, umax 16, g0 2, gstar 6, k 0, r 0 pending, COMMIT with NEXT_X 0x00000041 refused (CFG_ERROR)
- pipeline reset of TC-RS-HW-21: prst0 STATUS.COUNT 12, BUSY 0 at poll 1
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-18 TC-RS-HW-22

- TC-RS-HW-22: the pipeline reset after 4 of 196 SDUs
- pipeline reset of TC-RS-HW-22, during traffic: prst0 STATUS.COUNT 13, BUSY 0 at poll 1
- TC-RS-HW-22: APID 0: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: APID 1: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: APID 2: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: APID 3: 1 Space Packets equal to the model, of 1 that the data sent can give
- TC-RS-HW-22: no Space Packet cut by the pipeline reset
- after the pipeline reset during traffic: spp0 RX_PACKETS 0, TX_PACKETS 0, RX_LOSS 0
- pipeline reset of TC-RS-HW-22, second command: prst0 STATUS.COUNT 14, BUSY 0 at poll 1
- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0
- TC-RS-HW-22 error registers: expected none; allowed to move: none; others: spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00002C2C, ofb0.LANE_REASONS(1) 0x00008080, ofb0.LANE_REASONS(2) 0x00003F3F, ofb0.LANE_REASONS(3) 0x00001313

#### TC-RS-HW-18 TC-RS-HW-23

- pipeline reset of TC-RS-HW-23: prst0 STATUS.COUNT 15, BUSY 0 at poll 1

#### TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-02

- ocp0 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 0, REL 0, user 0, m 48, tinc 6, vmin -1, vmax 3, umax 18, g0 1, gstar 6, k 4, r 0
- ocp3 at the start of the run: IMAGE_X to LIMITS show the values of the generics: NX 64, NY 64, NZ 48, ABS 12, REL 0, user 0, m 1, tinc 6, vmin -1, vmax 3, umax 16, g0 2, gstar 6, k 0, r 0

#### TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-03

- TC-RS-HW-03 error registers: expected rmap0.TGT_CNT_ERR2 0x00000001, rmap0.EVENTS 0x00000005; allowed to move: none; others: ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x0000AFAF, ofb0.LANE_REASONS(1) 0x00004E4E, ofb0.LANE_REASONS(2) 0x00002020, ofb0.LANE_REASONS(3) 0x0000DFDF

#### TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-05

- spp0 sends the next Space Packet of APID 0 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 1 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 2 with Packet Sequence Count 0
- spp0 sends the next Space Packet of APID 3 with Packet Sequence Count 0

#### TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-06

- model decoder: 20 compressed images decode to the images sent (lossless: equal; near-lossless: within the maximum error)

#### TC-RS-HW-18 TC-RS-HW-13

- after the rejoin: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the rejoin: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the rejoin: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- after the rejoin: ofb0 lane 3: RX_RESETS 9, initialisation timeouts 8, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- TC-RS-HW-13 error registers: expected none; allowed to move: ofb0.LANE_EVENTS(3) 0x00000042, ofb0.LANE_TIMEOUT_COUNT(3) 0x00090008, ofb0.ML_MISALIGNED 0x00000002; others: spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_OVER 0x00000002, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00002F2F, ofb0.LANE_REASONS(1) 0x00003E3E, ofb0.LANE_REASONS(2) 0x00004646, ofb0.LANE_REASONS(3) 0x00003939

#### TC-RS-HW-18 TC-RS-HW-17

- TC-RS-HW-09: 32 images, 12582912 bytes of samples, 7039684 bytes compressed; transfer 0.011 s from the first submission to the last packet received: 9.121 Gbit/s of samples, 9.132 Gbit/s of link packets to the system and 5.115 Gbit/s from it. Not timed: the images and packets built before, the conversion of the 1736 packets received (0.109 s) and the checks after
- error registers at the end of the run: spp0.EVENTS 0x0000000C, rmap0.EVENTS 0x00000004, ofb0.VC_BW_UNDER 0x00000003, ofb0.LANE_REASONS(0) 0x00009F9F, ofb0.LANE_REASONS(1) 0x00006D6D, ofb0.LANE_REASONS(2) 0x0000FFFF, ofb0.LANE_REASONS(3) 0x0000D6D6
- end of the run: ofb0 lane 0: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- end of the run: ofb0 lane 1: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- end of the run: ofb0 lane 2: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0
- end of the run: ofb0 lane 3: RX_RESETS 0, initialisation timeouts 0, RXRESETDONE 1, receiver reset in progress 0, CDR lock 0, receiver electrical idle 0

### Compressed images

| Test case | Channel | Images | Samples | Compressed bytes | Bits per sample | Decoding check |
| --- | --- | --- | --- | --- | --- | --- |
| TC-RS-HW-06 | ocp0 | 5 | 983040 | 1013156 | 8.245 | 5 equal |
| TC-RS-HW-06 | ocp1 | 5 | 983040 | 1045152 | 8.505 | 5 equal |
| TC-RS-HW-06 | ocp2 | 5 | 983040 | 957104 | 7.789 | 5 equal |
| TC-RS-HW-06 | ocp3 | 5 | 983040 | 664264 | 5.406 | 5 within the maximum error |
| TC-RS-HW-08 | ocp0 | 4 (1 aborted) | 589824 | 766792 | 10.400 | 1 aborted, not decoded, 3 equal |
| TC-RS-HW-08 | ocp1 | 4 (1 aborted) | 589824 | 735740 | 9.979 | 1 aborted, not decoded, 3 equal |
| TC-RS-HW-08 | ocp2 | 4 (1 aborted) | 589824 | 773816 | 10.496 | 1 aborted, not decoded, 3 equal |
| TC-RS-HW-08 | ocp3 | 4 (1 aborted) | 589824 | 474176 | 6.431 | 1 aborted, not decoded, 3 within the maximum error |
| TC-RS-HW-09 | ocp0 | 8 | 1572864 | 1925912 | 9.796 | 8 equal |
| TC-RS-HW-09 | ocp1 | 8 | 1572864 | 1962344 | 9.981 | 8 equal |
| TC-RS-HW-09 | ocp2 | 8 | 1572864 | 1866800 | 9.495 | 8 equal |
| TC-RS-HW-09 | ocp3 | 8 | 1572864 | 1284628 | 6.534 | 8 within the maximum error |
| TC-RS-HW-10 | ocp0 | 22 | 1130025 | 1462840 | 10.356 | 22 equal |
| TC-RS-HW-10 | ocp3 | 22 | 917401 | 643116 | 5.608 | 22 within the maximum error |
| TC-RS-HW-10 | ocp1 | 1 | 196608 | 246672 | 10.037 | 1 equal |
| TC-RS-HW-10 | ocp2 | 1 | 196608 | 248364 | 10.106 | 1 equal |
| TC-RS-HW-12 | ocp0 | 1 | 196608 | 250292 | 10.184 | 1 equal |
| TC-RS-HW-12 | ocp1 | 1 | 196608 | 250892 | 10.209 | 1 equal |
| TC-RS-HW-12 | ocp2 | 1 | 196608 | 254528 | 10.357 | 1 equal |
| TC-RS-HW-12 | ocp3 | 1 | 196608 | 133344 | 5.426 | 1 within the maximum error |
| TC-RS-HW-14 | ocp0 | 3 | 589824 | 743136 | 10.079 | 3 equal |
| TC-RS-HW-14 | ocp1 | 3 | 589824 | 753640 | 10.222 | 3 equal |
| TC-RS-HW-14 | ocp2 | 3 | 589824 | 770560 | 10.451 | 3 equal |
| TC-RS-HW-14 | ocp3 | 3 | 589824 | 444960 | 6.035 | 3 within the maximum error |
| TC-RS-HW-16 | ocp0 | 1 | 196608 | 256192 | 10.424 | 1 equal |
| TC-RS-HW-16 | ocp1 | 1 | 196608 | 262656 | 10.688 | 1 equal |
| TC-RS-HW-16 | ocp2 | 1 | 196608 | 259292 | 10.551 | 1 equal |
| TC-RS-HW-16 | ocp3 | 1 | 196608 | 163708 | 6.661 | 1 within the maximum error |
| TC-RS-HW-19 | ocp0 | 2 | 393216 | 522428 | 10.629 | 2 equal |
| TC-RS-HW-19 | ocp1 | 2 | 393216 | 495240 | 10.076 | 2 equal |
| TC-RS-HW-19 | ocp2 | 2 | 393216 | 514100 | 10.459 | 2 equal |
| TC-RS-HW-19 | ocp3 | 2 | 393216 | 286076 | 5.820 | 2 within the maximum error |
| TC-RS-HW-20 | ocp0 | 1 | 196608 | 251992 | 10.254 | 1 equal |
| TC-RS-HW-20 | ocp1 | 1 | 196608 | 256716 | 10.446 | 1 equal |
| TC-RS-HW-20 | ocp2 | 1 | 196608 | 255128 | 10.381 | 1 equal |
| TC-RS-HW-20 | ocp3 | 1 | 196608 | 135884 | 5.529 | 1 within the maximum error |
| TC-RS-HW-21 | ocp0 | 1 | 196608 | 249304 | 10.144 | 1 equal |
| TC-RS-HW-21 | ocp3 | 1 | 196608 | 156524 | 6.369 | 1 within the maximum error |
| TC-RS-HW-22 | ocp0 | 1 | 196608 | 248656 | 10.118 | 1 equal |
| TC-RS-HW-22 | ocp1 | 1 | 196608 | 258644 | 10.524 | 1 equal |
| TC-RS-HW-22 | ocp2 | 1 | 196608 | 255792 | 10.408 | 1 equal |
| TC-RS-HW-22 | ocp3 | 1 | 196608 | 142524 | 5.799 | 1 within the maximum error |
| TC-RS-HW-23 TC-RS-HW-06 | ocp0 | 5 | 983040 | 1013156 | 8.245 | 5 equal |
| TC-RS-HW-23 TC-RS-HW-06 | ocp1 | 5 | 983040 | 1045152 | 8.505 | 5 equal |
| TC-RS-HW-23 TC-RS-HW-06 | ocp2 | 5 | 983040 | 957104 | 7.789 | 5 equal |
| TC-RS-HW-23 TC-RS-HW-06 | ocp3 | 5 | 983040 | 664264 | 5.406 | 5 within the maximum error |
| TC-RS-HW-13 | ocp0 | 2 | 393216 | 504320 | 10.260 | 2 equal |
| TC-RS-HW-13 | ocp1 | 2 | 393216 | 497040 | 10.112 | 2 equal |
| TC-RS-HW-13 | ocp2 | 2 | 393216 | 504404 | 10.262 | 2 equal |
| TC-RS-HW-13 | ocp3 | 2 | 393216 | 318748 | 6.485 | 2 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-06 | ocp0 | 5 | 983040 | 1013156 | 8.245 | 5 equal |
| TC-RS-HW-18 TC-RS-HW-06 | ocp1 | 5 | 983040 | 1045152 | 8.505 | 5 equal |
| TC-RS-HW-18 TC-RS-HW-06 | ocp2 | 5 | 983040 | 957104 | 7.789 | 5 equal |
| TC-RS-HW-18 TC-RS-HW-06 | ocp3 | 5 | 983040 | 664264 | 5.406 | 5 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-08 | ocp0 | 4 (1 aborted) | 589824 | 766792 | 10.400 | 1 aborted, not decoded, 3 equal |
| TC-RS-HW-18 TC-RS-HW-08 | ocp1 | 4 (1 aborted) | 589824 | 735740 | 9.979 | 1 aborted, not decoded, 3 equal |
| TC-RS-HW-18 TC-RS-HW-08 | ocp2 | 4 (1 aborted) | 589824 | 773816 | 10.496 | 1 aborted, not decoded, 3 equal |
| TC-RS-HW-18 TC-RS-HW-08 | ocp3 | 4 (1 aborted) | 589824 | 474176 | 6.431 | 1 aborted, not decoded, 3 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-09 | ocp0 | 8 | 1572864 | 1925912 | 9.796 | 8 equal |
| TC-RS-HW-18 TC-RS-HW-09 | ocp1 | 8 | 1572864 | 1962344 | 9.981 | 8 equal |
| TC-RS-HW-18 TC-RS-HW-09 | ocp2 | 8 | 1572864 | 1866800 | 9.495 | 8 equal |
| TC-RS-HW-18 TC-RS-HW-09 | ocp3 | 8 | 1572864 | 1284628 | 6.534 | 8 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-10 | ocp0 | 22 | 1130025 | 1462840 | 10.356 | 22 equal |
| TC-RS-HW-18 TC-RS-HW-10 | ocp3 | 22 | 917401 | 643116 | 5.608 | 22 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-10 | ocp1 | 1 | 196608 | 246672 | 10.037 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-10 | ocp2 | 1 | 196608 | 248364 | 10.106 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-12 | ocp0 | 1 | 196608 | 250292 | 10.184 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-12 | ocp1 | 1 | 196608 | 250892 | 10.209 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-12 | ocp2 | 1 | 196608 | 254528 | 10.357 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-12 | ocp3 | 1 | 196608 | 133344 | 5.426 | 1 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-14 | ocp0 | 3 | 589824 | 743136 | 10.079 | 3 equal |
| TC-RS-HW-18 TC-RS-HW-14 | ocp1 | 3 | 589824 | 753640 | 10.222 | 3 equal |
| TC-RS-HW-18 TC-RS-HW-14 | ocp2 | 3 | 589824 | 770560 | 10.451 | 3 equal |
| TC-RS-HW-18 TC-RS-HW-14 | ocp3 | 3 | 589824 | 444960 | 6.035 | 3 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-16 | ocp0 | 1 | 196608 | 256192 | 10.424 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-16 | ocp1 | 1 | 196608 | 262656 | 10.688 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-16 | ocp2 | 1 | 196608 | 259292 | 10.551 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-16 | ocp3 | 1 | 196608 | 163708 | 6.661 | 1 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-19 | ocp0 | 2 | 393216 | 522428 | 10.629 | 2 equal |
| TC-RS-HW-18 TC-RS-HW-19 | ocp1 | 2 | 393216 | 495240 | 10.076 | 2 equal |
| TC-RS-HW-18 TC-RS-HW-19 | ocp2 | 2 | 393216 | 514100 | 10.459 | 2 equal |
| TC-RS-HW-18 TC-RS-HW-19 | ocp3 | 2 | 393216 | 286076 | 5.820 | 2 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-20 | ocp0 | 1 | 196608 | 251992 | 10.254 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-20 | ocp1 | 1 | 196608 | 256716 | 10.446 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-20 | ocp2 | 1 | 196608 | 255128 | 10.381 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-20 | ocp3 | 1 | 196608 | 135884 | 5.529 | 1 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-21 | ocp0 | 1 | 196608 | 249304 | 10.144 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-21 | ocp3 | 1 | 196608 | 156524 | 6.369 | 1 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-22 | ocp0 | 1 | 196608 | 248656 | 10.118 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-22 | ocp1 | 1 | 196608 | 258644 | 10.524 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-22 | ocp2 | 1 | 196608 | 255792 | 10.408 | 1 equal |
| TC-RS-HW-18 TC-RS-HW-22 | ocp3 | 1 | 196608 | 142524 | 5.799 | 1 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-06 | ocp0 | 5 | 983040 | 1013156 | 8.245 | 5 equal |
| TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-06 | ocp1 | 5 | 983040 | 1045152 | 8.505 | 5 equal |
| TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-06 | ocp2 | 5 | 983040 | 957104 | 7.789 | 5 equal |
| TC-RS-HW-18 TC-RS-HW-23 TC-RS-HW-06 | ocp3 | 5 | 983040 | 664264 | 5.406 | 5 within the maximum error |
| TC-RS-HW-18 TC-RS-HW-13 | ocp0 | 2 | 393216 | 504320 | 10.260 | 2 equal |
| TC-RS-HW-18 TC-RS-HW-13 | ocp1 | 2 | 393216 | 497040 | 10.112 | 2 equal |
| TC-RS-HW-18 TC-RS-HW-13 | ocp2 | 2 | 393216 | 504404 | 10.262 | 2 equal |
| TC-RS-HW-18 TC-RS-HW-13 | ocp3 | 2 | 393216 | 318748 | 6.485 | 2 within the maximum error |

Rate of TC-RS-HW-09: 32 images, 12582912 bytes of samples, 7039684 bytes compressed; transfer 0.011 s from the first submission to the last packet received: 9.184 Gbit/s of samples, 9.196 Gbit/s of link packets to the system and 5.151 Gbit/s from it. Not timed: the images and packets built before, the conversion of the 1736 packets received (0.115 s) and the checks after
