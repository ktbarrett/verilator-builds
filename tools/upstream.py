"""Read Verilator's declared version without executing upstream code."""

import re
import tarfile


def source_version(configure_ac):
    match = re.search(
        r"^\s*AC_INIT\(\s*\[Verilator\]\s*,\s*\[(\d+\.\d{3})(?:\s[^\]]*)?\]",
        configure_ac,
        re.MULTILINE,
    )
    if not match:
        raise ValueError("Cannot read Verilator version from configure.ac")
    return "v" + match[1]


def archive_version(path):
    try:
        with tarfile.open(path) as archive:
            return source_version(archive.extractfile("source/configure.ac").read().decode())
    except (tarfile.TarError, KeyError, UnicodeDecodeError) as error:
        raise ValueError("Invalid upstream source archive") from error
