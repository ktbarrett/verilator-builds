import os
import subprocess
import tempfile
import unittest
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
            ):
                api = GitHub("example/repo")
                if status == 404:
                    self.assertIsNone(api.release("v5.048"))
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        api.release("v5.048")

    def test_cli_errors_do_not_look_like_missing_releases(self):
        error = subprocess.CalledProcessError(4, ["gh", "api"], stderr="Please set GH_TOKEN")
        with patch("tools.github.subprocess.run", side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                GitHub("example/repo").release("v5.048")

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

    def test_download_streams_binary_without_text_conversion(self):
        data = b"\x1f\x8b\xff\x00\r\n"
        endpoint = "repos/example/repo/releases/assets/123"

        def run(command, **kwargs):
            self.assertEqual(
                command,
                [
                    "gh",
                    "api",
                    "--method",
                    "GET",
                    endpoint,
                    "--header",
                    "Accept: application/octet-stream",
                ],
            )
            kwargs["stdout"].write(data)

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "archive.tar.gz"
            with patch("tools.github.subprocess.run", side_effect=run):
                GitHub("example/repo").download(endpoint, destination)
            self.assertEqual(destination.read_bytes(), data)

    def test_deleted_asset_reports_not_found(self):
        error = subprocess.CalledProcessError(1, ["gh", "api"], stderr="gh: Not Found (HTTP 404)")
        with tempfile.TemporaryDirectory() as temp:
            with patch("tools.github.subprocess.run", side_effect=error):
                with self.assertRaises(GitHubNotFound):
                    GitHub("example/repo").download(
                        "repos/example/repo/releases/assets/123", Path(temp) / "archive.tar.gz"
                    )

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
