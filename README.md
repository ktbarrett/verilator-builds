# Verilator Binaries

Portable distributions of [Verilator](https://github.com/verilator/verilator).
Version tags match upstream version tags.
There is also a special `nightly` release which builds every night.

## GitHub Action

Use this repository as an action to put a specific version of Verilator on `PATH`:

```yaml
steps:
  - uses: ktbarrett/verilator-builds@dev
    with:
      version: '5.048'
  - run: verilator --version
```

Both `5.048` and `v5.048` select the same upstream release. Use `version: nightly`
to install the latest successfully published nightly. The action selects the
archive for the runner's operating system and architecture and verifies its
SHA-256 checksum and build metadata before installing it.

If that release or platform's archive is missing, the action builds the requested
upstream tag from source on the runner. This also applies to releases older than
the binary support cutoffs below. If no nightly build is available, it resolves
upstream `master` to a commit and builds that commit. Old versions still need to
support the runner's compiler and operating system. Source builds install under
the runner's temporary directory and do not publish artifacts to this repository.

The action runs on Linux and macOS, on x86-64 and ARM64. It sets up Python 3.12,
adds Verilator's `bin` directory to `PATH`, and sets `VERILATOR_ROOT` for subsequent
steps. A checkout of your repository is not required to install Verilator.
The action ref (`@dev` above, or a commit SHA) selects the installer implementation;
the `version` input selects Verilator.

| Input | Default | Description |
| --- | --- | --- |
| `version` | Required | An upstream release version, with optional `v`, or `nightly` |
| `token` | `${{ github.token }}` | Token for GitHub metadata and downloads; no write permission needed |
| `install-dependencies` | `'true'` | Install missing runtime tools and source build dependencies |
| `force-source` | `'false'` | Always build from source; `nightly` builds current upstream `master` |
| `jobs` | `'2'` | Parallel compilation jobs for source builds |

Dependency installation uses `apt-get` on Linux (root or passwordless `sudo`) and
Homebrew on macOS. GitHub-hosted Ubuntu and macOS runners include the necessary
package managers, compilers, and GitHub CLI (`gh`). For a self-hosted runner with
`gh` and dependencies already provisioned, set `install-dependencies: 'false'`.
Runtime tools are a C++ compiler,
Perl, Python, and GNU Make. Source builds additionally require autoconf, bison,
flex (including its development headers), help2man, and zlib development headers.
On macOS, install Xcode Command Line Tools and use Homebrew's bison and flex.
Verilator build and installation paths must not contain whitespace.

The action exposes `version` (the actual upstream version with `v`), `path`
(installation prefix), `source-sha` (upstream commit), and `built-from-source`
(`true` or `false`) as step outputs. For example:

```yaml
- uses: ktbarrett/verilator-builds@dev
  id: verilator
  with:
    version: nightly
- run: verilator --version
```

## Platform Support

| Archive platform | Compatibility target | First Verilator release |
| --- | --- | --- |
| `linux-x86_64` | glibc 2.17+, baseline x86-64 | v5.048 |
| `linux-aarch64` | glibc 2.17+, ARMv8-A | v5.048 |
| `macos-x86_64` | macOS 10.15+ | v5.054 |
| `macos-arm64` | macOS 11+ | v5.050 |

Nightlies build the current upstream `master` on every platform, including Intel
macOS ahead of its first supported stable release, `v5.054`.

## Use a package

Download an archive from this repository's Releases page, verify it against
`SHA256SUMS-<label>.txt`, extract it, and add its `bin` directory to `PATH`:

```sh
export PATH="/path/to/verilator-v5.048-linux-x86_64/bin:$PATH"
verilator --version
```

### Dependencies

You will still need to install the runtime dependencies yourself.
This generally includes a C++ compiler, Perl, Python 3.6+, and GNU Make.
