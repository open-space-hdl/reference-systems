# spfi_encrypt on the VCK190: Hardware Test Procedure

## 1. Purpose

This procedure tests the system spfi_encrypt on the AMD VCK190 evaluation board against a STAR-Dundee STAR-Ultra
PCIe as the far end of the SpaceFibre link. The far end reaches every register of the system with RMAP commands on
virtual channel 0, loads the keys of the four encryption channels, sends Space Packets on virtual channel 1 and
checks the encrypted chunks that come back with AES-GCM. The script
[`systems/spfi_encrypt/sw/hardware_test.py`](../../../sw/hardware_test.py) runs all test cases.

## 2. Material

| Item | Use |
| --- | --- |
| VCK190 evaluation board (production silicon), power supply, USB cable to the JTAG and UART port | Device under test |
| STAR-Ultra PCIe in a test computer, STAR-System installed on that computer with its Python package | Far end, test computer |
| Straight QSFP cable (four lanes) | QSFP1 of the VCK190 to port 1 of the STAR-Ultra |
| Build computer with AMD Vivado 2025.2 (device support and a synthesis licence for the XCVC1902), Python 3.10 or later and Git | Export (section 3), build and programming |
| The repository at v0.2.0 with its submodules on the build computer and on the test computer: exported by the Python package openspacehdl 0.1.0, or cloned from GitHub (section 3) | Sources of the build and of the test script |
| Python 3.10 or later with pytest and `pycryptodome` (installed with openspacehdl), and Git, on the test computer | Export (section 3) and test script |

## 3. Installation from the openspacehdl package

The release 0.1.0 of the Python package openspacehdl carries this repository at v0.2.0, with every core it pins, as
Git bundles with their history. On the build computer and on the test computer (Python 3.10 or later, Git on the
`PATH`), in PowerShell or cmd:

```shell
python -m venv --system-site-packages C:\osh\venv
C:\osh\venv\Scripts\python -m pip install openspacehdl==0.1.0
C:\osh\venv\Scripts\openspacehdl export --git C:\osh\src
```

pip installs the package with pytest and `pycryptodome`, which the test script needs, and their dependencies. In the
virtual environment it keeps a version that the Python of the computer already has within the range of the package,
and installs only what is missing or out of range; a message that it does not uninstall a package of the Python of
the computer is expected. `C:\osh\venv\Scripts\python -m pip check` then reports no broken requirements.

The export directory must not exist or must be empty. The export itself needs no network: it clones this repository,
Open Logic FT and every core from the bundles into directories of `C:\osh\src` next to `reference-systems`, fills the
submodules in `cores/` of `C:\osh\src\reference-systems` from them at the recorded commits and writes
`C:\osh\src\openspacehdl-lock.json`. The origin of every clone is its published repository, so that `git fetch` works
later on a computer with network access. Every command of sections 4 and 8 runs from
`C:\osh\src\reference-systems`, where `git submodule status` shows the commits of the release without a leading `-`
or `+` and `python tools/check_pins.py --offline` passes against the tags that the bundles carry. Keep the output of
`openspacehdl --version` and of `openspacehdl lock` (the pins) and the file `openspacehdl-lock.json` with the
records, outside the exported tree.

A computer without network access takes the package and its dependencies from a computer with network access, the
same version of Windows and the same version of Python: the set of files depends on the version of Python, and
`pycryptodome` is a binary for 64-bit Windows. There, `py -3.14 -m pip download openspacehdl==0.1.0 -d <dir>` (with
the version of Python of the offline computer) writes the wheels to `<dir>`. Compare the SHA-256 of
`openspacehdl-0.1.0-py3-none-any.whl` with the one on the GitHub release of the package, copy `<dir>`, and install
with `C:\osh\venv\Scripts\python -m pip install --no-index --find-links <dir> openspacehdl==0.1.0`. The export is
the same. The SHA-256 of the file:

```shell
# PowerShell
Get-FileHash <dir>\openspacehdl-0.1.0-py3-none-any.whl -Algorithm SHA256
# cmd
certutil -hashfile <dir>\openspacehdl-0.1.0-py3-none-any.whl SHA256
```

