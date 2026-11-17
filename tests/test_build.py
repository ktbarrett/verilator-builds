import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build import linux_library_override
from tools.config import CONFIG, macos_flex, version_matches


class MacosFlexTests(unittest.TestCase):
    def test_configured_pins(self) -> None:
        self.assertEqual(macos_flex("v5.052"), "flex@2.6.4")
        self.assertEqual(macos_flex("v5.054"), "flex@2.6.4")
        self.assertEqual(macos_flex("v5.056"), "flex")
        self.assertEqual(macos_flex("master"), "flex")
        self.assertEqual(macos_flex("nightly-0123456789ab-0123456789ab-1-1"), "flex")

    def test_first_matching_pin_wins(self) -> None:
        config = {
            "nightly": "flex",
            "releases": [
                {"versions": "<v5.056", "formula": "flex@2.6.4"},
                {"versions": ">=v5.056, <v5.060", "formula": "flex@2.6.9"},
                {"formula": "flex@2.6.10"},
            ],
        }
        with patch.dict(CONFIG, {"macos_flex": config}):
            self.assertEqual(macos_flex("v5.054"), "flex@2.6.4")
            self.assertEqual(macos_flex("v5.058"), "flex@2.6.9")
            self.assertEqual(macos_flex("v5.060"), "flex@2.6.10")
            self.assertEqual(macos_flex("master"), "flex")

    def test_nightly_switch(self) -> None:
        config = {"nightly": "flex@2.6.9", "releases": [{"formula": "flex@2.6.4"}]}
        with patch.dict(CONFIG, {"macos_flex": config}):
            self.assertEqual(macos_flex("master"), "flex@2.6.9")
            self.assertEqual(macos_flex("nightly-0123456789ab-0123456789ab-1-1"), "flex@2.6.9")
            self.assertEqual(macos_flex("v5.060"), "flex@2.6.4")

    def test_version_operators(self) -> None:
        cases = {
            "<v5.054": [True, False, False],
            "<=v5.054": [True, True, False],
            "==v5.054": [False, True, False],
            "!=v5.054": [True, False, True],
            ">=v5.054": [False, True, True],
            ">v5.054": [False, False, True],
            ">v5.050,<=v5.054,!=v5.052": [False, True, False],
        }
        for spec, expected in cases.items():
            actual = [version_matches(label, spec) for label in ("v5.052", "v5.054", "v5.056")]
            self.assertEqual(actual, expected, spec)
        self.assertFalse(version_matches("master", "!=v5.054"))

    def test_invalid_versions_fail(self) -> None:
        for spec in ("5.056", "=<v5.056", "<v5.56", "<v5.056,", "~=v5.056"):
            with self.assertRaisesRegex(ValueError, "Invalid version clause"):
                version_matches("v5.052", spec)

    def test_configured_pins_are_valid(self) -> None:
        # The macOS action runs 'brew extract --version=<version> flex' for each
        # versioned formula. Check every clause here, since development builds skip them.
        config = CONFIG["macos_flex"]
        formula = r"^flex(@\d+(\.\d+)*)?$"
        self.assertRegex(config["nightly"], formula)
        for pin in config["releases"]:
            self.assertRegex(pin["formula"], formula)
            if "versions" in pin:
                version_matches("v5.052", pin["versions"])


class LinuxLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        (self.source / "src").mkdir()
        self.makefile = self.source / "src/Makefile_obj"

    def test_missing_static_library_fails_early(self):
        self.makefile.write_text("CFG_LIBS = -lpthread -latomic\n")
        with patch("tools.build.subprocess.check_output", return_value="libatomic.a\n"):
            with self.assertRaisesRegex(ValueError, "install the static libatomic package"):
                linux_library_override(self.source, "g++", os.environ)

    def test_no_atomic_dependency_needs_no_override(self):
        self.makefile.write_text("CFG_LIBS = -lpthread\n")
        self.assertIsNone(linux_library_override(self.source, "g++", os.environ))

    @unittest.skipUnless(
        sys.platform == "linux" and shutil.which("g++") and shutil.which("readelf"),
        "Requires Linux with GCC and readelf",
    )
    def test_atomic_operations_link_without_shared_libatomic(self):
        # Exercise the generic out-of-line atomic helper on both Linux architectures.
        cpp = self.source / "atomic.cpp"
        cpp.write_text(
            "struct alignas(16) Value { unsigned long long parts[4]; };\n"
            "int main() {\n"
            "  Value value{}, expected{}, desired{{1, 2, 3, 4}};\n"
            "  if (!__atomic_compare_exchange(&value, &expected, &desired, false,\n"
            "                                 __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) return 1;\n"
            "  return value.parts[0] != 1;\n"
            "}\n"
        )
        self.makefile.write_text("CFG_LIBS = -lpthread -latomic\n")
        override = linux_library_override(self.source, "g++", os.environ)
        static_flags = shlex.split(override.split("=", 1)[1])
        for name, libraries in (("shared", ["-latomic"]), ("static", static_flags)):
            binary = self.source / name
            subprocess.run(
                [
                    "g++",
                    "-std=c++14",
                    "-static-libgcc",
                    "-static-libstdc++",
                    str(cpp),
                    "-o",
                    str(binary),
                    *libraries,
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True)
            dynamic = subprocess.check_output(["readelf", "-d", str(binary)], text=True)
            self.assertEqual("libatomic.so.1" in dynamic, name == "shared")
        # Only Verilator's make invocation is overridden, not installed model makefiles.
        self.assertEqual(self.makefile.read_text(), "CFG_LIBS = -lpthread -latomic\n")


if __name__ == "__main__":
    unittest.main()
