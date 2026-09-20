"""Write platform matrix or bootstrap commit outputs for GitHub Actions."""

import argparse
import json
import os

from .config import CONFIG, PLATFORMS, supported_platforms
from .github import GitHub


def platform_matrix(label):
    platforms = supported_platforms(label)
    if not platforms:
        raise ValueError(f"No platforms support Verilator {label}")
    return {
        "include": [
            {"platform": name, "source_asset": name == platforms[0], **PLATFORMS[name]}
            for name in platforms
        ]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--seed", action="store_true", help="Resolve upstream HEAD for CI")
    inputs.add_argument("--label", help="Release tag or development label to select platforms for")
    args = parser.parse_args()
    if args.seed:
        api = GitHub(os.environ["GITHUB_REPOSITORY"])
        result = "sha=" + api.commit(CONFIG["upstream"], CONFIG["nightly_branch"])
    else:
        result = "matrix=" + json.dumps(
            platform_matrix(args.label),
            separators=(",", ":"),
        )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(result + "\n")


if __name__ == "__main__":
    main()
