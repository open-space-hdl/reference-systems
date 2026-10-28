"""VUnit run script of the reference systems.

Compiles Open Logic FT and the cores into their libraries (olo, openrmap, openspp, openfibre, opencrypto, ...)
exactly as the Composer files of systems/ describe them (section `sources`, submodules in cores/), then runs the
system testbenches. Vendor-specific blocks (for example the GTY adapter ofb_pa_gty) need the vendor libraries and are
left out here.

GHDL is the default simulator; `--questa` selects QuestaSim. `python run.py --compile` analyses all sources.
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

from composer_system import library_files, load, system_files  # noqa: E402

# Simulator selection must happen before VUnit parses the command line
if "--questa" in sys.argv:
    sys.argv.remove("--questa")
    os.environ["VUNIT_SIMULATOR"] = "modelsim"
    # QuestaSim from VUNIT_MODELSIM_PATH (the directory of vsim) or from the PATH
    if "VUNIT_MODELSIM_PATH" not in os.environ:
        vsim = shutil.which("vsim")
        if vsim is None:
            sys.exit("QuestaSim not found: set VUNIT_MODELSIM_PATH to the directory of vsim or put vsim on the PATH")
        os.environ["VUNIT_MODELSIM_PATH"] = str(Path(vsim).parent)
else:
    os.environ.setdefault("VUNIT_SIMULATOR", "ghdl")

from vunit import VUnit  # noqa: E402  (import after the simulator selection)
from vunit.sim_if.ghdl import GHDLInterface  # noqa: E402


def own_sim_flags(get_command):
    """VUnit 5.0.0.dev7 appends the generics of a test to the ghdl.sim_flags list in place, and all configurations
    share that list. Every simulation gets its own copy, and the command must carry exactly its own generics."""

    def wrapper(self, config, *args, **kwargs):
        config.sim_options = {**config.sim_options,
                              "ghdl.sim_flags": list(config.sim_options.get("ghdl.sim_flags", []))}
        cmd = get_command(self, config, *args, **kwargs)
        generics = [arg for arg in cmd if arg.startswith("-g")]
        expected = [f"-g{name}={value}" for name, value in config.generics.items()]
        if generics != expected:
            sys.exit(f"GHDL command with the generics {generics}, expected {expected}")
        return cmd

    return wrapper


GHDLInterface._get_command = own_sim_flags(GHDLInterface._get_command)


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
