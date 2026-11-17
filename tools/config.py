"""Shared package naming and configuration."""

import hashlib
import json
import operator
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text())
PLATFORMS = CONFIG["platforms"]


def recipe_digest():
    """Digest the files that can change a package. Other commits do not force rebuilds."""
    ignore = {ROOT / entry for entry in CONFIG["recipe_ignore"]}
    files = []
    for entry in CONFIG["recipe_paths"]:
        path = ROOT / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p
                for p in path.rglob("*")
                if p.is_file()
                and not any(part.startswith(".") for part in p.relative_to(path).parts)
                and "__pycache__" not in p.parts
            )
        else:
            raise ValueError(f"Recipe path is missing: {entry}")
    digest = hashlib.sha256()
    for path in sorted(files):
        if path in ignore:
            continue
        content = path.read_bytes()
        digest.update(f"{path.relative_to(ROOT).as_posix()}\0{len(content)}\0".encode())
        digest.update(content)
    return digest.hexdigest()


def version(tag):
    match = re.fullmatch(r"v(\d+)\.(\d{3})", tag)
    return tuple(map(int, match.groups())) if match else None


def supported_platforms(label):
    """Apply release floors to version tags; build development labels on every target."""
    parsed = version(validate_label(label))
    return [
        name
        for name, target in PLATFORMS.items()
        if parsed is None or parsed >= version(target["first_release"])
    ]


COMPARISONS = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
}


def version_matches(label: str, spec: str) -> bool:
    """Check a label against comma-separated clauses such as ">=v5.056,<v5.060".

    Every clause must hold. Development labels have no version and never match.
    """
    clauses = []
    for clause in spec.split(","):
        match = re.fullmatch(r"\s*(<=|>=|==|!=|<|>)\s*(v\d+\.\d{3})\s*", clause)
        if not match:
            raise ValueError(f"Invalid version clause: {clause!r}")
        clauses.append((COMPARISONS[match[1]], version(match[2])))
    parsed = version(validate_label(label))
    return parsed is not None and all(compare(parsed, bound) for compare, bound in clauses)


def macos_flex(label: str) -> str:
    """Homebrew flex formula for a label.

    Development labels have no version, so they use the "nightly" formula. Release
    labels use the first matching release pin, else the newest flex. A release pin
    without "versions" matches every release.
    """
    config = CONFIG["macos_flex"]
    if version(validate_label(label)) is None:
        return config["nightly"]
    for pin in config["releases"]:
        spec = pin.get("versions")
        if spec is None or version_matches(label, spec):
            return pin["formula"]
    return "flex"


def validate_label(label):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", label):
        raise ValueError(f"Unsafe package label: {label!r}")
    return label


def archive_name(label, platform):
    validate_label(label)
    return f"verilator-{label}-{platform}.tar.gz"


def source_name(label):
    return f"verilator-{validate_label(label)}-source.tar.gz"


def expected_assets(label, source_version=None):
    validate_label(label)
    if version(label):
        if source_version is not None and source_version != label:
            raise ValueError("Release label and source version differ")
    return {archive_name(label, p) for p in supported_platforms(label)} | {
        source_name(label),
        f"manifest-{label}.json",
        f"SHA256SUMS-{label}.txt",
    }
