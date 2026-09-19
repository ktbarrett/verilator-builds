"""Write platform matrix or bootstrap commit outputs for GitHub Actions."""

import argparse
import json
import os

from .config import CONFIG, PLATFORMS
from .github import GitHub


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    if args.seed:
        api = GitHub(os.environ["GITHUB_REPOSITORY"])
        result = "sha=" + api.commit(CONFIG["upstream"], f"refs/tags/{CONFIG['first_release']}")
    else:
        result = "matrix=" + json.dumps(
            {"include": [{"platform": name, **values} for name, values in PLATFORMS.items()]},
            separators=(",", ":"),
        )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(result + "\n")


if __name__ == "__main__":
    main()
