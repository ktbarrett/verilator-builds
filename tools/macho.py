"""Audit Mach-O load commands without requiring Apple's command-line tools."""

from macholib import mach_o
from macholib.MachO import MachO
from macholib.ptypes import sizeof

DYLIB_COMMANDS = {
    mach_o.LC_LOAD_DYLIB,
    mach_o.LC_LOAD_WEAK_DYLIB,
    mach_o.LC_REEXPORT_DYLIB,
    mach_o.LC_LAZY_LOAD_DYLIB,
    mach_o.LC_LOAD_UPWARD_DYLIB,
    mach_o.LC_PREBOUND_DYLIB,
}


def audit_macho(binary, minimum, arch):
    macho = MachO(str(binary))
    cpu = {"x86_64": 0x01000007, "arm64": 0x0100000C}[arch]
    if len(macho.headers) != 1 or macho.headers[0].header.cputype != cpu:
        raise ValueError(f"Wrong Mach-O architecture; expected {arch}")
    header = macho.headers[0]
    subtype = 3 if arch == "x86_64" else 0
    if header.header.cpusubtype & 0x00FFFFFF != subtype:
        raise ValueError(f"Wrong Mach-O architecture subtype; expected baseline {arch}")
    versions = []
    for load, command, data in header.commands:
        if load.cmd in DYLIB_COMMANDS:
            offset = command.name - sizeof(load) - sizeof(command)
            end = data.find(b"\0", offset)
            if offset < 0 or end < offset:
                raise ValueError("Invalid Mach-O library name")
            library = data[offset:end].decode()
            if not library.startswith(("/usr/lib/", "/System/Library/")):
                raise ValueError(f"Non-system macOS library: {library}")
        elif load.cmd == mach_o.LC_RPATH:
            raise ValueError("Unexpected Mach-O library search path")
        elif load.cmd == mach_o.LC_BUILD_VERSION:
            if command.platform != 1:  # PLATFORM_MACOS, not Catalyst or iOS.
                raise ValueError("Mach-O deployment target is not macOS")
            versions.append(command.minos)
        elif load.cmd == mach_o.LC_VERSION_MIN_MACOSX:
            versions.append(command.version)
    parts = tuple(map(int, minimum.split(".")))
    major, minor, patch = parts + (0,) * (3 - len(parts))
    maximum = (major << 16) | (minor << 8) | patch
    if not versions or any(version > maximum for version in versions):
        raise ValueError(f"Mach-O deployment target exceeds {minimum}: {versions}")
