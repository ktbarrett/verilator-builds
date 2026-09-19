"""Assemble relocatable Verilator distributions and their provenance."""

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

from .config import CONFIG, PLATFORMS, ROOT, archive_name, source_name


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export_source(checkout, sha, destination, archive):
    """Export tracked source without touching the caller's working tree."""
    entries = subprocess.check_output(
        ["git", "-C", str(checkout), "ls-tree", "-r", sha],
        text=True,
    )
    if any(line.startswith("160000 ") for line in entries.splitlines()):
        raise ValueError("Upstream now has submodules; extend source export before packaging")
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "archive",
            "--format=tar.gz",
            f"--prefix={destination.name}/",
            f"--output={archive}",
            sha,
        ],
        check=True,
    )
    with tarfile.open(archive) as source:
        source.extractall(destination.parent, filter="data")


def relocate_launchers(install):
    """Set Perl launcher paths to match our archive layout on every host."""
    launcher = install / "bin/verilator"
    contents, count = re.subn(
        r"^my \$verilator_pkgdatadir_relpath = .*;$",
        'my $verilator_pkgdatadir_relpath = "../share/verilator";',
        launcher.read_text(),
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError("Expected exactly one data-directory assignment in bin/verilator")
    launcher.write_text(contents)
    # These public-script redirects use the same install-time path substitution.
    # Native executables are replaced separately; private utilities have no redirect.
    for script in (install / "share/verilator/bin").iterdir():
        with script.open("rb") as stream:
            if stream.read(2) != b"#!":
                continue
        contents, count = re.subn(
            r"^my \$relpath = .*;[^\r\n]*$",
            'my $relpath = "../../../bin";',
            script.read_text(),
            flags=re.MULTILINE,
        )
        if count:
            script.write_text(contents)


def normalize_install(install, source, platform):
    data = install / "share/verilator"
    relocate_launchers(install)
    compiler = "clang++" if platform.startswith("macos-") else "g++"
    replacements = {
        "AR": "ar",
        "CXX": compiler,
        "LINK": compiler,
        "PERL": "perl",
        "PYTHON3": "python3",
        "OBJCACHE": "",
        # A build host's optional mold installation must not leak to consumers.
        "CFG_LDFLAGS_VERILATED": "",
    }
    makefile = data / "include/verilated.mk"
    contents = makefile.read_text()
    for variable, value in replacements.items():
        contents, count = re.subn(
            rf"^{variable}\s*\??=.*$",
            f"{variable} = {value}",
            contents,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError(f"Expected exactly one {variable} in {makefile}")
    makefile.write_text(contents)
    pc = install / "share/pkgconfig/verilator.pc"
    pc.write_text(
        re.sub(r"^prefix=.*$", "prefix=${pcfiledir}/../..", pc.read_text(), flags=re.MULTILINE)
    )
    # Upstream installs Perl redirects here, even for .exe files. CMake locates
    # verilator_bin here and must get a real native executable on Windows.
    for binary in (install / "bin").glob("verilator*bin*"):
        shutil.copy2(binary, data / "bin" / binary.name)
    shutil.copy2(source / "LICENSE", install / "LICENSE.verilator")
    shutil.copytree(source / "LICENSES", install / "LICENSES", dirs_exist_ok=True)
    shutil.copytree(ROOT / "licenses", install / "LICENSES", dirs_exist_ok=True)
    if platform.startswith("windows-"):
        # Include the installed MinGW runtime and winpthreads notices too.
        license_root = Path("/ucrt64/share/licenses")
        found = False
        for license_dir in license_root.glob("*"):
            if any(name in license_dir.name for name in ("mingw-w64", "crt", "winpthread")):
                shutil.copytree(
                    license_dir, install / "LICENSES" / license_dir.name, dirs_exist_ok=True
                )
                found = True
        if not found:
            raise ValueError("MSYS2 runtime license files were not found")
    (install / "README.txt").write_text(
        "Verilator portable distribution\n\n"
        "Add this directory's bin/ to PATH; leave VERILATOR_ROOT unset.\n"
        "Requires Perl, Python 3, GNU make, and a compatible C++ compiler.\n"
        "Windows: use the MSYS2 UCRT64 shell and MinGW-w64 GCC.\n"
        "Use installation and simulation build paths without spaces.\n"
        "Optional features may require zlib, an SMT solver, or SystemC.\n"
        "See manifest.json and the release page for source and build details.\n"
        "Verilator is licensed under LGPL-3.0-only OR Artistic-2.0; see LICENSES.\n"
    )
    for metadata in [makefile, pc, *data.glob("*.cmake")]:
        contents = metadata.read_text()
        for build_path in (str(source), str(install)):
            if build_path in contents:
                raise ValueError(f"Build path leaked into installed metadata: {metadata}")


def create_archive(install, output, platform, label, sha, recipe, build_info):
    manifest = {
        "schema": 1,
        "upstream": CONFIG["upstream"],
        "sha": sha,
        "recipe": recipe,
        "label": label,
        "platform": platform,
        "minimum": PLATFORMS[platform]["minimum"],
        **build_info,
    }
    (install / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    name = archive_name(label, platform)
    root = output / name.removesuffix(".tar.gz").removesuffix(".zip")
    # Archive root is independent of the temporary staging directory name.
    shutil.move(install, root)
    try:
        if name.endswith(".zip"):
            shutil.make_archive(str(root), "zip", root.parent, root.name)
        else:
            with tarfile.open(output / name, "w:gz") as archive:
                archive.add(root, arcname=root.name)
    finally:
        shutil.rmtree(root)
    manifest["archive"] = name
    manifest["sha256"] = sha256(output / name)
    (output / f"{platform}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return output / name


def source_archive_path(output, label):
    return output / source_name(label)
