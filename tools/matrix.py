"""Write platform matrix or bootstrap commit outputs for GitHub Actions."""

import argparse
import json
import os

from .config import CONFIG, PLATFORMS, supported_platforms, version
from .github import GitHub
from .upstream import source_version


def platform_matrix(release):
    platforms = supported_platforms(release)
    if not platforms:
        raise ValueError(f"No platforms support Verilator {release}")
    return {
        "include": [
            {"platform": name, "source_asset": name == platforms[0], **PLATFORMS[name]}
            for name in platforms
        ]
    }


def seed_release():
    # Exercise every target using the oldest release supported by all of them.
    return max((target["first_release"] for target in PLATFORMS.values()), key=version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--seed", action="store_true")
    inputs.add_argument("--sha", help="Immutable upstream commit to select platforms for")
    args = parser.parse_args()
    api = GitHub(os.environ["GITHUB_REPOSITORY"])
    if args.seed:
        result = "sha=" + api.commit(CONFIG["upstream"], f"refs/tags/{seed_release()}")
    else:
        release = source_version(api.file(CONFIG["upstream"], "configure.ac", args.sha))
        result = "matrix=" + json.dumps(
            platform_matrix(release),
            separators=(",", ":"),
        )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(result + "\n")


if __name__ == "__main__":
    main()
