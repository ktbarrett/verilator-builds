"""Audit ELF dependencies and symbol-version requirements without executing binaries."""

import re

from elftools.elf.dynamic import DynamicSegment
from elftools.elf.elffile import ELFFile
from elftools.elf.gnuversions import GNUVerNeedSection

ALLOWED_LIBRARIES = {
    "libc.so.6",
    "libm.so.6",
    "libpthread.so.0",
    "libdl.so.2",
    "librt.so.1",
    "ld-linux-x86-64.so.2",
    "ld-linux-aarch64.so.1",
}


def audit_elf(binary, arch):
    with binary.open("rb") as stream:
        elf = ELFFile(stream)
        machine = {"x86_64": "EM_X86_64", "aarch64": "EM_AARCH64"}[arch]
        if elf["e_machine"] != machine or elf.elfclass != 64 or not elf.little_endian:
            raise ValueError(f"Wrong ELF architecture; expected {arch}")
        needed = set()
        has_version_requirements = False
        for segment in elf.iter_segments():
            if not isinstance(segment, DynamicSegment):
                continue
            for tag in segment.iter_tags():
                if tag.entry.d_tag == "DT_NEEDED":
                    needed.add(tag.needed)
                elif tag.entry.d_tag in {"DT_RPATH", "DT_RUNPATH"}:
                    raise ValueError("Unexpected ELF library search path")
                elif tag.entry.d_tag == "DT_VERNEED":
                    has_version_requirements = True
        if needed - ALLOWED_LIBRARIES:
            raise ValueError(f"Unexpected shared libraries: {sorted(needed - ALLOWED_LIBRARIES)}")
        requirements = elf.get_section_by_name(".gnu.version_r")
        if has_version_requirements and not isinstance(requirements, GNUVerNeedSection):
            # Our stripped packages retain dynamic sections. Do not silently
            # skip the ABI audit if those sections are missing or malformed.
            raise ValueError("Missing ELF symbol-version requirements")
        if requirements is not None:
            for _, auxiliaries in requirements.iter_versions():
                for auxiliary in auxiliaries:
                    name = auxiliary.name
                    if name.startswith(("GLIBCXX_", "CXXABI_", "GCC_")):
                        raise ValueError("Binary dynamically requires a compiler runtime")
                    match = re.fullmatch(r"GLIBC_([0-9]+(?:\.[0-9]+)+)", name)
                    if match and tuple(map(int, match[1].split("."))) > (2, 17):
                        raise ValueError("Binary requires glibc newer than 2.17")
