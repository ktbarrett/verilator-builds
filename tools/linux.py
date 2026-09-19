"""Run the Linux build in manylinux without running Node-based Actions inside it."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .config import PLATFORMS, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=[p for p in PLATFORMS if p.startswith("linux-")], required=True
    )
    parser.add_argument("--sha", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        # Preserve the compiler selected by the manylinux image.
        os.environ["PATH"] = "/opt/python/cp312-cp312/bin:" + os.environ["PATH"]
        subprocess.run(
            [
                "yum",
                "install",
                "-y",
                "autoconf",
                "bison",
                "flex",
                "make",
                "perl",
                "help2man",
                "zlib-devel",
                "libatomic-static",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", "/workspace/upstream"],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.build",
                "--source",
                "/workspace/upstream",
                "--platform",
                args.platform,
                "--sha",
                args.sha,
                "--label",
                args.label,
                "--recipe",
                args.recipe,
            ],
            check=True,
        )
        return
    image = PLATFORMS[args.platform]["image"]
    subprocess.run(["docker", "pull", image], check=True)
    digest = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image],
        text=True,
    ).strip()
    for directory in ("dist", "work"):
        (ROOT / directory).mkdir(exist_ok=True)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{ROOT}:/workspace",
                "-w",
                "/workspace",
                "-e",
                f"BUILD_IMAGE={digest}",
                "-e",
                "GITHUB_ACTIONS=true",
                digest,
                "/opt/python/cp312-cp312/bin/python3",
                "-m",
                "tools.linux",
                "--inside",
                *sys.argv[1:],
            ],
            check=True,
        )
    finally:
        # The container builds as root; let the host upload and clean its outputs.
        subprocess.run(
            [
                "sudo",
                "chown",
                "-R",
                f"{os.getuid()}:{os.getgid()}",
                str(Path("dist")),
                str(Path("work")),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
