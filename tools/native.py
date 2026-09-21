"""Build an installation for the action's runner, without portable-package ABI constraints."""

import os
import shutil
import subprocess

from .upstream import source_version


def run(command, **kwargs):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def dependencies(platform, build, install):
    """Prepare runtime/build tools and return the environment for native commands."""
    env = os.environ.copy()
    for name in ("VERILATOR_ROOT", "VERILATOR_BIN", "VERILATOR_TEST_FLAGS", "INSTALL_TOKEN"):
        env.pop(name, None)
    macos = platform.startswith("macos-")
    if install and macos and build:
        run(["brew", "install", "autoconf", "bison", "flex", "help2man"], env=env)
    if macos and shutil.which("brew"):
        # Apple's bison is too old for current Verilator; these formulae are keg-only.
        for formula in ("bison", "flex"):
            prefix = subprocess.run(
                ["brew", "--prefix", formula], capture_output=True, text=True, check=False
            )
            if prefix.returncode == 0:
                env["PATH"] = prefix.stdout.strip() + "/bin:" + env["PATH"]
    required = ["make", "perl", "clang++" if macos else "g++"]
    if build:
        required += ["autoconf", "bison", "flex", "help2man"]
    missing = [name for name in required if not shutil.which(name, path=env["PATH"])]
    if install and not macos and (build or missing):
        if not shutil.which("apt-get"):
            raise ValueError(
                "Automatic dependency installation requires apt-get; install build tools "
                "yourself and set install-dependencies: 'false'"
            )
        sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]
        run([*sudo, "apt-get", "update"], env=env)
        packages = ["g++", "make", "perl"]
        if build:
            packages += ["autoconf", "bison", "flex", "help2man", "libfl-dev", "zlib1g-dev"]
        run([*sudo, "apt-get", "install", "-y", "--no-install-recommends", *packages], env=env)
        missing = [name for name in required if not shutil.which(name, path=env["PATH"])]
    if missing:
        raise ValueError("Missing required tools: " + ", ".join(missing))
    return env


def build_native(source, prefix, requested, sha, jobs, env):
    """Install at its final prefix; upstream handles the native installation layout."""
    if any(c.isspace() for path in (source, prefix) for c in str(path)):
        raise ValueError("Verilator source and installation paths cannot contain whitespace")
    actual = source_version((source / "configure.ac").read_text())
    if requested != "nightly" and actual != requested:
        raise ValueError(f"Requested {requested}, but upstream source declares {actual}")
    env = {**env, "VERILATOR_SRC_VERSION": f"{actual} ({sha})", "CCACHE_DISABLE": "1"}
    run(["autoconf"], cwd=source, env=env)
    run(
        [
            "sh",
            "./configure",
            f"--prefix={prefix}",
            "--disable-defenv",
            "--disable-tcmalloc",
            "--disable-partial-static",
        ],
        cwd=source,
        env=env,
    )
    run(["make", f"-j{jobs}"], cwd=source, env=env)
    run(["make", "install"], cwd=source, env=env)
    return actual
