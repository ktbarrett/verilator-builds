"""Check binary dependencies and run a simulation from an extracted distribution."""

import argparse
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .config import PLATFORMS, ROOT


def output(*command, **kwargs):
    return subprocess.check_output(list(map(str, command)), text=True, **kwargs)


def check_linux(dynamic, symbols, header, arch):
    expected = "Advanced Micro Devices X86-64" if arch == "x86_64" else "AArch64"
    if expected not in header:
        raise ValueError(f"Wrong ELF architecture; expected {arch}")
    allowed = {
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
        "libdl.so.2",
        "librt.so.1",
        "ld-linux-x86-64.so.2",
        "ld-linux-aarch64.so.1",
    }
    needed = set(re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic))
    if needed - allowed:
        raise ValueError(f"Unexpected shared libraries: {sorted(needed - allowed)}")
    if "(RPATH)" in dynamic or "(RUNPATH)" in dynamic:
        raise ValueError("Unexpected ELF library search path")
    # objdump uses '(GLIBC_2.x)' on undefined symbols; readelf uses '@GLIBC_2.x'.
    versions = re.findall(r"\bGLIBC_(\d+(?:\.\d+)+)", symbols)
    if any(tuple(map(int, v.split("."))) > (2, 17) for v in versions):
        raise ValueError("Binary requires glibc newer than 2.17")
    if re.search(r"\b(GLIBCXX_|CXXABI_|GCC_)", symbols):
        raise ValueError("Binary dynamically requires a compiler runtime")


def check_macos(libraries, commands, architecture, minimum, arch):
    if architecture.strip() != arch:
        raise ValueError(f"Wrong Mach-O architecture: {architecture}")
    for line in libraries.splitlines()[1:]:
        name = line.strip().split(" (", 1)[0]
        if not name.startswith(("/usr/lib/", "/System/Library/")):
            raise ValueError(f"Non-system macOS library: {name}")
    versions = re.findall(r"\bminos\s+(\d+(?:\.\d+)+)", commands)
    versions += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)", commands)

    def parts(value):
        numbers = tuple(map(int, value.split(".")))
        return numbers + (0,) * (3 - len(numbers))

    if not versions or any(parts(v) > parts(minimum) for v in versions):
        raise ValueError(f"Mach-O deployment target exceeds {minimum}: {versions}")
    if "LC_RPATH" in commands:
        raise ValueError("Unexpected Mach-O library search path")


def audit(binary, platform):
    target = PLATFORMS[platform]
    if platform.startswith("linux-"):
        check_linux(
            output("readelf", "-d", binary),
            output("objdump", "-T", binary),
            output("readelf", "-h", binary),
            target["arch"],
        )
    elif platform.startswith("macos-"):
        check_macos(
            output("otool", "-L", binary),
            output("otool", "-l", binary),
            output("lipo", "-archs", binary),
            target["minimum"],
            target["arch"],
        )


def validate_archive(archive, platform, sha, skip_abi_audit=False):
    with tempfile.TemporaryDirectory(prefix="verilator-smoke-") as temp:
        root = Path(temp)
        with tarfile.open(archive) as package:
            package.extractall(root, filter="data")
        (install,) = root.iterdir()
        manifest = json.loads((install / "manifest.json").read_text())
        if manifest["sha"] != sha or manifest["platform"] != platform:
            raise ValueError("Archive provenance does not match requested build")
        binaries = list((install / "bin").glob("verilator*bin*"))
        if len(binaries) < 3:
            raise ValueError("Missing optimized, debug, or coverage executable")
        if not skip_abi_audit:
            for binary in binaries:
                audit(binary, platform)
        env = os.environ.copy()
        for key in ("VERILATOR_ROOT", "VERILATOR_BIN", "VERILATOR_TEST_FLAGS"):
            env.pop(key, None)
        env["PATH"] = str(install / "bin") + os.pathsep + env["PATH"]
        verilator = ["perl", str(install / "bin/verilator")]
        version = output(*verilator, "--version", env=env)
        print(version, end="", flush=True)
        if sha not in version:
            raise ValueError("Verilator version does not identify the source commit")
        fixture = ROOT / "tests/smoke"
        model = root / "model"
        model.mkdir()
        subprocess.run(
            [*verilator, "--lint-only", str(fixture / "top.sv")], cwd=model, env=env, check=True
        )
        subprocess.run(
            [
                *verilator,
                "--cc",
                "--exe",
                "--trace",
                "--coverage",
                "--top-module",
                "top",
                "--Mdir",
                "obj_dir",
                str(fixture / "top.sv"),
                str(fixture / "main.cpp"),
            ],
            cwd=model,
            env=env,
            check=True,
        )
        subprocess.run(
            ["make", "-C", "obj_dir", "-f", "Vtop.mk", "-j2"], cwd=model, env=env, check=True
        )
        executable = model / "obj_dir/Vtop"
        result = output(executable, cwd=model, env=env)
        if "simulation passed" not in result:
            raise ValueError("Simulation failed")
        for artifact in ("trace.vcd", "coverage.dat"):
            if not (model / artifact).stat().st_size:
                raise ValueError(f"Missing simulation artifact: {artifact}")
        subprocess.run(
            [
                "perl",
                str(install / "bin/verilator_coverage"),
                "--write-info",
                "coverage.info",
                "coverage.dat",
            ],
            cwd=model,
            env=env,
            check=True,
        )
        print(f"Validated {archive.name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    validate_archive(args.archive, args.platform, args.sha)


if __name__ == "__main__":
    main()
