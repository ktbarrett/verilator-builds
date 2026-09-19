"""Shared package naming and configuration."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text())
PLATFORMS = CONFIG["platforms"]


def version(tag):
    match = re.fullmatch(r"v(\d+)\.(\d{3})", tag)
    return tuple(map(int, match.groups())) if match else None


def validate_label(label):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", label):
        raise ValueError(f"Unsafe package label: {label!r}")
    return label


def archive_name(label, platform):
    validate_label(label)
    suffix = "zip" if platform.startswith("windows-") else "tar.gz"
    return f"verilator-{label}-{platform}.{suffix}"


def source_name(label):
    return f"verilator-{validate_label(label)}-source.tar.gz"


def expected_assets(label):
    return {archive_name(label, p) for p in PLATFORMS} | {
        source_name(label),
        f"manifest-{label}.json",
        f"SHA256SUMS-{label}.txt",
    }
