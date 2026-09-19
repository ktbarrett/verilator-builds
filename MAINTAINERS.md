
## Automation

`Build releases` runs daily. It enumerates upstream tags, starting
at `first_release` in `config.json`, and compares them with complete published
releases. It builds up to three missing stable releases per run, oldest first,
and checks upstream `master` for a nightly build. A change to this repository's
packaging commit also triggers a new nightly. Unchanged, complete builds are
skipped. Failed builds and incomplete uploads are retried on a later run.

All platforms build the same resolved upstream commit. Stable publication first
uploads into a draft and publishes only after all five packages pass validation.
Existing complete stable releases are preserved. A moved upstream tag causes an
error rather than silently replacing its published binaries. Older upstream tags
can be requested manually; their compatibility with these recipes is not promised.

The rolling nightly keeps **one successful generation**. Replacement assets have
unique names and are verified before the release switches to them; obsolete
assets are then deleted. The next scheduled run also removes uploads left behind
by an interrupted attempt. A failed replacement leaves the previous successful
nightly available, even when it is older than one day. Storage temporarily holds
both generations during replacement. Intermediate Actions artifacts expire after
one day, and no persistent build cache is used. Stable release assets are retained.

Each release contains five binary archives, the corresponding upstream source
archive, a combined manifest, and SHA-256 checksums. The source archive is an exact
export of the upstream commit. Packaging changes to installed metadata are
implemented in this repository. GitHub's automatically generated source archives
refer to **this packaging repository**, not upstream Verilator; use the explicitly
attached `verilator-<label>-source.tar.gz` instead. Mirror tags identify packaging
commits; manifests identify the upstream source commit.

GitHub schedules can be delayed and run only from the default branch. GitHub may
disable scheduled workflows in inactive public repositories; re-enable them in
Actions if necessary. Runner availability and repository Actions quotas apply.

## Start the first release or build a specific revision

Push these files to the repository's default branch and enable GitHub Actions.
Use **Actions → Build releases → Run workflow**:

| Mode | Ref | Result |
| --- | --- | --- |
| `build` (default) | Any upstream tag or commit | One-day Actions artifacts; no release changes |
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

Standalone utilities are Python modules using the standard library (Python 3.12+).
GitHub API utilities also use `gh`. Responsibilities are separated:

- `tools/discover.py`: resolve requested source revisions and find missing releases.
- `tools/build.py`, `tools/linux.py`: build a private source export on the target platform.
- `tools/package.py`: relocate installed metadata, create archives and provenance.
- `tools/validate.py`: inspect executable dependencies and run the installed smoke test.
- `tools/publish.py`: verify complete sets, publish, and clean up nightly assets.
- `config.json`: seed release, platform compatibility targets, runners, and images.

Build locally from an existing checkout without changing that checkout:

```sh
python3.12 -m tools.build --source ../verilator \
  --sha "$(git -C ../verilator rev-parse 'v5.048^{commit}')" \
  --label v5.048 --recipe "$(git rev-parse HEAD)" --platform linux-x86_64
```

This requires autoconf, flex, bison, help2man, make, Perl, Python, the target C++
compiler, and platform binary inspection tools. Linux ABI validation requires a
manylinux-compatible environment. For a functional development build on a newer
Linux host, `--skip-abi-audit` is available; it is forbidden in CI and publication
rejects such packages. Outputs go to ignored `dist/`; temporary builds use ignored
`work/`. Build and install paths must not contain spaces.

Linux builds need the static libatomic archive (the manylinux setup installs
`libatomic-static`). After configure, the build replaces `-latomic` with
`-l:libatomic.a` in Verilator's `CFG_LIBS` make variable. This keeps atomic helper
code in the executable while leaving glibc dynamically linked. The override is
recorded in the package manifest and does not change generated simulation link flags.

Every CI package is extracted to a new directory after its build source and
staging installation have been removed. Validation checks version provenance,
linting, generated C++ compilation, simulation results, VCD tracing, and coverage.
ELF audits reject symbols above glibc 2.17 and external compiler runtimes; Mach-O
audits check architecture, minimum OS, and system-only libraries; PE audits reject
VC, GCC, MSYS, and other non-system DLL imports.

```sh
python3.12 -m unittest discover -s tests -v
ruff check .
ruff format --check .
```

Pull requests and pushes also build the seed release across all five platforms.
GitHub Actions are pinned to commits. Manylinux image digests are recorded in
manifests, but the configured image tags and installed build dependencies can
advance; these builds are not claimed to be bit-for-bit reproducible. Pin image
digests in `config.json` when a fixed build environment is desired. New upstream
submodules deliberately stop source export until the source packaging is updated.
