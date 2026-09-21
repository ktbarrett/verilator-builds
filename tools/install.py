"""Install a published Verilator package or build the requested version on this runner."""

import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .config import CONFIG, PLATFORMS, archive_name, version
from .github import GitHub, GitHubNotFound
from .native import build_native, dependencies
from .package import sha256
from .releases import release_state


def requested_version(value):
    value = value.strip()
    if value == "nightly":
        return value
    tag = value if value.startswith("v") else "v" + value
    if version(tag) is None:
        raise ValueError("version must be an upstream release such as v5.048 or 5.048, or nightly")
    return tag


def runner_platform():
    system = {"Linux": "linux", "Darwin": "macos"}.get(host_platform.system())
    machine = host_platform.machine().lower()
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if system == "macos" and arch == "aarch64":
        arch = "arm64"
    result = f"{system}-{arch}"
    if result not in PLATFORMS:
        raise ValueError("Supported runners are Linux and macOS on x86-64 or ARM64")
    return result


def published_package(api, requested, platform):
    """Select only the committed generation, ignoring partially uploaded nightlies."""
    release = api.release(requested)
    if release is None or release.get("draft"):
        return None
    state = release_state(release)
    if state is None:
        raise ValueError("Published release has no valid Verilator build metadata")
    if requested != "nightly" and state["label"] != requested:
        raise ValueError("Published release label differs from the requested version")
    if not re.fullmatch(r"[0-9a-f]{40}", state["sha"]):
        raise ValueError("Published release has an invalid upstream commit")
    name = archive_name(state["label"], platform)
    checksums = f"SHA256SUMS-{state['label']}.txt"
    assets = {asset["name"]: asset for asset in api.assets(release) if asset.get("size", 0) > 0}
    if not {name, checksums} <= assets.keys():
        return None
    return {**state, "archive_id": assets[name]["id"], "checksums_id": assets[checksums]["id"]}


def extract_archive(archive, destination):
    with tarfile.open(archive) as package:
        package.extractall(destination, filter="data")
    entries = list(destination.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise ValueError("Expected a single directory in the archive")
    return entries[0]


def install_package(api, requested, platform, state, work, prefix):
    name = archive_name(state["label"], platform)
    checksums = f"SHA256SUMS-{state['label']}.txt"
    base = f"repos/{api.repository}/releases/assets"
    api.download(f"{base}/{state['checksums_id']}", work / checksums)
    api.download(f"{base}/{state['archive_id']}", work / name)
    matches = re.findall(
        rf"^([0-9a-f]{{64}})  {re.escape(name)}$",
        (work / checksums).read_text(),
        re.MULTILINE,
    )
    if len(matches) != 1 or sha256(work / name) != matches[0]:
        raise ValueError(f"SHA-256 checksum mismatch for {name}")
    extracted = extract_archive(work / name, work / "package")
    manifest = json.loads((extracted / "manifest.json").read_text())
    for field, expected in {
        "schema": 1,
        "upstream": CONFIG["upstream"],
        "label": state["label"],
        "sha": state["sha"],
        "recipe": state["recipe"],
        "platform": platform,
        "abi_audited": True,
    }.items():
        if manifest.get(field) != expected:
            raise ValueError(f"Package {field} differs from release metadata")
    actual = manifest["source_version"]
    if not isinstance(actual, str) or version(actual) is None:
        raise ValueError("Package has an invalid source version")
    if requested != "nightly" and actual != requested:
        raise ValueError("Package source version differs from the requested version")
    shutil.move(extracted, prefix)
    return actual, state["sha"]


def build_source(api, requested, work, prefix, platform, jobs, install_dependencies):
    ref = CONFIG["nightly_branch"] if requested == "nightly" else f"refs/tags/{requested}"
    sha = api.commit(CONFIG["upstream"], ref)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Upstream returned an invalid commit")
    archive = work / "source.tar.gz"
    # This API requires JSON negotiation, then redirects to the binary tarball.
    api.download(f"repos/{CONFIG['upstream']}/tarball/{sha}", archive, accept="application/json")
    source = extract_archive(archive, work / "source")
    env = dependencies(platform, build=True, install=install_dependencies)
    actual = build_native(source, prefix, requested, sha, jobs, env)
    # Prove that the installed launcher does not depend on its build directory.
    shutil.rmtree(source)
    return actual, sha


def install(
    requested, repository, token, root, jobs=2, install_dependencies=True, force_source=False
):
    requested = requested_version(requested)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid release repository")
    if jobs < 1:
        raise ValueError("jobs must be positive")
    platform = runner_platform()
    api = GitHub(repository, token=token)
    prefix = root / "verilator"
    try:
        with tempfile.TemporaryDirectory(prefix="build-", dir=root) as temp:
            work = Path(temp)
            state = None if force_source else published_package(api, requested, platform)
            result = None
            if state is not None:
                print(f"Installing {state['label']} for {platform}", flush=True)
                try:
                    result = install_package(api, requested, platform, state, work, prefix)
                except GitHubNotFound:
                    # A nightly can switch generations between lookup and download.
                    print(
                        "Published assets are no longer available; building from source", flush=True
                    )
            built = result is None
            if built:
                print(f"Building {requested} from source for {platform}", flush=True)
                result = build_source(
                    api, requested, work, prefix, platform, jobs, install_dependencies
                )
        actual, sha = result
        env = dependencies(platform, build=False, install=install_dependencies)
        output = subprocess.check_output(
            [str(prefix / "bin/verilator"), "--version"], env=env, text=True
        )
        print(output, end="", flush=True)
        if not re.search(rf"\bVerilator v?{re.escape(actual[1:])}\b", output):
            raise ValueError("Installed Verilator does not report the expected version")
        # Older upstream releases predate VERILATOR_SRC_VERSION support. Their
        # source commit is pinned at download time, but is absent from --version.
        if not built and sha not in output:
            raise ValueError("Installed Verilator does not identify the expected source commit")
        return {
            "version": actual,
            "path": str(prefix),
            "source-sha": sha,
            "built-from-source": str(built).lower(),
        }
    except BaseException:
        if prefix.exists():
            shutil.rmtree(prefix)
        raise


def boolean_input(name, default):
    value = os.environ.get(name, default).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def main():
    # Each invocation gets its own prefix so repeated setup steps cannot replace
    # an installation still referenced by an earlier step's outputs.
    root = Path(tempfile.mkdtemp(prefix="setup-verilator-", dir=os.environ["RUNNER_TEMP"]))
    try:
        outputs = install(
            os.environ["INSTALL_VERSION"],
            os.environ["INSTALL_REPOSITORY"],
            os.environ.get("INSTALL_TOKEN", ""),
            root,
            jobs=int(os.environ.get("INSTALL_JOBS", "2")),
            install_dependencies=boolean_input("INSTALL_DEPENDENCIES", "true"),
            force_source=boolean_input("INSTALL_FORCE_SOURCE", "false"),
        )
        with open(os.environ["GITHUB_PATH"], "a") as stream:
            stream.write(outputs["path"] + "/bin\n")
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            # A runner may already have another installation selected through this variable.
            stream.write(f"VERILATOR_ROOT={outputs['path']}/share/verilator\n")
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.writelines(f"{name}={value}\n" for name, value in outputs.items())
    except BaseException:
        shutil.rmtree(root)
        raise


if __name__ == "__main__":
    main()
