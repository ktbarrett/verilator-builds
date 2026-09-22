
## Automation

`Build releases` runs daily. It enumerates upstream tags, starting
at `first_release` in `config.json`, and compares them with complete published
releases. It builds up to three missing stable releases per run, oldest first,
and checks upstream `master` for a nightly build. A change to this repository's
packaging commit also triggers a new nightly. Unchanged, complete builds are
skipped. Failed builds and incomplete uploads are retried on a later run.

All platforms build the same resolved upstream commit. Stable publication first
uploads into a draft and publishes only after every supported platform passes validation.
Existing complete stable releases are preserved. A moved upstream tag causes an
error rather than silently replacing its published binaries.

Each platform has an inclusive `first_release` in `config.json`: Linux starts
at `v5.048`, macOS ARM64 at `v5.050`, and macOS Intel at `v5.054`. The top-level
`first_release` controls automatic release discovery. Versioned labels use these
release floors to select platforms. Development labels, including `master` and
the generated nightly labels, build every platform. This same label determines the
matrix, local build eligibility, required release assets, and nightly cleanup.
Versioned labels older than every platform's cutoff fail with a clear error.
For local builds, use a release tag as the label to apply its platform minimums,
or `--label master` to test every platform with a development checkout.

The rolling nightly keeps **one successful generation**. Replacement assets have
unique names and are verified before the release switches to them; obsolete
assets are then deleted. The next scheduled run also removes uploads left behind
by an interrupted attempt. A failed replacement leaves the previous successful
nightly available, even when it is older than one day. Storage temporarily holds
both generations during replacement. Intermediate Actions artifacts expire after
one day, and no persistent build cache is used. Stable release assets are retained.

Each release contains one binary archive per supported platform, the corresponding upstream source
archive, a combined manifest, and SHA-256 checksums. The source archive is an exact
export of the upstream commit. Packaging changes to installed metadata are
implemented in this repository. GitHub's automatically generated source archives
refer to **this packaging repository**, not upstream Verilator; use the explicitly
attached `verilator-<label>-source.tar.gz` instead. Mirror tags identify packaging
commits; manifests identify the upstream source commit.
Manifests also record the declared source version. Publication verifies that stable
labels and package metadata match the source archive. Discovery requires only the
packages selected by the label: two for `v5.048`, three for `v5.050` and `v5.052`,
and four from `v5.054` onward. Development labels always require every platform.

GitHub schedules can be delayed and run only from the default branch. GitHub may
disable scheduled workflows in inactive public repositories; re-enable them in
Actions if necessary. Runner availability and repository Actions quotas apply.

## Start the first release or build a specific revision

Push these files to the repository's default branch and enable GitHub Actions.
Use **Actions → Build releases → Run workflow**:

| Mode | Ref | Result |
| --- | --- | --- |
| `build` (default) | Any upstream tag or commit | Test every platform; one-day Actions artifacts |
| `stable` | `v5.048` | Publish the first mirrored release |
| `auto` | Ignored | Catch up on releases from v5.048 and update nightly |
| `nightly` | `master`, a tag, or a commit | Force replacement of the rolling prerelease |

Or, with GitHub CLI:

```sh
gh workflow run releases.yml -f mode=stable -f ref=v5.048
gh workflow run releases.yml -f mode=build -f ref=<upstream-commit>
gh workflow run releases.yml -f mode=auto
```

The standard `GITHUB_TOKEN` is sufficient. Only release management jobs request
`contents: write`; compilation and PR jobs use `contents: read`, do not persist
checkout credentials, and receive no publication token in their environment.
All release runs share a concurrency group. Avoid enabling GitHub immutable
releases for the rolling nightly, which requires asset deletion and tag updates.

## Maintain and test the recipes

