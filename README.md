# Verilator Binaries

Portable distributions of [Verilator](https://github.com/verilator/verilator).
Version tags match upstream version tags.
There is also a special `nightly` release which builds every night.

## Platform Support

| Archive platform | Compatibility target | First Verilator release |
| --- | --- | --- |
| `linux-x86_64` | glibc 2.17+, baseline x86-64 | v5.048 |
| `linux-aarch64` | glibc 2.17+, ARMv8-A | v5.048 |
| `macos-x86_64` | macOS 10.15+ | v5.054 |
| `macos-arm64` | macOS 11+ | v5.050 |
| `windows-x86_64` | Windows 11, MSYS2 UCRT64 | v5.048 |

Nightlies build the current upstream `master` on every platform, including Intel
macOS ahead of its first supported stable release, `v5.054`.

## Use a package

Download an archive from this repository's Releases page, verify it against
`SHA256SUMS-<label>.txt`, extract it, and add its `bin` directory to `PATH`:

```sh
export PATH="/path/to/verilator-v5.048-linux-x86_64/bin:$PATH"
verilator --version
```

On Windows, use an **MSYS2 UCRT64 shell**, with at least:

```sh
pacman -S --needed make perl python mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-zlib
export PATH="/c/tools/verilator-v5.048-windows-x86_64/bin:$PATH"
verilator --version
```

The native executables statically link the GCC runtimes and use Windows' system
libraries, including its built-in UCRT.

### Dependencies

You will still need to install the runtime dependencies yourself.
This generally includes a C++ compiler, Perl, Python 3.6+, and GNU Make.
