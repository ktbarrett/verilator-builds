import copy
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.config import (
    CONFIG,
    PLATFORMS,
    archive_name,
    expected_assets,
    source_name,
    supported_platforms,
)
from tools.discover import MARKER, complete, discover, release_state
from tools.github import GitHub
from tools.package import create_archive, sha256
from tools.publish import assemble, cleanup_nightly, publish

SHA = "a" * 40
RECIPE = "b" * 40


def setUpModule():
    # Release mechanics use a small fictional support policy, independent of config.json.
    unittest.enterModuleContext(
        patch.dict(
            PLATFORMS,
            {
                "linux-x86_64": {"first_release": "v1.000", "minimum": "test"},
                "linux-aarch64": {"first_release": "v1.000", "minimum": "test"},
                "test-later": {"first_release": "v1.002", "minimum": "test"},
            },
            clear=True,
        )
    )
    unittest.enterModuleContext(
        patch.dict(CONFIG, {"first_release": "v1.000", "max_releases_per_run": 3})
    )


def release(label, sha=SHA, recipe=RECIPE, nightly=False, draft=False, source_version=None):
    state = {"label": label, "sha": sha, "recipe": recipe}
    if source_version is not None:
        state["source_version"] = source_version
    return {
        "id": 1,
        "tag_name": "nightly" if nightly else label,
        "body": f"<!-- {MARKER} {json.dumps(state)} -->",
        "draft": draft,
        "prerelease": nightly,
    }


def assets(label, source_version=None):
    return [
        {"id": i, "name": name, "size": 1}
        for i, name in enumerate(sorted(expected_assets(label, source_version)), 1)
    ]


class FakeGitHub:
    repository = "example/verilator-builds"

    def __init__(self):
        self.tags = [{"name": "v1.000", "commit": {"sha": SHA}}]
        self.release_list = []
        self.asset_list = []
        self.events = []
        self.fail_upload = False

    def pages(self, endpoint):
        return iter(self.tags)

    def releases(self):
        return copy.deepcopy(self.release_list)

    def assets(self, release):
        return copy.deepcopy(self.asset_list)

    def commit(self, repository, ref):
        return SHA

    def api(self, endpoint, method="GET", data=None):
        self.events.append((method, endpoint, data))
        if method == "DELETE":
            asset_id = int(endpoint.rsplit("/", 1)[1])
            self.asset_list = [a for a in self.asset_list if a["id"] != asset_id]
        elif method == "POST":
            self.release_list = [{"id": 1, "body": "", **data}]
            return self.release_list[0]
        elif "/releases/" in endpoint and method == "PATCH":
            self.release_list[0].update(data)

    def upload(self, tag, paths):
        self.events.append(("UPLOAD", tag, None))
        for path in paths:
            self.asset_list = [a for a in self.asset_list if a["name"] != path.name]
            self.asset_list.append(
                {
                    "id": max([a["id"] for a in self.asset_list], default=0) + 1,
                    "name": path.name,
                    "size": path.stat().st_size,
                    "digest": "sha256:" + sha256(path),
                }
            )
            if self.fail_upload:
                raise RuntimeError("simulated partial upload")


