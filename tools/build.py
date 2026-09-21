"""Build, package, and validate an exact Verilator commit in a private work directory."""

import argparse
import os
import platform as host_platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import PLATFORMS, supported_platforms, validate_label
from .package import create_archive, export_source, normalize_install, sha256, source_archive_path
from .upstream import source_version
from .validate import validate_archive


def run(command, **kwargs):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def linux_library_override(source, compiler, env):
    """Select static libatomic in Verilator's link libraries, after the objects."""
    makefile = source / "src/Makefile_obj"
    matches = re.findall(r"^CFG_LIBS[ \t]*=[ \t]*(.*)$", makefile.read_text(), re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one CFG_LIBS assignment in {makefile}")
    libraries, count = re.subn(r"(?<!\S)-latomic(?!\S)", "-l:libatomic.a", matches[0])
    if not count:
        return None
    archive = subprocess.check_output(
        [compiler, "-print-file-name=libatomic.a"], env=env, text=True
    ).strip()
    if archive == "libatomic.a" or not Path(archive).is_file():
        raise ValueError(
            f"{compiler} cannot find libatomic.a; install the static libatomic package"
        )
    print(f"Linking static libatomic from {archive}", flush=True)
    return f"CFG_LIBS={libraries}"


def build(args):
    target = PLATFORMS[args.platform]
    validate_label(args.label)
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise ValueError("--sha must be a full upstream commit SHA")
    actual = subprocess.check_output(
        ["git", "-C", str(args.source), "rev-parse", f"{args.sha}^{{commit}}"],
        text=True,
    ).strip()
    if actual != args.sha:
        raise ValueError("Source commit does not match the requested SHA")
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=args.work) as temp:
        work = Path(temp).resolve()
        source, install = work / "source", work / "install"
        source_archive = source_archive_path(args.output, args.label)
        export_source(args.source, args.sha, source, source_archive)
        release = source_version((source / "configure.ac").read_text())
        if args.platform not in supported_platforms(args.label):
            raise ValueError(
                f"{args.platform} requires Verilator {target['first_release']} or newer; "
                f"source declares {release}"
            )
        env = os.environ.copy()
        for key in (
            "VERILATOR_ROOT",
            "VERILATOR_BIN",
            "VERILATOR_SOLVER",
            "SYSTEMC",
            "SYSTEMC_INCLUDE",
            "SYSTEMC_LIBDIR",
            "CPATH",
            "LIBRARY_PATH",
        ):
            env.pop(key, None)
        compiler = "clang++" if args.platform.startswith("macos-") else "g++"
        env.update(
            CXX=compiler,
            CC="clang" if compiler == "clang++" else "gcc",
            CCACHE_DISABLE="1",
            VERILATOR_SRC_VERSION=f"{args.label} ({args.sha})",
        )
        archflags = "-march=x86-64 -mtune=generic" if target["arch"] == "x86_64" else ""
        if args.platform == "linux-aarch64":
            archflags = "-march=armv8-a"
        env.update(CFLAGS=archflags, CXXFLAGS=archflags, CPPFLAGS="", LDFLAGS="", LIBS="")
        configure = [
            "sh",
            "./configure",
            f"--prefix={install}",
            "--disable-defenv",
            "--disable-tcmalloc",
            "--disable-jemalloc",
        ]
        if args.platform.startswith("macos-"):
            env["MACOSX_DEPLOYMENT_TARGET"] = target["minimum"]
            env["CXXFLAGS"] += " -Werror=unguarded-availability-new"
            env["SDKROOT"] = subprocess.check_output(
                ["xcrun", "--show-sdk-path"],
                text=True,
            ).strip()
            configure.append("--disable-partial-static")
        else:
            env["LDFLAGS"] = "-static-libgcc -static-libstdc++"
        run(["autoconf"], cwd=source, env=env)
        run(configure, cwd=source, env=env)
        # Build and ship upstream's optimized, debug, and coverage executables.
        # Strip debug symbols below to bound download size.
        make_args = [f"-j{args.jobs}"]
        if args.platform.startswith("linux-"):
            override = linux_library_override(source, compiler, env)
            if override is not None:
                make_args.append(override)
        run(["make", *make_args], cwd=source, env=env)
        run(["make", "install", *make_args], cwd=source, env=env)
        normalize_install(install, source, args.platform)
        for binary in (install / "bin").glob("verilator*bin*"):
            run(
                [
                    "strip",
                    "-x" if args.platform.startswith("macos-") else "--strip-unneeded",
                    binary,
                ],
                env=env,
            )
        build_info = {
            "source_version": release,
            "compiler": subprocess.check_output([compiler, "--version"], env=env, text=True),
            "host": host_platform.platform(),
            "configure": [arg.replace(str(install), "<prefix>") for arg in configure],
            "flags": {key: env[key] for key in ("CXXFLAGS", "LDFLAGS", "LIBS")},
            "make_overrides": make_args[1:],
            "image": os.environ.get("BUILD_IMAGE", ""),
            "abi_audited": not args.skip_abi_audit,
            "source_sha256": sha256(source_archive),
        }
        archive = create_archive(
            install, args.output, args.platform, args.label, args.sha, args.recipe, build_info
        )
        # Source and staging paths must be unavailable during the smoke test.
        shutil.rmtree(source)
        validate_archive(archive, args.platform, args.sha, args.skip_abi_audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--platform", choices=PLATFORMS, required=True)
    parser.add_argument("--work", type=lambda p: Path(p).resolve(), default=Path("work").resolve())
    parser.add_argument(
        "--output", type=lambda p: Path(p).resolve(), default=Path("dist").resolve()
    )
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 4))
    parser.add_argument(
        "--skip-abi-audit",
        action="store_true",
        help="Local development only; do not claim baseline compatibility",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if any(any(c.isspace() for c in str(path)) for path in (args.work, args.output)):
        parser.error("Build and output paths cannot contain whitespace (upstream make limitation)")
    if args.skip_abi_audit and os.environ.get("GITHUB_ACTIONS") == "true":
        parser.error("ABI audits cannot be disabled in CI")
    build(args)


if __name__ == "__main__":
    main()
