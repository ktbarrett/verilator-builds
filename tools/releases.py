"""Read the publication state shared by release discovery and publication."""

import json
import re

from .config import expected_assets

MARKER = "verilator-builds-state:"


def release_state(release):
    match = re.search(r"<!-- " + MARKER + r" (.*?) -->", release.get("body") or "")
    if not match:
        return None
    try:
        state = json.loads(match[1])
        if not isinstance(state, dict) or not all(
            isinstance(state.get(key), str) for key in ("sha", "recipe", "label")
        ):
            return None
        source_version = state.get("source_version")
        if source_version is not None and not isinstance(source_version, str):
            return None
        expected_assets(state["label"], source_version)
        return state
    except (json.JSONDecodeError, ValueError):
        return None


def complete(release, assets, sha, recipe=None):
    state = release_state(release)
    if release.get("draft") or not state or state.get("sha") != sha:
        return False
    if recipe is not None and state.get("recipe") != recipe:
        return False
    names = {a["name"] for a in assets if a.get("size", 0) > 0}
    tag = release.get("tag_name")
    if tag != "nightly" and (release.get("prerelease") or state["label"] != tag):
        return False
    if tag == "nightly" and not release.get("prerelease"):
        return False
    return expected_assets(state["label"], state.get("source_version")) <= names
