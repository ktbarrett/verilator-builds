import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.config import archive_name
from tools.github import GitHub, GitHubNotFound
from tools.install import (
    build_source,
    extract_archive,
    install,
    main,
    published_package,
    requested_version,
    runner_platform,
)
from tools.package import create_archive, sha256
from tools.releases import MARKER

SHA = "a" * 40
RECIPE = "b" * 40
REPOSITORY = "example/verilator-builds"


class InputTests(unittest.TestCase):
    def test_normalize_versions_and_reject_refs_or_shell_text(self):
        for value in ("5.048", "v5.048", " v5.048 "):
            self.assertEqual(requested_version(value), "v5.048")
        self.assertEqual(requested_version("nightly"), "nightly")
        for value in ("", "latest", "master", "v5", "5.48", "../../etc", "$(id)", "5.048\nx"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                requested_version(value)

    def test_runner_architectures_and_unsupported_systems(self):
        for system, machine, expected in (
            ("Linux", "x86_64", "linux-x86_64"),
            ("Linux", "aarch64", "linux-aarch64"),
            ("Darwin", "x86_64", "macos-x86_64"),
            ("Darwin", "arm64", "macos-arm64"),
        ):
            with (
                patch("tools.install.host_platform.system", return_value=system),
                patch("tools.install.host_platform.machine", return_value=machine),
            ):
                self.assertEqual(runner_platform(), expected)
        with patch("tools.install.host_platform.system", return_value="Windows"):
            with self.assertRaisesRegex(ValueError, "Supported runners"):
                runner_platform()


@unittest.skipUnless(shutil.which("sh"), "Requires a POSIX shell")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.destination = self.root / "destination"
        self.destination.mkdir()
        self.api = self.enterContext(patch("tools.github.GitHub.api", side_effect=self.read_api))
        self.enterContext(patch("tools.github.GitHub.download", side_effect=self.download))
        self.enterContext(patch("tools.install.runner_platform", return_value="linux-x86_64"))
        self.enterContext(patch("tools.install.dependencies", return_value=os.environ.copy()))
        self.build = self.enterContext(
            patch("tools.install.build_source", side_effect=self.build_source)
        )
        self.release = None
        self.asset_list = []
        self.downloads = []
        self.downloaded_names = []

    def launcher(self, prefix, version="v5.048", sha=SHA):
        (prefix / "bin").mkdir(parents=True)
        (prefix / "share/verilator").mkdir(parents=True)
        launcher = prefix / "bin/verilator"
        launcher.write_text(f"#!/bin/sh\necho 'Verilator {version} ({sha})'\n")
        launcher.chmod(0o755)

    def package(self, label="v5.048", nightly=False):
        staging = self.root / "staging"
        self.launcher(staging)
        create_archive(
            staging,
            self.assets,
            "linux-x86_64",
            label,
            SHA,
            RECIPE,
            {"abi_audited": True, "source_version": "v5.048"},
        )
        archive = self.assets / archive_name(label, "linux-x86_64")
        checksums = self.assets / f"SHA256SUMS-{label}.txt"
        checksums.write_text(f"{sha256(archive)}  {archive.name}\n")
        state = {"label": label, "sha": SHA, "recipe": RECIPE}
        self.release = {
            "id": 42,
            "draft": False,
            "tag_name": "nightly" if nightly else label,
            "body": f"<!-- {MARKER} {json.dumps(state)} -->",
        }
        self.asset_list = [
            {"id": i, "name": p.name, "size": p.stat().st_size}
            for i, p in enumerate((archive, checksums), 1)
        ]

    def read_api(self, endpoint):
        if "/releases/tags/" in endpoint:
            return self.release
        self.assertIn("/releases/42/assets?per_page=100&page=", endpoint)
        page = int(endpoint.rsplit("=", 1)[1])
        return self.asset_list[(page - 1) * 100 : page * 100]

    def download(self, endpoint, destination):
        self.downloads.append(endpoint)
        self.downloaded_names.append(destination.name)
        asset_id = int(endpoint.rsplit("/", 1)[1])
        asset = next(a for a in self.asset_list if a.get("id") == asset_id)
        shutil.copyfile(self.assets / asset["name"], destination)

    def build_source(self, api, requested, work, prefix, platform, jobs, install_dependencies):
        actual = "v5.049" if requested == "nightly" else requested
        self.launcher(prefix, actual)
        return actual, SHA

    def install(self, requested="v5.048", **kwargs):
        return install(requested, REPOSITORY, "token", self.destination, **kwargs)

    def test_install_stable_archive_without_compilation(self):
        self.package()
        result = self.install("5.048")
        self.assertEqual(result["built-from-source"], "false")
        self.assertEqual(result["source-sha"], SHA)
        self.assertTrue((Path(result["path"]) / "bin/verilator").is_file())
        self.assertEqual(list(self.destination.iterdir()), [Path(result["path"])])
        self.build.assert_not_called()
        self.assertTrue(
            all(f"repos/{REPOSITORY}/releases/assets/" in endpoint for endpoint in self.downloads)
        )

    def test_nightly_uses_published_generation_and_paginates_assets(self):
        self.package("nightly-successful", nightly=True)
        self.asset_list[:0] = [{"name": f"pending-upload-{i}", "size": 1} for i in range(100)]
        self.asset_list.append({"name": archive_name("nightly-newer", "linux-x86_64"), "size": 1})
        result = self.install("nightly")
        self.assertEqual(result["version"], "v5.048")
        self.assertEqual(result["built-from-source"], "false")
        self.assertTrue(all("nightly-successful" in name for name in self.downloaded_names))
        self.build.assert_not_called()

    def test_missing_release_builds_requested_tag_below_publication_floor(self):
        result = self.install("v4.228")
        self.assertEqual(result["built-from-source"], "true")
        self.assertEqual(self.build.call_args.args[1], "v4.228")

    def test_missing_nightly_builds_nightly(self):
        result = self.install("nightly")
        self.assertEqual(result["version"], "v5.049")
        self.assertEqual(self.build.call_args.args[1], "nightly")

    def test_older_source_build_need_not_embed_the_commit_in_its_version(self):
        with patch("tools.install.subprocess.check_output", return_value="Verilator 4.228\n"):
            self.assertEqual(self.install("v4.228")["source-sha"], SHA)

    def test_wrong_installed_version_fails_and_removes_installation(self):
        with patch("tools.install.subprocess.check_output", return_value="Verilator 5.050\n"):
            with self.assertRaisesRegex(ValueError, "expected version"):
                self.install("v5.048")
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_missing_platform_builds_from_source(self):
        self.package()
        self.asset_list = [a for a in self.asset_list if a["name"].startswith("SHA256SUMS")]
        self.assertEqual(self.install()["built-from-source"], "true")

    def test_force_source_bypasses_release_lookup(self):
        self.package()
        self.assertEqual(self.install(force_source=True)["built-from-source"], "true")
        self.api.assert_not_called()

    def test_deleted_nightly_assets_fall_back_to_source(self):
        self.package("nightly-old", nightly=True)
        with patch("tools.github.GitHub.download", side_effect=GitHubNotFound("HTTP 404")):
            self.assertEqual(self.install("nightly")["built-from-source"], "true")

    def test_corrupt_archive_is_rejected_without_source_fallback(self):
        self.package()
        (self.assets / archive_name("v5.048", "linux-x86_64")).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.install()
        self.build.assert_not_called()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_invalid_metadata_is_rejected(self):
        self.package()
        self.release["body"] = "missing state"
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.install()
        self.build.assert_not_called()

    def test_wrong_platform_manifest_is_rejected_even_with_valid_checksum(self):
        self.package()
        archive = self.assets / archive_name("v5.048", "linux-x86_64")
        extracted = extract_archive(archive, self.root / "rewrite")
        metadata = extracted / "manifest.json"
        manifest = json.loads(metadata.read_text())
        manifest["platform"] = "linux-aarch64"
        metadata.write_text(json.dumps(manifest))
        with tarfile.open(archive, "w:gz") as package:
            package.add(extracted, arcname=extracted.name)
        (self.assets / "SHA256SUMS-v5.048.txt").write_text(f"{sha256(archive)}  {archive.name}\n")
        with self.assertRaisesRegex(ValueError, "platform"):
            self.install()
        self.build.assert_not_called()

    def test_draft_is_not_installable(self):
        self.package()
        self.release["draft"] = True
        self.assertIsNone(published_package(GitHub(REPOSITORY), "v5.048", "linux-x86_64"))

    def test_action_exports_path_version_commit_and_root(self):
        self.package()
        env = {
            "RUNNER_TEMP": str(self.destination),
            "INSTALL_VERSION": "5.048",
            "INSTALL_REPOSITORY": REPOSITORY,
            "INSTALL_TOKEN": "token",
            "GITHUB_PATH": str(self.root / "path"),
            "GITHUB_ENV": str(self.root / "env"),
            "GITHUB_OUTPUT": str(self.root / "output"),
            "INSTALL_JOBS": "2",
            "INSTALL_DEPENDENCIES": "false",
            "INSTALL_FORCE_SOURCE": "false",
        }
        with patch.dict(os.environ, env):
            main()
        output = dict(
            line.split("=", 1) for line in (self.root / "output").read_text().splitlines()
        )
        self.assertEqual(output["version"], "v5.048")
        self.assertEqual(output["source-sha"], SHA)
        self.assertEqual((self.root / "path").read_text(), output["path"] + "/bin\n")
        self.assertEqual(
            (self.root / "env").read_text(), f"VERILATOR_ROOT={output['path']}/share/verilator\n"
        )


class SourceTests(unittest.TestCase):
    def test_source_fetch_pins_tag_or_nightly_branch_to_a_commit(self):
        for requested, ref in (("v5.046", "refs%2Ftags%2Fv5.046"), ("nightly", "master")):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)

                def download_source(arguments, **kwargs):
                    self.assertIn(f"repos/verilator/verilator/tarball/{SHA}", arguments)
                    # GitHub's archive endpoint rejects the release-asset media type.
                    self.assertIn("Accept: application/json", arguments)
                    with tarfile.open(fileobj=kwargs["stdout"], mode="w:gz") as archive:
                        data = b"AC_INIT([Verilator],[5.046], [])\n"
                        entry = tarfile.TarInfo("verilator-source/configure.ac")
                        entry.size = len(data)
                        archive.addfile(entry, io.BytesIO(data))

                with (
                    patch("tools.github.GitHub.api", return_value={"sha": SHA}) as api,
                    patch("tools.github.GitHub.run", side_effect=download_source),
                    patch("tools.install.dependencies", return_value={}),
                    patch("tools.install.build_native", return_value="v5.046") as build,
                ):
                    result = build_source(
                        GitHub(REPOSITORY, token="token"),
                        requested,
                        root,
                        root / "install",
                        "linux-x86_64",
                        2,
                        False,
                    )
                    self.assertEqual(result, ("v5.046", SHA))
                    api.assert_called_once_with(f"repos/verilator/verilator/commits/{ref}")
                    self.assertEqual(build.call_args.args[2:5], (requested, SHA, 2))
                    self.assertFalse(build.call_args.args[0].exists())

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "evil.tar.gz"
            with tarfile.open(archive, "w:gz") as package:
                entry = tarfile.TarInfo("../escaped")
                entry.size = 1
                package.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaises(tarfile.OutsideDestinationError):
                extract_archive(archive, root / "destination")
            self.assertFalse((root / "escaped").exists())


if __name__ == "__main__":
    unittest.main()
