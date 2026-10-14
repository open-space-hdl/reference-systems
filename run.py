"""VUnit run script of the reference systems.

Compiles Open Logic FT and the cores into their libraries (olo, openrmap, openspp, openfibre, opencrypto, ...)
exactly as the Composer files of systems/ describe them (section `sources`, submodules in cores/), then runs the
system testbenches. Vendor-specific blocks (for example the GTY adapter ofb_pa_gty) need the vendor libraries and are
left out here.

GHDL is the default simulator; `--questa` selects QuestaSim. `python run.py --compile` analyses all sources.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

from composer_system import library_files, load, system_files  # noqa: E402

# QuestaSim installation used when --questa is given and VUNIT_MODELSIM_PATH is not set
QUESTA_DEFAULT_PATH = r"D:\Microchip\Libero_SoC_2025.2\Libero_SoC\QuestaSim_Pro\win64"

# Simulator selection must happen before VUnit parses the command line
if "--questa" in sys.argv:
    sys.argv.remove("--questa")
    os.environ["VUNIT_SIMULATOR"] = "modelsim"
    if "VUNIT_MODELSIM_PATH" not in os.environ and Path(QUESTA_DEFAULT_PATH).exists():
        os.environ["VUNIT_MODELSIM_PATH"] = QUESTA_DEFAULT_PATH
else:
    os.environ.setdefault("VUNIT_SIMULATOR", "ghdl")

from vunit import VUnit  # noqa: E402  (import after the simulator selection)


def collect_sources() -> dict[str, list[Path]]:
    """Source files per library over all systems; the systems share the submodules in cores/."""
    libraries: dict[str, list[Path]] = {}
    for file in system_files():
        for library, files in library_files(load(file)).items():
            known = libraries.setdefault(library, [])
            known.extend(f for f in files if f not in known)
    return libraries


def set_simulator_options(vu):
    # Open Logic and the cores use constructs that GHDL accepts only relaxed (shared variables of non-protected types)
    vu.add_compile_option("ghdl.a_flags", ["-frelaxed", "-Wno-hide", "-Wno-shared"])
    vu.add_compile_option("modelsim.vcom_flags", ["-suppress", "1346,1236,1090"])
    # allow_empty: a system without a testbench yet is only compiled
    vu.set_sim_option("ghdl.elab_flags", ["-frelaxed"], allow_empty=True)
    vu.set_sim_option("ghdl.sim_flags", ["--max-stack-alloc=0"], allow_empty=True)
    vu.set_sim_option("disable_ieee_warnings", True, allow_empty=True)
    vu.set_sim_option("modelsim.vsim_flags", ["-suppress", "3009,3473,8684,8683"], allow_empty=True)


def main():
    vu = VUnit.from_argv()
    vu.add_vhdl_builtins()
    for library, files in collect_sources().items():
        vu.add_library(library).add_source_files(files)
    set_simulator_options(vu)
    vu.main()


if __name__ == "__main__":
    main()
