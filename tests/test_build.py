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