class DiscoveryTests(unittest.TestCase):
    def test_bootstrap_and_development_build(self):
        result = discover(FakeGitHub(), RECIPE, "auto", "", "1")
        self.assertEqual([b["mode"] for b in result], ["stable", "nightly"])
        self.assertEqual(result[0]["label"], "v1.000")
        self.assertEqual(result[0]["sha"], SHA)

    def test_complete_is_idempotent_but_missing_asset_is_retried(self):
        api = FakeGitHub()
        api.release_list = [release("v1.000"), release("nightly-old", nightly=True)]
        api.asset_list = assets("v1.000") + assets("nightly-old")
        self.assertEqual(discover(api, RECIPE, "auto", "", "1"), [])
        api.asset_list = [a for a in api.asset_list if a["name"] != source_name("v1.000")]
        result = discover(api, RECIPE, "auto", "", "2")
        self.assertEqual([b["label"] for b in result], ["v1.000"])

    def test_recipe_changes_rebuild_only_nightly(self):
        api = FakeGitHub()
        api.release_list = [release("v1.000"), release("nightly-old", nightly=True)]
        api.asset_list = assets("v1.000") + assets("nightly-old")
        result = discover(api, "c" * 40, "auto", "", "1")
        self.assertEqual([b["mode"] for b in result], ["nightly"])

    def test_source_version_markers_and_legacy_markers(self):
        label = "nightly-old-source"
        old_source = release(label, nightly=True, source_version="v1.000")
        self.assertTrue(complete(old_source, assets(label, "v1.000"), SHA))
        self.assertTrue(complete(release(label, nightly=True), assets(label, "v1.000"), SHA))
        self.assertTrue(complete(release(label, nightly=True), assets(label), SHA))
        for bad_version in ([], 5048, "bad", "v1.002"):
            self.assertIsNone(release_state(release("v1.000", source_version=bad_version)))

    def test_tag_filter_sort_and_backlog_limit(self):
        api = FakeGitHub()
        api.tags = [
            {"name": tag, "commit": {"sha": SHA}}
            for tag in ["v1.006", "v1.002", "v0.998", "v1.000", "v1.004", "v1.008-rc1"]
        ]
        result = discover(api, RECIPE, "auto", "", "1")
        self.assertEqual([b["label"] for b in result[:-1]], ["v1.000", "v1.002", "v1.004"])
        self.assertEqual(len(result), CONFIG["max_releases_per_run"] + 1)

    def test_manual_commit_and_historical_tag(self):
        result = discover(FakeGitHub(), RECIPE, "build", SHA, "1")
        self.assertEqual(result[0]["sha"], SHA)
        self.assertEqual(result[0]["mode"], "build")
        self.assertEqual(
            discover(FakeGitHub(), RECIPE, "stable", "v0.998", "1")[0]["label"], "v0.998"
        )
        with self.assertRaises(ValueError):
            discover(FakeGitHub(), RECIPE, "stable", "master", "1")

    def test_drafts_and_malformed_markers_are_incomplete(self):
        self.assertFalse(complete(release("v1.000", draft=True), assets("v1.000"), SHA))
        for state in ("not json", "null", "[]", '{"sha": "a"}'):
            self.assertIsNone(release_state({"body": f"<!-- {MARKER} {state} -->"}))
        incomplete = assets("v1.000")
        incomplete[0]["size"] = 0
        self.assertFalse(complete(release("v1.000"), incomplete, SHA))

    def test_api_pagination(self):
        api = GitHub("example/repo")
        with patch.object(api, "api", side_effect=[list(range(100)), [100]]) as call:
            self.assertEqual(list(api.pages("repos/example/repo/tags")), list(range(101)))
            self.assertEqual(call.call_args.args[0], "repos/example/repo/tags?per_page=100&page=2")


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def packages(self, label, source_version=None):
        source_version = source_version or (label if label.startswith("v") else "v1.005")
        source = self.directory / source_name(label)
        with tarfile.open(source, "w:gz", pax_headers={"comment": SHA}) as archive:
            entry = tarfile.TarInfo("source/configure.ac")
            content = f"AC_INIT([Verilator],[{source_version[1:]} devel], [https://verilator.org])\n".encode()
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
        for platform in supported_platforms(label):
            install = self.directory / "install"
            install.mkdir()
            (install / "example.txt").write_text("fixture")
            create_archive(
                install,
                self.directory,
                platform,
                label,
                SHA,
                RECIPE,
                {
                    "abi_audited": True,
                    "source_sha256": sha256(source),
                    "source_version": source_version,
                },
            )

    def test_assemble_full_set_and_reject_corruption(self):
        self.packages("v1.000")
        paths = assemble(self.directory, "v1.000", SHA, RECIPE)
        self.assertEqual({p.name for p in paths}, expected_assets("v1.000"))
        archive = next(p for p in paths if "linux-x86_64" in p.name)
        archive.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            assemble(self.directory, "v1.000", SHA, RECIPE)

    def test_reject_missing_platform_or_wrong_provenance(self):
        self.packages("v1.000")
        with self.assertRaisesRegex(ValueError, "Mismatched sha"):
            assemble(self.directory, "v1.000", "c" * 40, RECIPE)
        (self.directory / "linux-aarch64.manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            assemble(self.directory, "v1.000", SHA, RECIPE)

    def test_reject_source_corruption(self):
        self.packages("v1.000")
        (self.directory / source_name("v1.000")).write_bytes(b"wrong source")
        with self.assertRaisesRegex(ValueError, "Invalid upstream source archive"):
            assemble(self.directory, "v1.000", SHA, RECIPE)

    def test_source_version_must_match_release_tag_and_package(self):
        self.packages("v1.000", source_version="v1.002")
        with self.assertRaisesRegex(ValueError, "label and source version differ"):
            assemble(self.directory, "v1.000", SHA, RECIPE)
        self.packages("v1.000")
        path = self.directory / "linux-x86_64.manifest.json"
        manifest = json.loads(path.read_text())
        manifest["source_version"] = "v1.002"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Mismatched source_version"):
            assemble(self.directory, "v1.000", SHA, RECIPE)

    def test_nightly_includes_every_platform_regardless_of_source_version(self):
        label = "nightly-old-commit"
        api = FakeGitHub()
        api.release_list = [release("nightly-old", nightly=True)]
        api.asset_list = assets("nightly-old")
        self.packages(label, source_version="v1.000")
        publish(api, self.directory, "nightly", label, SHA, RECIPE)
        self.assertEqual({a["name"] for a in api.asset_list}, expected_assets(label, "v1.000"))
        self.assertTrue(complete(api.release_list[0], api.asset_list, SHA, RECIPE))
        self.assertIn(archive_name(label, "test-later"), {a["name"] for a in api.asset_list})
        api.asset_list.append({"id": 100, "name": "obsolete-upload.tar.gz", "size": 1})
        cleanup_nightly(api)
        self.assertEqual({a["name"] for a in api.asset_list}, expected_assets(label, "v1.000"))

    def test_reject_unaudited_package(self):
        self.packages("v1.000")
        path = self.directory / "linux-x86_64.manifest.json"
        manifest = json.loads(path.read_text())
        manifest["abi_audited"] = False
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "ABI audit"):
            assemble(self.directory, "v1.000", SHA, RECIPE)

    def test_cleanup_incomplete_first_nightly(self):
        api = FakeGitHub()
        api.release_list = [{"id": 1, "tag_name": "nightly", "draft": True, "body": ""}]
        api.asset_list = assets("nightly-interrupted")[:2]
        cleanup_nightly(api)
        self.assertEqual(api.asset_list, [])

    def test_successful_nightly_publishes_before_deleting(self):
        api = FakeGitHub()
        api.release_list = [release("nightly-old", nightly=True)]
        api.asset_list = assets("nightly-old")
        self.packages("nightly-new")
        publish(api, self.directory, "nightly", "nightly-new", SHA, RECIPE)
        self.assertEqual({a["name"] for a in api.asset_list}, expected_assets("nightly-new"))
        patch_index = next(
            i for i, e in enumerate(api.events) if e[0] == "PATCH" and e[2].get("body")
        )
        delete_index = next(i for i, e in enumerate(api.events) if e[0] == "DELETE")
        self.assertLess(patch_index, delete_index)

    def test_partial_upload_preserves_old_set_and_cleanup_recovers(self):
        api = FakeGitHub()
        api.release_list = [release("nightly-old", nightly=True)]
        api.asset_list = assets("nightly-old")
        old_body = api.release_list[0]["body"]
        self.packages("nightly-new")
        api.fail_upload = True
        with self.assertRaisesRegex(RuntimeError, "partial upload"):
            publish(api, self.directory, "nightly", "nightly-new", SHA, RECIPE)
        self.assertEqual(api.release_list[0]["body"], old_body)
        self.assertFalse(any(e[0] == "DELETE" for e in api.events))
        self.assertTrue(expected_assets("nightly-old") <= {a["name"] for a in api.asset_list})
        cleanup_nightly(api)
        self.assertEqual({a["name"] for a in api.asset_list}, expected_assets("nightly-old"))

    def test_stable_draft_is_published_only_after_upload(self):
        api = FakeGitHub()
        self.packages("v1.000")
        publish(api, self.directory, "stable", "v1.000", SHA, RECIPE)
        self.assertTrue(api.events[0][2]["draft"])
        self.assertFalse(api.release_list[0]["draft"])
        self.assertFalse(api.release_list[0]["prerelease"])
        self.assertTrue(complete(api.release_list[0], api.asset_list, SHA))
        api.events.clear()
        publish(api, self.directory, "stable", "v1.000", SHA, RECIPE)
        self.assertEqual(api.events, [])

    def test_moved_upstream_tag_is_rejected(self):
        api = FakeGitHub()
        api.release_list = [release("v1.000", sha="c" * 40)]
        self.packages("v1.000")
        with self.assertRaisesRegex(ValueError, "tag changed"):
            publish(api, self.directory, "stable", "v1.000", SHA, RECIPE)
        self.assertEqual(api.events, [])


if __name__ == "__main__":
    unittest.main()
