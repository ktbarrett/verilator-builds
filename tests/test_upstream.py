import unittest

from tools.upstream import source_version


class SourceVersionTests(unittest.TestCase):
    def test_parse_upstream_version_without_using_comment_examples(self):
        for value, expected in (
            ("5.048 2026-04-26", "v5.048"),
            ("5.049 devel", "v5.049"),
            ("5.050", "v5.050"),
        ):
            text = f"#AC_INIT([Verilator],[0.000 devel])\nAC_INIT([Verilator],[{value}],\n [url])"
            self.assertEqual(source_version(text), expected)
        with self.assertRaisesRegex(ValueError, "Cannot read Verilator version"):
            source_version("#AC_INIT([Verilator],[5.050 devel])")


if __name__ == "__main__":
    unittest.main()
