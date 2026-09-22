"""Read the publication state shared by release discovery and publication."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .config import expected_assets

MARKER = "verilator-builds-state:"
CommitSHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class _ReleaseState(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    sha: CommitSHA
    recipe: CommitSHA
    label: str
    source_version: Annotated[str, Field(pattern=r"^v[0-9]+\.[0-9]{3}$")] | None = None


def release_state(release):
    match = re.search(r"<!-- " + MARKER + r" (.*?) -->", release.get("body") or "")
    if not match:
        return None
    try:
        state = _ReleaseState.model_validate_json(match[1]).model_dump(exclude_unset=True)
        expected_assets(state["label"], state.get("source_version"))
        return state
    except ValueError:
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
