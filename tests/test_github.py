import io
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from tools.github import GitHub


class GitHubTests(unittest.TestCase):
    def test_api_errors_propagate_with_diagnostics(self):
        for status in (401, 403, 404, 429, 500):
            error = subprocess.CalledProcessError(
                1, ["gh", "api"], stderr=f"gh: Request failed (HTTP {status})\n"
            )
            with (
                self.subTest(status=status),
                patch("tools.github.subprocess.run", side_effect=error),
                redirect_stderr(io.StringIO()) as diagnostics,
            ):
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    GitHub("example/repo").api("repos/example/repo/releases")
                self.assertIs(raised.exception, error)
                self.assertEqual(diagnostics.getvalue(), error.stderr)

    def test_cli_errors_propagate_with_diagnostics(self):
        error = subprocess.CalledProcessError(4, ["gh", "api"], stderr="Please set GH_TOKEN")
        with (
            patch("tools.github.subprocess.run", side_effect=error),
            redirect_stderr(io.StringIO()) as diagnostics,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                GitHub("example/repo").api("repos/example/repo/releases")
        self.assertEqual(diagnostics.getvalue(), "Please set GH_TOKEN\n")

    def test_publication_preserves_inherited_auth_and_json_input(self):
        with patch("tools.github.subprocess.run") as run:
            run.return_value.stdout = '{"id": 42}'
            result = GitHub("example/repo").api(
                "repos/example/repo/releases", "POST", {"tag_name": "v5.048"}
            )
        self.assertEqual(result, {"id": 42})
        self.assertNotIn("env", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["input"], '{"tag_name": "v5.048"}')
        self.assertEqual(
            run.call_args.args[0],
            ["gh", "api", "--method", "POST", "repos/example/repo/releases", "--input", "-"],
        )


if __name__ == "__main__":
    unittest.main()
