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


def supported_platforms(source_version):
    """Apply inclusive per-platform release floors to a source version."""
    parsed = version(source_version)
    if parsed is None:
        raise ValueError(f"Invalid source version: {source_version!r}")
    return [
        name for name, target in PLATFORMS.items() if parsed >= version(target["first_release"])
    ]


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


def expected_assets(label, source_version=None):
    validate_label(label)
    if version(label):
        if source_version is not None and source_version != label:
            raise ValueError("Release label and source version differ")
        source_version = label
    # Old nightly markers predate platform floors and always describe all targets.
    platforms = PLATFORMS if source_version is None else supported_platforms(source_version)
    return {archive_name(label, p) for p in platforms} | {
        source_name(label),
        f"manifest-{label}.json",
        f"SHA256SUMS-{label}.txt",
    }