Use [uv](https://docs.astral.sh/uv/) to create the Python 3.12+ development
environment and install the project dependencies and development tools:

```sh
uv sync --locked --python 3.12
uv run pre-commit install
```

`uv sync --locked` creates `.venv/` from the committed `uv.lock` and fails if
the project dependencies and lockfile disagree. This repository is a collection of
tools run from its checkout, so uv manages dependencies without building or
installing the repository as a Python package. Environment activation is optional;
use `uv run` to execute commands in it.

The pre-commit hooks run Ruff lint fixes followed by formatting. Review and stage
any changes they make before committing again. Both hooks and the development
dependency use Ruff 0.16.6; update their versions together.

The utilities use PyGithub for GitHub operations and Pydantic when reading
release metadata from GitHub. Responsibilities are separated:

- `tools/discover.py`: resolve requested source revisions and find missing releases.
- `tools/upstream.py`: read the source version from upstream metadata.
- `tools/github.py`: authenticate GitHub API calls, paginate results, and manage release assets.
- `tools/releases.py`: validate published generation metadata shared by discovery and publication.
- `tools/matrix.py`: select supported build targets and resolve the CI test release.
- `tools/build.py`, `tools/linux.py`: build a private source export on the target platform.
- `tools/package.py`: relocate installed metadata, create archives and provenance.
- `tools/elf.py`, `tools/macho.py`: inspect binary compatibility using pyelftools and macholib.
- `tools/validate.py`: validate archive provenance and run the installed smoke test.
- `tools/publish.py`: verify complete sets, publish, and clean up nightly assets.
- `config.json`: discovery start, per-platform first releases, compatibility targets, runners, and images.

Packaging explicitly sets the Perl launcher's data path to `../share/verilator`
and public-script redirects to `../../../bin`. This keeps the archive relocatable.
Users do not need to set `VERILATOR_ROOT`.

Build locally from an existing checkout without changing that checkout:

```sh
uv run python -m tools.build --source ../verilator \
  --sha "$(git -C ../verilator rev-parse 'v5.048^{commit}')" \
  --label v5.048 --recipe "$(git rev-parse HEAD)" --platform linux-x86_64
```

This requires autoconf, flex, bison, help2man, make, Perl, Python, the target C++
compiler, and the platform
`strip` utility. Linux ABI validation requires a
manylinux-compatible environment. For a functional development build on a newer
Linux host, `--skip-abi-audit` is available; it is forbidden in CI and publication
rejects such packages. Outputs go to ignored `dist/`; temporary builds use ignored
`work/`. Build and install paths must not contain spaces.

Linux builds need the static libatomic archive. The manylinux setup first checks
`g++ -print-file-name=libatomic.a` and uses the toolchain's existing archive. If
missing, it asks yum for a package providing `*/libatomic.a`, since RPM names vary
between architectures and toolchains, then checks the compiler can find it.
After configure, the build replaces `-latomic` with
`-l:libatomic.a` in Verilator's `CFG_LIBS` make variable. This keeps atomic helper
code in the executable while leaving glibc dynamically linked. The override is
recorded in the package manifest and does not change generated simulation link flags.

Every CI package is extracted to a new directory after its build source and
staging installation have been removed. Validation checks version provenance,
linting, generated C++ compilation, simulation results, VCD tracing, and coverage.
ELF audits reject symbols above glibc 2.17 and external compiler runtimes; Mach-O
audits check architecture, minimum OS, and system-only libraries.

```sh
uv run python -m pytest
uv run pre-commit run --all-files
```

The Python CI job uses the same environment setup, hooks, and test command.
Pytest runs both fixture-based tests and the existing unittest tests. To check Ruff
without editing files, run `uv run ruff check .` and `uv run ruff format --check .`.

Pull requests and pushes resolve the current upstream `master` to an immutable
commit and build every platform. No development-version cutoff is required.
GitHub Actions are pinned to commits. Manylinux image digests are recorded in
manifests, but the configured image tags and installed build dependencies can
advance; these builds are not claimed to be bit-for-bit reproducible. Pin image
digests in `config.json` when a fixed build environment is desired. New upstream
submodules deliberately stop source export until the source packaging is updated.

## Python dependencies and compatibility checks

PyGithub handles API requests, pagination, asset uploads,
release updates, and tag updates. The adapter reads `GH_TOKEN`, falling back to
`GITHUB_TOKEN`; public reads can also run without credentials. Python utilities
no longer require `gh`. Automatic HTTP retries are disabled so an ambiguous write
failure cannot replay a mutation. Scheduled runs reconcile remote state and retry
incomplete publications. Uploads replace assets with matching names; nightly
filenames include their generation so this preserves the previous successful set.
Publication still checks uploaded sizes and available digests before switching
the release marker and removing obsolete assets.

Pydantic validates release markers read from GitHub without coercing provenance
values. Legacy markers may omit `source_version`. The checked-in configuration
and manifests generated by our own jobs use ordinary dictionaries and JSON; they
do not need internal data-contract models. The manifest schema stays at version 1.
Checksum matching, source/recipe identity, ABI audit status, embedded/sidecar
manifest equality, platform floors, and publication ordering remain explicit
checks in their owning modules.

The ELF reader checks architecture, dynamic libraries, RPATH/RUNPATH, and GNU
symbol-version requirements. Packages with missing version sections are rejected
when their dynamic table advertises those requirements. The Mach-O reader checks
architecture and subtype, library load commands, RPATH, and both modern and legacy
macOS deployment-target commands. These readers do not execute the inspected
binaries or require `readelf`, `objdump`, `otool`, or `lipo`. Unit tests exercise
our compatibility rules using parsed metadata. The hosted build jobs exercise the real readers while
auditing and smoke-testing every produced package on its target platform.

Every workflow installs the locked runtime dependencies with uv before invoking
Python modules. Linux containers bootstrap their own environment at
`/tmp/verilator-builds-venv` using the manylinux Python 3.12 interpreter; they do
not reuse the host's `.venv/`. CI bootstraps uv 0.12.17. Dependency updates must
retain wheels compatible with both manylinux2014 architectures and both macOS
runners. Update `uv.lock` deliberately with `uv lock --upgrade`, then run the
unit tests and all four package builds.
