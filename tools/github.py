"""Small GitHub API adapter using the runner's authenticated gh CLI."""

import base64
import json
import os
import subprocess
import sys
from urllib.parse import quote


class GitHubNotFound(RuntimeError):
    """The requested GitHub resource returned HTTP 404."""


class GitHub:
    def __init__(self, repository, token=None):
        self.repository = repository
        # Keep the action's token local to gh subprocesses, out of source builds.
        self.env = (
            None if token is None else {**os.environ, "GH_TOKEN": token, "GH_HOST": "github.com"}
        )

    def run(self, arguments, **kwargs):
        try:
            return subprocess.run(["gh", *arguments], env=self.env, check=True, **kwargs)
        except subprocess.CalledProcessError as error:
            if error.stderr:
                print(error.stderr.rstrip(), file=sys.stderr, flush=True)
            if "(HTTP 404)" in (error.stderr or ""):
                raise GitHubNotFound(error.stderr.strip()) from error
            raise

    def api(self, endpoint, method="GET", data=None):
        command = ["api", "--method", method, endpoint]
        if data is not None:
            command += ["--input", "-"]
        result = self.run(
            command,
            input=json.dumps(data) if data is not None else None,
            text=True,
            capture_output=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def release(self, tag):
        try:
            return self.api(f"repos/{self.repository}/releases/tags/{quote(tag, safe='')}")
        except GitHubNotFound:
            return None

    def download(self, endpoint, destination):
        """Stream release assets or upstream source tarballs directly to disk."""
        with destination.open("wb") as stream:
            self.run(
                [
                    "api",
                    "--method",
                    "GET",
                    endpoint,
                    "--header",
                    "Accept: application/octet-stream",
                ],
                stdout=stream,
                stderr=subprocess.PIPE,
                text=True,
            )

    def pages(self, endpoint):
        page = 1
        while True:
            separator = "&" if "?" in endpoint else "?"
            items = self.api(f"{endpoint}{separator}per_page=100&page={page}")
            yield from items
            if len(items) < 100:
                return
            page += 1

    def releases(self):
        return list(self.pages(f"repos/{self.repository}/releases"))

    def assets(self, release):
        return list(self.pages(f"repos/{self.repository}/releases/{release['id']}/assets"))

    def commit(self, repository, ref):
        return self.api(f"repos/{repository}/commits/{quote(ref, safe='')}")["sha"]

    def file(self, repository, path, ref):
        result = self.api(
            f"repos/{repository}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"
        )
        if result.get("encoding") != "base64":
            raise ValueError(f"Unsupported content encoding for {path}")
        return base64.b64decode(result["content"]).decode()

    def upload(self, tag, paths):
        self.run(
            [
                "release",
                "upload",
                tag,
                "--repo",
                self.repository,
                "--clobber",
                *map(str, paths),
            ],
        )
