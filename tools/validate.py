"""Check binary dependencies and run a simulation from an extracted distribution."""

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .config import PLATFORMS, ROOT
from .elf import audit_elf
from .macho import audit_macho


def output(*command, **kwargs):
    return subprocess.check_output(list(map(str, command)), text=True, **kwargs)


def audit(binary, platform):
    target = PLATFORMS[platform]
    if platform.startswith("linux-"):
        audit_elf(binary, target["arch"])
    elif platform.startswith("macos-"):
        audit_macho(binary, target["minimum"], target["arch"])


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
