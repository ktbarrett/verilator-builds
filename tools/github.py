"""GitHub operations backed by PyGithub; publication policy lives in tools.publish."""

import os

from github import Auth, Github
from github.GitRelease import GitRelease
from github.GitReleaseAsset import GitReleaseAsset


class GitHub:
    def __init__(self, repository):
        self.repository = repository
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        # Do not replay a possibly successful upload or release mutation. The
        # scheduler reconciles remote state and retries incomplete publications.
        self.client = Github(
            auth=Auth.Token(token) if token else None, retry=0, timeout=60, lazy=True
        )
        self.repo = self.client.get_repo(repository)

    def releases(self):
        fields = ("id", "url", "tag_name", "name", "body", "draft", "prerelease", "upload_url")
        return [
            {field: getattr(release, field) for field in fields}
            for release in self.repo.get_releases()
        ]

    def tags(self, repository):
        return [tag.raw_data for tag in self.client.get_repo(repository).get_tags()]

    def _release(self, release):
        return self.client.create_from_raw_data(GitRelease, release)

    def assets(self, release):
        fields = ("id", "url", "name", "size", "digest")
        return [
            {field: getattr(asset, field) for field in fields}
            for asset in self._release(release).get_assets()
        ]

    def commit(self, repository, ref):
        return self.client.get_repo(repository).get_commit(ref).complete().sha

    def create_release(self, tag, recipe, nightly):
        return self.repo.create_git_release(
            tag,
            tag,
            "",
            draft=True,
            prerelease=nightly,
            target_commitish=recipe,
            make_latest="false",
        ).raw_data

    def update_release(self, release, **changes):
        fields = {
            "name": release.get("name") or release["tag_name"],
            "body": release.get("body") or "",
            "draft": release["draft"],
            "prerelease": release["prerelease"],
            **changes,
        }
        fields["message"] = fields.pop("body")
        return self._release(release).update_release(**fields).raw_data

    def delete_asset(self, asset):
        self.client.create_from_raw_data(GitReleaseAsset, asset).delete_asset()

    def upload(self, release, paths):
        remote = self._release(release)
        existing = {asset.name: asset for asset in remote.get_assets()}
        for path in paths:
            # Match gh release upload --clobber. Nightly filenames include their
            # generation, so these replacements cannot delete the previous set.
            if path.name in existing:
                existing[path.name].delete_asset()
            remote.upload_asset(str(path))

    def update_tag(self, tag, sha):
        self.repo.get_git_ref(f"tags/{tag}").complete().edit(sha, force=True)
