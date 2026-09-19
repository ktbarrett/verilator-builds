import unittest

from tools.validate import check_linux, check_macos, check_windows


class CompatibilityTests(unittest.TestCase):
    def test_linux_rejects_new_glibc_and_runtime_dependencies(self):
        header = "Machine: Advanced Micro Devices X86-64"
        dynamic = "(NEEDED) Shared library: [libc.so.6]"
        check_linux(dynamic, "x@GLIBC_2.17", header, "x86_64")
        for symbols in (
            "x@GLIBC_2.18",
            "x@GLIBC_2.27",
            "x@GLIBCXX_3.4.20",
            "x@GCC_3.0",
            "0000 DF *UND* 0 (GLIBC_2.25) getrandom",
            "0000 DF *UND* 0 (GLIBCXX_3.4.20) foo",
        ):
            with self.assertRaises(ValueError):
                check_linux(dynamic, symbols, header, "x86_64")
        for library in ("libstdc++.so.6", "libgcc_s.so.1", "libatomic.so.1", "libjemalloc.so.2"):
            with self.assertRaises(ValueError):
                check_linux(f"(NEEDED) [{library}]", "", header, "x86_64")

    def test_linux_architecture_and_rpath(self):
        with self.assertRaisesRegex(ValueError, "architecture"):
            check_linux("", "", "Machine: AArch64", "x86_64")
        with self.assertRaisesRegex(ValueError, "search path"):
            check_linux("(RUNPATH) [/opt/build]", "", "Machine: AArch64", "aarch64")

    def test_macos_deployment_and_homebrew_linkage(self):
        libs = "binary:\n\t/usr/lib/libc++.1.dylib (compatibility version 1.0.0)"
        check_macos(libs, "cmd LC_BUILD_VERSION\n minos 10.15", "x86_64", "10.15", "x86_64")
        check_macos(
            libs,
            "cmd LC_VERSION_MIN_MACOSX\n cmdsize 16\n version 10.15",
            "x86_64",
            "10.15",
            "x86_64",
        )
        with self.assertRaisesRegex(ValueError, "deployment target"):
            check_macos(libs, "minos 13.0", "arm64", "11.0", "arm64")
        with self.assertRaisesRegex(ValueError, "Non-system"):
            check_macos(
                "binary:\n\t/opt/homebrew/lib/libfoo.dylib (x)",
                "minos 11.0",
                "arm64",
                "11.0",
                "arm64",
            )

    def test_windows_accepts_ucrt_and_rejects_external_runtimes(self):
        check_windows("pei-x86-64\n DLL Name: KERNEL32.dll\n DLL Name: ucrtbase.dll")
        for dll in (
            "VCRUNTIME140.dll",
            "MSVCP140.dll",
            "libstdc++-6.dll",
            "libgcc_s_seh-1.dll",
            "libwinpthread-1.dll",
            "msys-2.0.dll",
        ):
            with self.assertRaisesRegex(ValueError, "Non-system"):
                check_windows(f"pei-x86-64\n DLL Name: {dll}")


if __name__ == "__main__":
    unittest.main()
