"""Resolve immutable inputs for scheduled and manually requested builds."""

import argparse
import json
import os
import re

from .config import CONFIG, expected_assets, version
from .github import GitHub

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
        expected_assets(state["label"])
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
    return expected_assets(state["label"]) <= names


def discover(api, recipe, mode, ref, run):
    upstream = CONFIG["upstream"]
    if mode != "auto":
        sha = api.commit(upstream, ref)
        if mode == "stable":
            if version(ref) is None:
                raise ValueError("Stable publication requires an upstream vN.NNN release tag")
            tag_sha = api.commit(upstream, f"refs/tags/{ref}")
            if tag_sha != sha:
                raise ValueError("Tag and supplied reference resolve differently")
            label = ref
        else:
            label = f"{mode}-{sha[:12]}-{recipe[:12]}-{run}"
        return [{"sha": sha, "label": label, "mode": mode, "ref": ref}]

    releases = {r["tag_name"]: r for r in api.releases()}
    tags = list(api.pages(f"repos/{upstream}/tags"))
    candidates = sorted(
        (
            t
            for t in tags
            if version(t["name"]) is not None
            and version(t["name"]) >= version(CONFIG["first_release"])
        ),
        key=lambda t: version(t["name"]),
    )
    if not any(t["name"] == CONFIG["first_release"] for t in candidates):
        raise ValueError("Configured first release was not found upstream")
    builds = []
    for tag in candidates:
        name, sha = tag["name"], tag["commit"]["sha"]
        release = releases.get(name)
        if release and complete(release, api.assets(release), sha):
            continue
        builds.append({"sha": sha, "label": name, "mode": "stable", "ref": name})
        if len(builds) == CONFIG["max_releases_per_run"]:
            break

    sha = api.commit(upstream, CONFIG["nightly_branch"])
    nightly = releases.get("nightly")
    if not nightly or not complete(nightly, api.assets(nightly), sha, recipe):
        builds.append(
            {
                "sha": sha,
                "label": f"nightly-{sha[:12]}-{recipe[:12]}-{run}",
                "mode": "nightly",
                "ref": CONFIG["nightly_branch"],
            }
        )
    return builds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["auto", "build", "stable", "nightly"], default="auto")
    parser.add_argument("--ref", default=CONFIG["first_release"])
    args = parser.parse_args()
    builds = discover(
        GitHub(os.environ["GITHUB_REPOSITORY"]),
        os.environ["GITHUB_SHA"],
        args.mode,
        args.ref,
        f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}",
    )
    matrix = json.dumps({"include": builds}, separators=(",", ":"))
    print(matrix)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"matrix={matrix}\nhas_builds={str(bool(builds)).lower()}\n")


if __name__ == "__main__":
    main()
