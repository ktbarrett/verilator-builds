import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.package import normalize_makefile, relocate_launchers


@unittest.skipUnless(shutil.which("perl"), "Requires Perl")
class LauncherTests(unittest.TestCase):
    def test_launchers_find_data_after_package_moves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install = root / "staging"
            (install / "bin").mkdir(parents=True)
            data = install / "share/verilator"
            (data / "include").mkdir(parents=True)
            (data / "bin").mkdir()
            (data / "include/verilated_std.sv").write_text("fixture")
            launcher = install / "bin/verilator"
            launcher.write_text(
                "#!/usr/bin/env perl\nuse FindBin qw($RealBin);\nuse Cwd qw(realpath);\n"
                'my $verilator_pkgdatadir_relpath = "..";\n'
                'my $root = realpath("$RealBin/$verilator_pkgdatadir_relpath");\n'
                'die "Missing data" unless -f "$root/include/verilated_std.sv";\n'
                'print "$root\\n";\n'
            )
            redirect = data / "bin/verilator"
            redirect.write_text(
                "#!/usr/bin/env perl\nuse FindBin qw($RealBin);\n"
                'my $relpath = "incorrect";\n'
                'exec $^X, "$RealBin/$relpath/verilator";\n'
            )
            private = data / "bin/private"
            private.write_text('#!/usr/bin/env perl\nprint "private";\n')
            original = private.read_bytes()
            native = data / "bin/verilator_bin"
            native.write_bytes(b"\x7fELF\xff\x00")
            relocate_launchers(install)
            self.assertEqual(private.read_bytes(), original)
            self.assertEqual(native.read_bytes(), b"\x7fELF\xff\x00")
            moved = root / "relocated"
            install.rename(moved)
            for path in ("bin/verilator", "share/verilator/bin/verilator"):
                result = subprocess.check_output(["perl", str(moved / path)], cwd=root, text=True)
                self.assertEqual(Path(result.strip()), moved / "share/verilator")

    def test_unknown_launcher_layout_fails_before_packaging(self):
        with tempfile.TemporaryDirectory() as temp:
            install = Path(temp)
            (install / "bin").mkdir()
            (install / "bin/verilator").write_text("changed upstream launcher")
            with self.assertRaisesRegex(ValueError, "data-directory assignment"):
                relocate_launchers(install)


UPSTREAM_MAKEFILE = """\
AR = /usr/bin/ar
CXX = /opt/gcc/bin/g++
LINK = /opt/gcc/bin/g++
OBJCACHE ?= ccache
PERL = /usr/bin/perl
PYTHON3 = /usr/bin/python3
CFG_CXXFLAGS_STD = -std=gnu++17
CFG_CXXFLAGS_COROUTINES = -fcoroutines
CFG_LDFLAGS_VERILATED = -fuse-ld=mold
"""


class MakefileTests(unittest.TestCase):
    def normalized(self, root: Path, platform: str) -> Path:
        makefile = root / "verilated.mk"
        makefile.write_text(UPSTREAM_MAKEFILE)
        normalize_makefile(makefile, platform)
        return makefile

    def test_operators_are_kept_and_values_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            makefile = self.normalized(Path(temp), "linux-x86_64")
            self.assertEqual(
                makefile.read_text().splitlines(),
                [
                    "AR = ar",
                    "CXX = g++",
                    "LINK = g++",
                    "OBJCACHE ?= ",
                    "PERL = perl",
                    "PYTHON3 = python3",
                    "CFG_CXXFLAGS_STD = -std=c++14",
                    "CFG_CXXFLAGS_COROUTINES = -std=c++20",
                    "CFG_LDFLAGS_VERILATED = ",
                ],
            )

    def test_macos_uses_clang(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contents = self.normalized(Path(temp), "macos-arm64").read_text()
            self.assertIn("CXX = clang++\n", contents)
            self.assertIn("LINK = clang++\n", contents)

    @unittest.skipUnless(shutil.which("make"), "Requires make")
    def test_objcache_from_environment_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            makefile = self.normalized(root, "linux-x86_64")
            printer = root / "print.mk"
            printer.write_text("print:\n\t@echo '$(OBJCACHE)'\n")
            command = ["make", "-s", "-f", str(makefile), "-f", str(printer), "print"]
            env = {k: v for k, v in os.environ.items() if k not in ("OBJCACHE", "MAKEFLAGS")}
            unset = subprocess.check_output(command, cwd=root, env=env, text=True)
            self.assertEqual(unset.strip(), "")
            env["OBJCACHE"] = "ccache"
            result = subprocess.check_output(command, cwd=root, env=env, text=True)
            self.assertEqual(result.strip(), "ccache")


if __name__ == "__main__":
    unittest.main()
