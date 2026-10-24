"""Find every release that the current recipe has not built, plus the nightly."""

import argparse
import json
import os

from .config import CONFIG, PLATFORMS, recipe_digest, supported_platforms, version
from .github import GitHub
from .releases import complete


def platform_matrix(label):
    platforms = supported_platforms(label)
    if not platforms:
        raise ValueError(f"No platforms support Verilator {label}")
    return [
        {"platform": name, "source_asset": name == platforms[0], **PLATFORMS[name]}
        for name in platforms
    ]


def discover(api, recipe, digest, run):
    upstream = CONFIG["upstream"]
    releases = {r["tag_name"]: r for r in api.releases()}
    tags = api.tags(upstream)
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
        if release and complete(release, api.assets(release), sha, digest):
            continue
        builds.append({"sha": sha, "label": name, "mode": "stable"})

    sha = api.commit(upstream, CONFIG["nightly_branch"])
    nightly = releases.get("nightly")
    if not nightly or not complete(nightly, api.assets(nightly), sha, digest):
        builds.append(
            {
                "sha": sha,
                "label": f"nightly-{sha[:12]}-{recipe[:12]}-{run}",
                "mode": "nightly",
            }
        )
    return builds


def check_builds(api):
    """Upstream master and the oldest supported release, to verify a recipe change."""
    upstream, oldest = CONFIG["upstream"], CONFIG["first_release"]
    return [
        {"sha": api.commit(upstream, CONFIG["nightly_branch"]), "label": "master"},
        {"sha": api.commit(upstream, f"refs/tags/{oldest}"), "label": oldest},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="List pull request builds instead of stale releases"
    )
    args = parser.parse_args()
    api = GitHub(os.environ["GITHUB_REPOSITORY"])
    if args.check:
        builds = check_builds(api)
    else:
        builds = discover(
            api,
            os.environ["GITHUB_SHA"],
            recipe_digest(),
            f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}",
        )
    jobs = [{**build, **target} for build in builds for target in platform_matrix(build["label"])]
    outputs = {
        "releases": json.dumps({"include": builds}, separators=(",", ":")),
        "builds": json.dumps({"include": jobs}, separators=(",", ":")),
        "has_builds": str(bool(builds)).lower(),
    }
    print(json.dumps(builds, indent=2))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.writelines(f"{key}={value}\n" for key, value in outputs.items())


if __name__ == "__main__":
    main()
