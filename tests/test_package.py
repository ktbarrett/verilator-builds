import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.package import relocate_launchers


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
            native = data / "bin/verilator_bin.exe"
            native.write_bytes(b"MZ\xff\x00")
            relocate_launchers(install)
            self.assertEqual(private.read_bytes(), original)
            self.assertEqual(native.read_bytes(), b"MZ\xff\x00")
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


if __name__ == "__main__":
    unittest.main()
