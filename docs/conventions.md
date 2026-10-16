# Reference Systems Conventions

The reference systems combine the Open Space HDL cores into complete designs. The cores are not changed here: a
system uses their interfaces as documented in their user guides, and a missing feature is added in the core first.

## Systems

- A system is defined by its Composer file `systems/<system>/<system>.composer.json`. The file is the source of truth
  for the instances, generics, connections, clocks and core versions of the system. It is edited only in the
  [Open Space Composer](https://openspacehdl.org/composer/) and stored as the Composer saves it, never edited by hand.
- The system name (`system.name` of the file) is the directory name, the file name and the name of the generated top
  level entity. It is lower case with underscores (for example `spfi_encrypt`).
- `systems/<system>/hdl/` holds the VHDL top level that the Composer generates from the file. It is regenerated after
  every change of the file and committed together with it; it is never edited by hand.
- The top levels are exported with the command line tool of the Composer (`tools/composer_hdl.mjs` of the website
  repository) at the commit in `tools/composer_ref.txt`. `python tools/check_hdl.py --composer <checkout>` exports
  every system again and checks that `hdl/` equals the export; the CI runs it. A new version of the Composer updates
  `tools/composer_ref.txt` and the exports of all systems in one change.
- Each system has a `README.md` with its function, blocks, clocks, top-level ports and status, and `docs/` with the
  address map and the initialisation sequence once its top level exists.

## Core versions

- `cores/<repository>` is a submodule of every repository that the section `sources` of a system file names: Open
  Logic FT (`cores/open-logic-ft`) and the cores (`cores/openrmap`, `cores/openspp`, ...). The directory is the
  repository name from the URL.
- Each submodule is pinned to the commit of the system files. All systems share `cores/` and therefore pin the same
  commits; moving to a new version of a core updates the system files of all systems and the submodule in one change.
- One Open Logic FT library serves the whole system: the tag of the source `olo`. Every core states in `verifiedWith`
  the Open Logic FT tag it was verified with; this tag is not newer than the tag of the system and is the pin of the
  core's own `open-logic` submodule.
- `python tools/check_pins.py` checks these rules: URL and commit of every submodule, the tag of Open Logic FT, the
  `verifiedWith` tags against the cores, the same commits across all systems, no unused submodule.
- Only commits that are published in the repositories of the cores are pinned, never a commit of a local clone.

## Hand-written HDL

Hand-written VHDL exists only in the board top levels (`targets/`) and in the testbenches (`sim/`).

- The [Open Logic coding conventions](../cores/open-logic-ft/doc/Conventions.md) apply, as in the cores: port names
  `<Interface>_<Signal>`, generics `_g`, constants `_c`, four spaces of indentation, VHDL-2008. The port names of the
  generated system (`Clk_<domain>`, `<instance>_<interface>_<signal>`) do not follow this pattern; a port map of the
  system switches the VSG rule `port_map_002` off for its lines.
- VSG 3.27 with the Open Logic configuration in `lint/config` reports no errors and no warnings for the hand-written
  files: `python lint/lint.py`. Safe automatic fixes: `python lint/lint.py --fix`.
- A board top level is named `<system>_<board>_top` (for example `spfi_encrypt_vck190_top`). It instantiates the
  generated system entity and adds only what the board needs: clock and reset sources, I/O and transceiver buffers,
  the processor or block design of the board. It does not change the function of the system.
- Resets are high-active. Vendor primitives are instantiated only in the board top level and in the vendor adapters
  of the cores.
- File header: copyright line and authors, then a 1 to 2 sentence description. Never write "all rights reserved".
- Never use em-dashes in code, comments or documentation. Use UTF-8 only.

## Targets

- `systems/<system>/targets/<board>/` holds everything that is specific to a board: the board top level, the block
  design script, the constraints, the build script and `docs/hardware_test.md`.
- The build script creates the project from the sources of the repository; a build needs no file outside the
  repository except the vendor tools. Build outputs (`vivado_out/`, `.Xil/`, logs) are not tracked.
- A build computer without network access gets the repository as Git bundles: `python tools/make_offline_bundle.py
  <directory>` writes the repository, the submodules in `cores/` at their pins and the submodule `en_cl_fix` of Open
  Logic FT, with a `README.txt` of the commits and the commands, and checks them by a clone without network access.
  The bundles are written outside the repository and never committed.
- The build script writes its results (device image, reports, the commits it was built from) to `results/` in the
  project directory; they are what a review of a build and of a hardware test needs.
- The constraints of a system come from its Composer file (`resolved.constraints`) and from the user guides of the
  cores; the board constraints (pins, I/O standards, board clocks) are added in the target.
- `hardware_test.md` describes the setup (boards, cables, link partner), the build, and the checks over the
  management interface of the system with their expected results.

## Verification

- VUnit runs every test (`run.py`). It compiles Open Logic FT and the cores exactly as the system files describe them
  (`tools/composer_system.py`) and the testbenches of the systems.
- GHDL is the default simulator; `python run.py --questa` runs the same tests in QuestaSim. Blocks that need vendor
  libraries (for example the AMD Versal GTY adapter of OpenFibre) are simulated with the vendor simulator or replaced
  by the behavioural models that the cores provide. A system testbench runs in xsim with the vendor models; the
  QuestaSim evidence (regressions and code coverage) comes from the cores.
- A system simulation with the AMD transceiver model runs in the AMD simulator xsim, which VUnit does not support:
  `systems/<system>/sim/run_xsim.py` builds and runs it with `tools/xsim.py` and checks the results with the same
  Python checks as a VUnit run would.
- A system testbench runs the test cases of the hardware test of the system: the stimuli come from its procedure
  against the software model, and its procedure checks the records of the bench, so that simulation and hardware
  test cannot diverge.
- Every GHDL simulation gets its own copy of the `ghdl.sim_flags` option, and `run.py` stops when a GHDL command
  carries other generics than those of its test: VUnit 5.0.0.dev7 appends the generics to a list that configurations
  added after `set_sim_option` share, so that a test could inherit generics of earlier tests.
- A system testbench drives and observes the top-level ports of the system only, never internal signals. The far end
  of a link is an independent model or a second instance, never the receiver of the port under test.

## Host software

- The host software of the tests is Python 3.10 or later. `sw/refsys/` holds what all systems share (RMAP, Space
  Packets, the chunks of OpenCrypto, the register maps from the C headers of the cores, the facts of a system file,
  the transports); `systems/<system>/sw/` holds the tests of a system.
- Register addresses and fields come from the generated headers of the cores (`cores/<core>/sw/<prefix>_regs.h`),
  never from copied numbers, so that they follow the pinned versions of the cores. The register banks of a system
  (base address, size, identification) and the channels of its ECC monitors come from the header that the Composer
  exports with the top level (`systems/<system>/hdl/<system>_regs.h`); connections and constants of a system come
  from its Composer file, so that a test follows the system when it changes.
- Packets are built and checked by the library, independent of the RTL of the cores. A transport carries complete
  link packets, path addresses included; the transport of a test equipment imports the software of its vendor, which
  the user installs on the test computer.
- Every module and every system test has unit tests that run without hardware (`python -m pytest`); a system test
  also runs against a software model of the system that names the documents of the cores it follows.

## Documentation

- Write for the engineer who builds or uses the system. Decisions are stated as facts, with the reason from the
  system goals or the user guides of the cores.
- Modern technical English, UTF-8, Markdown checked by markdownlint with `.markdownlint.json`; the files that the
  Composer generates in `hdl/` are checked by `tools/check_hdl.py`, not by markdownlint.

## Git

- `main` holds the integrated state. Every change is developed on a branch `feature/<topic>` or `bugfix/<topic>` and
  merged through a pull request once the checks are green.
- Commit messages: imperative subject line, body explains what and why.