With network access, a clone of the release from GitHub serves as well, with pytest and `pycryptodome` installed in
the Python of the test computer (`python -m pip install "pytest>=9.0,<10" "pycryptodome>=3.23,<5"`):

```shell
git clone -b v0.2.0 https://github.com/open-space-hdl/reference-systems.git C:\osh\src\reference-systems
cd C:\osh\src\reference-systems
git submodule update --init
git -C cores/open-logic-ft submodule update --init 3rdParty/en_cl_fix
python tools/check_pins.py
```

`git submodule status` must then show the commits of the release without a leading `-` or `+`, and
`tools/check_pins.py` checks the pins against the tags of the published repositories.

In both trees the submodules of the cores themselves (`open-logic` and `uvvm` in `cores/<core>`) stay empty (`git
submodule status --recursive` shows them with a leading `-`): they serve the tests of the cores, and the system uses
`cores/open-logic-ft` with its submodule `en_cl_fix`. Do not run `git submodule update --init --recursive`, which
needs network access for them. The longest path below the export directory has 117 characters in the working tree
and 141 with the Git metadata: keep the export directory short, as `C:\osh\src`, and set `RS_VIVADO_OUT` for the
build as in section 4.

The test (section 8) runs from the exported tree with a Python that has pytest, `pycryptodome` and the Python package of
the far-end software: the virtual environment of the package, created with `--system-site-packages` so that it also
finds the packages of the Python of the test computer, with every command of section 8 run by
`C:\osh\venv\Scripts\python`. With openspacehdl installed into the Python of the test computer itself, that Python has
pytest and `pycryptodome` as dependencies of the package: pip replaces a version of either outside the ranges of the
package in that Python, which other programs of the computer may notice. With a clone, the Python into which they were
installed runs the test. `--check-environment` (section 8) shows in every case whether the Python has every item.

`C:\osh\venv\Scripts\openspacehdl serve` serves the website, the Composer and the documentation of every core at the
pinned versions on `http://127.0.0.1:8765/`, without network access; the test does not need it.

## 4. Build

The transceiver reference clock has two sources on the board, and the device image fixes the pins it takes the clock
from and its frequency. Confirm the reference clock on the board first (section 5, step 2), then build the image of
that option only, from the root of the repository:

```shell
# Option A: oscillator Si570 U205 at 100.000 MHz, its frequency after power-up (default)
vivado -mode batch -source systems/spfi_encrypt/targets/vck190/tcl/build.tcl
# Option B: oscillator Si570 U205 set to 156.250 MHz
vivado -mode batch -source systems/spfi_encrypt/targets/vck190/tcl/build.tcl -tclargs all si570 156.25
# Option C: clock generator 8A34001, output Q1 at 156.25 MHz
vivado -mode batch -source systems/spfi_encrypt/targets/vck190/tcl/build.tcl -tclargs all 8a34001
```

The elaboration alone takes about 6 GB of memory.

