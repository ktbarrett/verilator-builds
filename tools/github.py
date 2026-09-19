"""Small GitHub API adapter using the runner's authenticated gh CLI."""

import base64
import json
import subprocess
from urllib.parse import quote


class GitHub:
    def __init__(self, repository):
        self.repository = repository

    def api(self, endpoint, method="GET", data=None):
        command = ["gh", "api", "--method", method, endpoint]
        if data is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command,
            input=json.dumps(data) if data is not None else None,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

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
        subprocess.run(
            [
                "gh",
                "release",
                "upload",
                tag,
                "--repo",
                self.repository,
                "--clobber",
                *map(str, paths),
            ],
            check=True,
        )
