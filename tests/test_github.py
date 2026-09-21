import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tools.github import GitHub, GitHubNotFound


class GitHubTests(unittest.TestCase):
    def test_only_a_404_counts_as_a_missing_release(self):
        for status in (401, 403, 404, 429, 500):
            error = subprocess.CalledProcessError(
                1, ["gh", "api"], stderr=f"gh: Request failed (HTTP {status})\n"
            )
            with (
                self.subTest(status=status),
                patch("tools.github.subprocess.run", side_effect=error),
                redirect_stderr(io.StringIO()) as diagnostics,
            ):
                api = GitHub("example/repo")
                if status == 404:
                    self.assertIsNone(api.release("v5.048"))
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        api.release("v5.048")
                self.assertEqual(diagnostics.getvalue(), error.stderr)

    def test_cli_errors_do_not_look_like_missing_releases(self):
        error = subprocess.CalledProcessError(4, ["gh", "api"], stderr="Please set GH_TOKEN")
        with (
            patch("tools.github.subprocess.run", side_effect=error),
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                GitHub("example/repo").release("v5.048")
        self.assertEqual(diagnostics.getvalue(), "Please set GH_TOKEN\n")

    def test_token_is_scoped_to_gh_subprocesses(self):
        with patch.dict(os.environ, {"GH_TOKEN": "caller-token", "GH_HOST": "example.com"}):
            api = GitHub("example/repo", token="action-token")
            with patch("tools.github.subprocess.run") as run:
                run.return_value.stdout = '{"id": 1}'
                self.assertEqual(api.release("v5.048"), {"id": 1})
            env = run.call_args.kwargs["env"]
            self.assertEqual(env["GH_TOKEN"], "action-token")
            self.assertEqual(env["GH_HOST"], "github.com")
            self.assertNotIn("action-token", run.call_args.args[0])
            self.assertEqual(os.environ["GH_TOKEN"], "caller-token")
            self.assertEqual(os.environ["GH_HOST"], "example.com")

    def test_source_download_streams_binary_without_text_conversion(self):
        data = b"\x1f\x8b\xff\x00\r\n"
        endpoint = "repos/example/repo/tarball/commit"

        def run(command, **kwargs):
            self.assertEqual(
                command,
                [
                    "gh",
                    "api",
                    "--method",
                    "GET",
                    endpoint,
                ],
            )
            kwargs["stdout"].write(data)

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "archive.tar.gz"
            with patch("tools.github.subprocess.run", side_effect=run):
                GitHub("example/repo").download_source("example/repo", "commit", destination)
            self.assertEqual(destination.read_bytes(), data)

    def test_named_assets_use_release_download(self):
        assets = {
            "verilator-nightly-generation.tar.gz": b"\x1f\x8b\xff",
            "SHA256SUMS.txt": b"hash\n",
        }

        def run(command, **kwargs):
            self.assertEqual(
                command[:6], ["gh", "release", "download", "nightly", "--repo", "example/repo"]
            )
            directory = Path(command[command.index("--dir") + 1])
            names = [command[i + 1] for i, value in enumerate(command) if value == "--pattern"]
            self.assertEqual(set(names), set(assets))
            for name in names:
                (directory / name).write_bytes(assets[name])

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with patch("tools.github.subprocess.run", side_effect=run):
                GitHub("example/repo").download_assets("nightly", list(assets), directory)
            for name, data in assets.items():
                self.assertEqual((directory / name).read_bytes(), data)

    def test_deleted_asset_reports_not_found(self):
        error = subprocess.CalledProcessError(1, ["gh", "api"], stderr="gh: Not Found (HTTP 404)")
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch("tools.github.subprocess.run", side_effect=error),
                redirect_stderr(io.StringIO()) as diagnostics,
            ):
                with self.assertRaises(GitHubNotFound):
                    GitHub("example/repo").download_assets(
                        "nightly", ["archive.tar.gz"], Path(temp)
                    )
            self.assertEqual(diagnostics.getvalue(), error.stderr + "\n")

    def test_partial_pattern_match_reports_missing_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "checksums.txt").write_text("checksum\n")
            with patch("tools.github.subprocess.run"):
                with self.assertRaisesRegex(GitHubNotFound, "archive.tar.gz"):
                    GitHub("example/repo").download_assets(
                        "nightly", ["checksums.txt", "archive.tar.gz"], directory
                    )

    def test_failed_release_download_rechecks_asset_availability(self):
        for available in ([], [{"name": "archive.tar.gz", "size": 1}]):
            error = subprocess.CalledProcessError(
                1, ["gh", "release", "download"], stderr="Download failed\n"
            )
            api = GitHub("example/repo")
            with (
                self.subTest(available=available),
                tempfile.TemporaryDirectory() as temp,
                patch("tools.github.subprocess.run", side_effect=error),
                patch.object(api, "release", return_value={"id": 1}),
                patch.object(api, "assets", return_value=available),
                redirect_stderr(io.StringIO()) as diagnostics,
            ):
                expected = subprocess.CalledProcessError if available else GitHubNotFound
                with self.assertRaises(expected):
                    api.download_assets("nightly", ["archive.tar.gz"], Path(temp))
                self.assertEqual(diagnostics.getvalue(), error.stderr)

    def test_download_failure_emits_captured_diagnostic(self):
        error = subprocess.CalledProcessError(
            1, ["gh", "api"], stderr="gh: Unsupported Accept header (HTTP 415)\n"
        )
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("tools.github.subprocess.run", side_effect=error),
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                GitHub("example/repo").download_source(
                    "example/repo", "commit", Path(temp) / "source.tar.gz"
                )
        self.assertIs(raised.exception, error)
        self.assertEqual(diagnostics.getvalue(), error.stderr)

    def test_publication_preserves_inherited_auth_and_json_input(self):
        with patch("tools.github.subprocess.run") as run:
            run.return_value.stdout = '{"id": 42}'
            result = GitHub("example/repo").api(
                "repos/example/repo/releases", "POST", {"tag_name": "v5.048"}
            )
        self.assertEqual(result, {"id": 42})
        self.assertIsNone(run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["input"], '{"tag_name": "v5.048"}')
        self.assertEqual(
            run.call_args.args[0],
            ["gh", "api", "--method", "POST", "repos/example/repo/releases", "--input", "-"],
        )


if __name__ == "__main__":
    unittest.main()