The script sets `XILINX_LOCAL_USER_DATA=no` for the runs that `launch_runs` starts at the same time (AMD AR 63253): on a
fresh installation of Vivado, one of the runs can otherwise fail at start-up with Common 17-1257 ("Failed to create
directory") and "tclapp::load_apps failed".

The script creates the project with the sources of the compile order that the Composer exported
(`systems/spfi_encrypt/hdl/`), the transceiver wizard instance of OpenFibre (`ofb_gtw`), the CIPS block design, the
board top level and the constraints, and runs synthesis, implementation and the device image. The pins of the reference
clock come from `constr/spfi_encrypt_vck190_refclk_si570.xdc` or `constr/spfi_encrypt_vck190_refclk_8a34001.xdc`. The
frequency sets the transceiver wizard instance and the clock `gt_refclk`, whose constraint the script writes into the
project directory (`spfi_encrypt_vck190_refclk_clock.xdc`); the line rate (6.25 Gbit/s) and the lane clock (156.25 MHz)
are the same for every option. `-tclargs rtl` (or, for example, `-tclargs rtl si570 156.25`) only creates the project
and elaborates the design, a check of the sources in about ten minutes; it writes `results/invalid_constraints.xdc` of
the elaborated design, so that a constraint that does not apply shows up before a synthesis, and
`results/build_info.txt`. In this step the transceiver wizard instance is a black box, so its clocks and constraints are
missing: the critical warning Constraints 18-5119 (`ofb_gtw_gt_quad_base_0`) and the warning Vivado 12-1008 (no clock at
the net `pa0_LaneClk_s`) are expected there, and `invalid_constraints.xdc` lists the location of the GTY quad and the
two maximum delays between the lane clock and `Clk_sys`. `Rst` of the system has no false path of its own: every block
that receives it releases it through `olo_base_reset_gen`, whose scoped constraints cut the paths into its
synchronisers.

On Windows the project path must not be longer than about 40 characters: the device image step compiles the platform
loader firmware in a deep directory below the project, and with a longer path `write_device_image` fails ("opening
dependency file ... No such file or directory"). Set the environment variable `RS_VIVADO_OUT` to a short directory
inside a working directory (for example `C:/git/rs`) before starting Vivado; without it the project is
`vivado_out/spfi_encrypt_vck190` in the repository.

The script writes the results to `results/` in the project directory:

| File | Content | Expected |
| --- | --- | --- |
| `spfi_encrypt_vck190_top.pdi` | Device image | Present |
| `timing_summary.rpt` | Timing of all clocks | The clock summary lists `clk_pl_0` (150 MHz), `gt_refclk` and the lane clock `ch0_txoutclk` (156.25 MHz); check_timing finds no register without a clock (`no_clock` 0); WNS and WHS at least 0 ns; no pulse width violation |
| `utilization.rpt`, `utilization_hierarchical.rpt` | Resources of the device and per block | LUT, register, block RAM and URAM count of the system and of its blocks |
| `methodology.rpt`, `drc.rpt` | Methodology and design rule checks | No critical item about clock domain crossings or the transceivers, apart from the two TIMING-54 below |
| `invalid_constraints.xdc` | The constraints that do not apply to the implemented design (`write_xdc -constraints invalid`) | Empty apart from the header |
| `build_info.txt` | Step, Vivado version, part, the reference clock with its frequency, the period of `gt_refclk` and the pins of `GtRefClk_P` and `GtRefClk_N` in the design, commits of the repository and of its submodules | The option confirmed in section 5 (`si570`: AB11, AB10; `8a34001`: AD11, AD10; period 10.000 ns at 100 MHz, 6.400 ns at 156.25 MHz); the commits of the release, as `git submodule status` shows them in the exported or cloned tree |

The implementation run stops before `opt_design` when a constraint did not apply (`tools/vivado/check_constraints.tcl`):
an object query of a constraint that matched nothing (Vivado 12-180, 12-507, 12-508, 12-584, 12-1008, 12-4739) or the
constraint file of a module that is not in the netlist (Designutils 20-1280). The messages of `link_design` above the
stop name the constraint. Synthesis always runs from scratch, without the netlist of an earlier run
(`AUTO_INCREMENTAL_CHECKPOINT` off). In a build whose CIPS was changed in the open project, the constraint file of the
CIPS was not read and the clock `clk_pl_0` was missing; the incremental synthesis after the change, which reused the
earlier netlist, is the likely cause. Change the system clock in the Composer file and in `tcl/build.tcl`
(`clk_sys_mhz`) and run the script again, not in the CIPS of the open project.

Expected messages: TIMING-9 and LUTAR-1 in the transceiver wizard and at the reset generators, and two TIMING-54
(critical warning) for the clock pair maximum delays of the system constraints (`set_max_delay -datapath_only` from the
lane clock to `Clk_sys` and back, `hdl/spfi_encrypt.xdc`). The system constraints are read scoped to the system entity,
and Vivado reports every exception between two clocks in a scoped file, because the exception applies to all paths
between the clocks, also outside the scope. Here that is intended: the lane clock exists only inside the system, so
every path between the two clocks is a crossing of the system.

## 5. Board set-up

1. Boot mode switch SW1 to JTAG (VCK190 Evaluation Board User Guide UG1366 v1.1).
2. Reference clock, before the build (section 4): the transceivers need the frequency of the device image at its pins.
   Use one of three options and build only the image of that option. Option A needs no setting: the Si570 runs at
   100.000 MHz after every power-up (UG1366, section "Programmable FMCP MGT SI570 Clock with Buffer"). Options B and C
   need the clock set in the System Controller of the board, with its board user interface (Board Evaluation and
   Management Tool) or its command line, over the USB-UART connector J207 (UG1366, section "System Controller"):

   | Item | Option A (default image) | Option B (image `si570 156.25`) | Option C (image `8a34001`) |
   | --- | --- | --- | --- |
   | Source | Programmable oscillator Si570 (U205, USER_SI570_1) through the clock buffer SI53340 (U206), output Q0 (UG1366, section "Programmable FMCP MGT SI570 Clock with Buffer") | As option A | Clock generator 8A34001 (U219), output Q1 (UG1366, section "GTY200: QSFP1") |
   | Pins of the device | GTY_REFCLK0 of GTY quad 201: AB11, AB10; the transceivers of quad 200 use this reference clock of the adjacent quad | As option A | MGTREFCLK1 of GTY quad 200: AD11, AD10 |
   | Frequency | 100.000 MHz | 156.250 MHz | 156.25 MHz |
   | Setting | Header J306 off (default: the clock buffer passes the Si570); no setting of the Si570. If the Si570 was set to another frequency since the board was switched on, switch the board off and on again | Header J306 off; set the Si570 to 156.250 MHz after every power-up | The 8A34001 is programmed with configuration files, not with a frequency value, and the frequency of Q1 is not documented: load a configuration whose output Q1 runs at 156.25 MHz |
   | Read-back | Optional: read the frequency of the Si570 back in the System Controller and record it | Read the frequency of the Si570 back in the System Controller and record it | Read the setting of the 8A34001 back in the System Controller and record what it gives for Q1; if Q1 cannot be confirmed at 156.25 MHz, use option A |

   A power cycle sets the Si570 back to 100.000 MHz: with option B, set it to 156.250 MHz again before the device is
   programmed. After every change of the reference clock, program the device again (step 4).
3. Connect QSFP1 of the VCK190 to port 1 of the STAR-Ultra. Lane i of the system must reach lane i of the far end;
   the system detects inverted receive polarity per lane itself.
4. Program the device image of the option (Vivado Hardware Manager); `results/build_info.txt` names its reference
   clock and frequency. LED 0 blinks (about 0.9 s on, 0.9 s off) and LED 1 is on.

## 6. Far-end configuration

| Parameter | Value |
| --- | --- |
| Port | Port 1 of the STAR-Ultra, four lanes |
| Line rate | 6.25 Gbit/s per lane, 8B/10B |
| Data scrambling | On. Scrambling is set per direction: each end announces in INIT3 whether it scrambles the data it sends, and the receiver of the other end follows (ECSS-E-ST-50-11C 5.7.6.2). The system scrambles what it sends: DL_CTRL bit 8 (`DATA_SCRAMBLED`) of `ofb0` is this near-end capability, reset value 1; the setting of the STAR-Ultra decides the other direction |
| Link start | Auto-start. The system starts the link after a reset (`LaneStart_g` of `ofb0` is true in the Composer file) |
| Channel 0 | Virtual channel 0 of port 1 through the routing switch of the STAR-Ultra: path address 3 is the port of the routing switch that leads to port 1, path address 1 the port that leads back to channel 0 |
| Channel 1 | Virtual channel 1 of port 1 |

The script sends RMAP commands on channel 0 with the path address 3 in front of the packet and the Reply Address 1;
the routing switch of the STAR-Ultra sends the command out of its port 3 to port 1, the system replies with the
address 1 and the routing switch delivers the reply through its port 1 on channel 0. Space Packets go on
channel 1 directly, without a path address.

## 7. System configuration

The script uses the reset values of the system and writes only what the tests need. It takes the register windows,
the encryption channels with their output APIDs and key slots, the chunk size and the buffer sizes from the Composer
file of the system, and the register maps from the headers of the cores:

| Item | Value |
| --- | --- |
| RMAP target `rmap0` | Logical address 0xFE, key 0x00, the whole register map accessible (reset values) |
| Initiator logical address | 0xFE |
| Register windows (`SPFI_ENCRYPT_<BANK>_BASE`) | `spp0` 0x0000_0000, `ocr0` 0x0001_0000, `edac0` 0x0002_0000, `ocr1` to `ocr3` 0x0003_0000 to 0x0005_0000, `rmap0` 0x0006_0000, `ofb0` 0x0007_0000 |
| Input APIDs | 0x010 + i for encryption channel i: the receive APID table of `spp0` routes APID 0x010 + i to receive port i; every other APID keeps the reset path to receive port 0 |
| Keys | One 256-bit key per AES-GCM core in slot 0, the slot its framer uses, with generated IVs: fixed field and first invocation counter per channel; keys and IV values are derived from the seed of the script |
| Test slot | Slot 1 of `ocr0`, which its framer does not use, holds a further key and IV values for the commands of several words |
| Output | Space Packets with APID i (encryption channel i) to destination 0 (Target Logical Address 0xFE), CPTP on virtual channel 1 |

## 8. Tests

The test cases run as pytest tests (`sw/hwtest/test_spfi_encrypt.py`, plugin `sw/refsys/hwplugin.py`). Run them on the
test computer from the root of the repository (the register maps come from the headers of the cores in `cores/`),
with the Python of section 3 (`C:\osh\venv\Scripts\python` in place of `python` for the virtual environment):

```shell
python systems/spfi_encrypt/sw/hardware_test.py --check-environment
python -B -m pytest systems/spfi_encrypt/sw/hwtest --target model --results <directory>
python -B -m pytest systems/spfi_encrypt/sw/hwtest --target device --device <STAR-Ultra> --results <directory>
```

- The test computer needs the repository with the submodules of the cores (section 3), Python 3.10 or later with
  pytest and `pycryptodome` (installed with the Python package openspacehdl), Git, and STAR-System with its Python
  package.
- `--check-environment` checks the test computer for a run against the board: Python, pytest, `pycryptodome` and the
  Python package of STAR-System, one line per item. It ends with exit code 1 when an item is missing (`FAIL`). With
  everything installed it prints:

  ```text
  OK   Python: <version>
  OK   pytest: <version>
  OK   pycryptodome: <version>
  OK   Python package of the far-end software: found
  ```

  `--check-environment --target model` checks what a run against the model needs: the same lines without the
  last one, and `SKIP Python package of the far-end software: not needed for --target model`.
- `--target model` runs the test cases against the software model of the system, `--target device` against the
  board through the STAR-Ultra (`<STAR-Ultra>`: its name in STAR-System).
- `--results` names a directory outside the repository. Every run writes a new directory into it with its records
  (section 9). `-B` keeps Python from writing bytecode into the repository; the run records the state of the working
  tree at its start and at its end and warns without `-B`.
- Before the first test case, the run reads channels 0 and 1 until no packet arrives within 0.5 s, and prints how many
  packets of an earlier run it discarded per channel. A run that stopped early, for example in TC-RS-HW-06, leaves
  chunks and late replies on the channels, which the next run would otherwise take for its own.
- The console shows every step, every checked value with its expected value and the Space Packets of every exchange
  as it happens, and one line per test case at the end.
- The run stops at the first failure, since the later test cases build on the earlier ones, and reports those as not
  run. `--keep-going` runs a later test case when the test cases it builds on passed (for diagnosis; the report says
  so).
- `--profile` (default `lab`: 40 Space Packets in TC-RS-HW-06) sets the size of the run, `--seed` (default 1) the keys
  and the packets. `--tc TC-RS-HW-nn` runs one test case and the test cases it builds on.
- The [system testbench](../../../sim/README.md) runs the same test cases in simulation with the profile `sim`.
- `hardware_test.py --device` and `--model`, the commands of earlier releases, run the same pytest tests.

The key registers are write-only. TC-RS-HW-04 and TC-RS-HW-05 check the IV values through the slot query;
TC-RS-HW-06 checks the keys, since every chunk must authenticate and decrypt with the key of its channel.

The script reads the base address and the identification of every register bank and the channels of the ECC
monitor `edac0` from `systems/spfi_encrypt/hdl/spfi_encrypt_regs.h`, which the Composer exports with the top level
(`SPFI_ENCRYPT_<BANK>_BASE`, `SPFI_ENCRYPT_<BANK>_ID`, `SPFI_ENCRYPT_EDAC0_CH_<MEMORY>`), and the register offsets
and fields from the headers of the cores (`cores/<core>/sw/<prefix>_regs.h`).

| Test | Steps | Pass criterion |
| --- | --- | --- |
| TC-RS-HW-01 Link | Read `ID`, `DL_STATUS`, and `LANE_STATUS` and `LANE_TIMEOUT_COUNT` of every lane of `ofb0`. The script prints for every lane, as a `RECORD` line that is no pass criterion, the completed receiver resets (`LANE_TIMEOUT_COUNT.RX_RESETS`), the initialisation timeouts (`LANE_TIMEOUT_COUNT.COUNT`) and `LANE_STATUS` bits 24 to 27 (RXRESETDONE, receiver reset in progress, CDR lock, receiver electrical idle) | `ID` = `SPFI_ENCRYPT_OFB0_ID`, link initialised (`DL_STATUS.LINK_STATE` = 3), all lanes of `ofb0` (`NumLanes_g` in the Composer file) active (`LANE_STATUS.STATE` = 7) |
| TC-RS-HW-02 Identification | Read the identification register of every register window | `ID` of every register bank with an identification (`spp0`, `ocr0` to `ocr3`, `rmap0`, `ofb0`) = its `SPFI_ENCRYPT_<BANK>_ID`; `INFO` of `edac0` 0x00001008 (8 channels, 16-bit counters) |
| TC-RS-HW-03 RMAP | Verified writes of `IRQ_ENA` of `edac0` and read back; a read with a wrong key | Every value reads back; the wrong key gives the status "invalid key" (3) |
| TC-RS-HW-04 Commands of several words | Read `ID`, `GENERICS`, `GENERICS2` and `RELEASE_BYTES` of `ocr0` with one command of 16 bytes and with single reads; load the test slot with the key in one write of 32 bytes and `IV_FIXED`, `IV_COUNT_LO` and `IV_COUNT_HI` in one write of 12 bytes, then `KEY_LOAD`; select the slot in `SLOT_QUERY` and read `SLOT_STATUS` to `SLOT_COUNT_HI` with one command of 16 bytes and with single reads | Both reads of 16 bytes give the words of the single reads; the slot is valid with generated IVs (`SLOT_STATUS` 0x3) and holds the written fixed field and invocation counter |
| TC-RS-HW-05 Configuration | Receive APID table of `spp0` (APID 0x010 + i to port i); slot 0 of every AES-GCM core loaded as in TC-RS-HW-04 with the key and IV values of its channel and queried; counters cleared | Table entries read back, no table overrun; `STATUS.BUSY` of every core returns to 0; every slot is valid with generated IVs and holds the fixed field and invocation counter of its channel |
| TC-RS-HW-06 Encryption | Space Packets on the four input APIDs: first 1 byte, one byte less than, equal to and one byte more than a chunk of 4096 bytes, and 8186 bytes (a Space Packet of 8192 bytes, the size of the receive buffer of `spp0`), then random lengths | Every input packet comes back as ceil(n / 4096) Space Packets with the APID of its channel and consecutive sequence counts; every chunk authenticates and decrypts with the key of its channel, carries the fixed field of its channel, the first invocation counter is the loaded one, the invocation counters have no gap, and the decrypted chunks give the input packets |
| TC-RS-HW-07 Counters | Read the packet counters of `spp0`, the encryption and error counters of the AES-GCM cores and the counters of `edac0` | Received and sent packets and encryptions match TC-RS-HW-06; every error counter and the ECC counters of every channel of `edac0` (`SPFI_ENCRYPT_EDAC0_CH_FRM0_ECC_0` to `FRM3_ECC_1`) are 0 |
| TC-RS-HW-18 Repeated run | The test cases of this run before TC-RS-HW-18 run again on the same device, without a reset or a new device image, as a second run of the script (`sw/refsys/systest.py`, `repeat_run`) | Every test case passes again; the records of the second run carry the name TC-RS-HW-18 and that of their test case |

### Repeated runs

TC-RS-HW-18 runs the test cases of the run again on the same device, without a reset or a new device image, as a
second run of the script does, and every one must pass again. The second run finds what the first one left: the
Space Packet node continues the Packet Sequence Counts of its output APIDs, and TC-RS-HW-06 checks only that the
counts of an APID are consecutive; TC-RS-HW-05 writes the receive table entries again and loads the key slots again.
The system has no reset of its data path that the script could command: after a run that stopped early, program the
device again before the next run.

### When the link does not come up

The registers of the system are reachable only over the link: without it TC-RS-HW-01 fails with a command that does
not complete on channel 0. Before any rerun, record:

1. The link state of port 1 and the lane state of its four lanes in STAR-System.
2. The LEDs of the VCK190 (section 5, step 4).
3. The reference clock: the option, the value read back in the System Controller (section 5, step 2) and the reference
   clock with its frequency in `results/build_info.txt` of the programmed image. They must agree; after a power cycle
   the Si570 runs at 100.000 MHz again, the frequency of option A.

The lane states of port 1 show how far the lane initialisation got (ECSS-E-ST-50-11C 5.6). The system starts its lanes
itself and sends INIT1 in the Started state; a lane of the STAR-Ultra in AutoStart leaves Wait when a signal arrives,
reaches Connecting after it has received INIT1 and Connected after it has received INIT2 or INIT3 of the system:

| Lane states of port 1 | Reading |
| --- | --- |
| Wait, and port 1 reports no signal | No signal arrives from the system: device image, reference clock or cable |
| Started or Connecting, then ClearLine and Wait again, never Connected | The STAR-Ultra receives the INIT1 of the system, but no lane of the system answers with INIT2: the lanes of the system do not receive the STAR-Ultra, or leave the initialisation; the receive path of the system |
| Active on some lanes only | A problem of single lanes: lane order, cable or polarity |
| Active on all lanes, but the link is not ready | The alignment of the lanes of the two ends |

The system resets the receiver of a lane at every restart of the lane, and a lane of the system stays in Started for
up to 3 ms per initialisation attempt, 32 us with the timeout of the standard (README of the system, lane
initialisation). The registers that show and set the receiver reset (LANE_STATUS bits 24 to 27,
LANE_TIMEOUT_COUNT, PA_CTRL of `ofb0`) are reachable only over the link: TC-RS-HW-01 records them once the link is
up. Another receiver reset setting, such as one of the PA_CTRL values in the hardware test of the OpenFibre
reference design for the VCK190, needs a device image with that setting as its reset value.

These records count from the start of the device image. Until the far end answers, every lane restarts its
initialisation every 3 ms and resets its receiver: a far end that starts a few seconds after the system leaves
hundreds of receiver resets and more than a thousand initialisation timeouts per lane, with a link that then carries
every packet correctly. The counts are records, not pass criteria. The CDR lock bit (LANE_STATUS bit 26) is not a
usable indication with the transceiver configuration of this design: it can read 0 on every lane while the link is
up and every packet arrives correctly, so it is recorded without an expected value.

Then correct what the records show (clock, image, cable) and run the script again. After the QSFP cable was unplugged
or moved, reset the link of port 1 in STAR-System before the next run; if the link still does not come up, program
the device image again, which resets the system, and its lanes start the link again (section 6, link start).

## 9. Records

Keep for every build and every test run, with the date:

| Item | Source |
| --- | --- |
| Commits of the repository and of the submodules | `results/build_info.txt` |
| Build results | `results/timing_summary.rpt`, `results/utilization.rpt`, `results/utilization_hierarchical.rpt`, `results/methodology.rpt`, `results/drc.rpt`, `results/invalid_constraints.xdc` |
| Board state | Reference clock: option and the value read back (section 5, step 2); LEDs after programming (section 5), version of the STAR-System software |
| Link state | When the link did not come up: the link and lane states of port 1 in STAR-System (section 8) |
| Test computer | The output of `--check-environment` |
| Test run | The command line and the directory that the run writes below `--results` (one per run, named after its date and time) |
| `steps.jsonl`, `run.log` | Every event of the run (steps, register accesses, checked values with their expected values, Space Packets, recorded values, environment and the state of the working tree), as JSON lines and as text |
| `junit.xml` | The JUnit XML report of pytest, with the system, profile, seed and commit of the run |
| `records_spfi_encrypt_<date>_<time>.json`, `report_spfi_encrypt.md` | The records of the run (results, test cases not run, recorded values) and their tables (`tools/render_records.py`) |

A failing test case is recorded with its output and the register values it reports. These files are what a review of
the hardware test needs: they are brought back from the build computer and the test computer together, the device
image is not needed for that.
