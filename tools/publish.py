"""Verify a full build set, publish it, and retire obsolete nightly assets."""

import argparse
import json
import os
import re
import tarfile
from pathlib import Path

from .config import CONFIG, archive_name, expected_assets, source_name, supported_platforms, version
from .github import GitHub
from .package import sha256
from .releases import MARKER, complete, release_state
from .upstream import archive_version


def assemble(directory, label, sha, recipe):
    source = directory / source_name(label)
    if not source.is_file() or not source.stat().st_size:
        raise ValueError("Missing corresponding upstream source archive")
    release = archive_version(source)
    platforms = supported_platforms(label)
    if not platforms:
        raise ValueError(f"No platforms support Verilator {release}")
    required_assets = expected_assets(label, release)
    manifests = []
    for platform in platforms:
        manifest = json.loads((directory / f"{platform}.manifest.json").read_text())
        name = archive_name(label, platform)
        archive = directory / name
        if manifest.get("abi_audited") is not True:
            raise ValueError(f"Package has not passed its ABI audit: {name}")
        for field, value in {
            "sha": sha,
            "recipe": recipe,
            "label": label,
            "platform": platform,
            "archive": name,
            "source_version": release,
        }.items():
            if manifest.get(field) != value:
                raise ValueError(f"Mismatched {field} in {platform} manifest")
        if manifest["sha256"] != sha256(archive):
            raise ValueError(f"Checksum mismatch: {name}")
        with tarfile.open(archive) as package:
            embedded = json.load(package.extractfile(name[:-7] + "/manifest.json"))
        if embedded != {k: v for k, v in manifest.items() if k not in {"archive", "sha256"}}:
            raise ValueError(f"Embedded manifest mismatch: {name}")
        manifests.append(manifest)
    source_hash = sha256(source)
    # The source asset comes from the first supported platform. Other jobs may use a
    # different git/zlib version, so compare their source commit rather than
    # expecting byte-identical gzip streams across operating systems.
    source_manifest = manifests[0]
    if source_manifest.get("source_sha256") != source_hash:
        raise ValueError("Source archive checksum differs from the build manifest")
    with tarfile.open(source) as archive:
        if archive.pax_headers.get("comment") != sha:
            raise ValueError("Source archive does not identify the requested upstream commit")
    combined = {
        "schema": 1,
        "sha": sha,
        "recipe": recipe,
        "label": label,
        "source_version": release,
        "source": {"archive": source.name, "sha256": source_hash},
        "packages": manifests,
    }
    (directory / f"manifest-{label}.json").write_text(json.dumps(combined, indent=2) + "\n")
    paths = [
        directory / name for name in sorted(required_assets) if not name.startswith("SHA256SUMS-")
    ]
    checksums = directory / f"SHA256SUMS-{label}.txt"
    checksums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in paths))
    return [*paths, checksums]


def obsolete_assets(assets, label, source_version=None):
    keep = expected_assets(label, source_version)
    return [a for a in assets if a["name"] not in keep]


def cleanup_nightly(api):
    """Recover after an interrupted upload or an interrupted cleanup."""
    nightly = next((r for r in api.releases() if r["tag_name"] == "nightly"), None)
    if not nightly:
        return
    state = release_state(nightly)
    assets = api.assets(nightly)
    if state and complete(nightly, assets, state["sha"], state["recipe"]):
        for asset in obsolete_assets(assets, state["label"], state.get("source_version")):
            api.delete_asset(asset)
    elif nightly["draft"] and not state:
        # An interrupted first publication has no successful set to preserve.
        for asset in assets:
            api.delete_asset(asset)


def publish(api, directory, mode, label, sha, recipe):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9a-f]{40}", recipe):
        raise ValueError("Publication requires full source and packaging commit SHAs")
    if mode == "stable":
        if version(label) is None or api.commit(CONFIG["upstream"], f"refs/tags/{label}") != sha:
            raise ValueError("Stable release does not match its upstream tag")
    paths = assemble(directory, label, sha, recipe)
    tag = label if mode == "stable" else "nightly"
    releases = api.releases()
    release = next((r for r in releases if r["tag_name"] == tag), None)
    if release and mode == "nightly" and not release["draft"] and not release_state(release):
        raise ValueError("Refusing to replace an unmanaged nightly release")
    if release and mode == "stable":
        state = release_state(release)
        if not state and not release["draft"]:
            raise ValueError("Refusing to replace a release not managed by this repository")
        if state and state["sha"] != sha:
            raise ValueError("Upstream tag changed since publication; investigate manually")
        if complete(release, api.assets(release), sha):
            print(f"{tag} is already complete; preserving the published release")
            return
        # An incomplete stable release must not appear as a complete download set.
        release = api.update_release(release, draft=True)
    if not release:
        release = api.create_release(tag, recipe, nightly=mode == "nightly")
    # Nightly names are generation-specific, so a failed upload preserves the old set.
    api.upload(release, paths)
    uploaded = {a["name"]: a for a in api.assets(release)}
    for path in paths:
        asset = uploaded.get(path.name)
        if not asset or asset.get("size") != path.stat().st_size:
            raise ValueError(f"Upload verification failed: {path.name}")
        # GitHub exposes digests for current uploads; enforce them when available.
        if asset.get("digest") and asset["digest"] != f"sha256:{sha256(path)}":
            raise ValueError(f"Uploaded digest mismatch: {path.name}")
    manifest = json.loads((directory / f"manifest-{label}.json").read_text())
    state = {
        "sha": sha,
        "recipe": recipe,
        "label": label,
        "source_version": manifest["source_version"],
    }
    platforms = ", ".join(package["platform"] for package in manifest["packages"])
    body = (
        f"Verilator binaries built from [{sha}](https://github.com/{CONFIG['upstream']}/commit/{sha}).\n\n"
        f"Packaging revision: [{recipe}](https://github.com/{api.repository}/commit/{recipe}).\n\n"
        f"Includes: {platforms}. "
        "See the repository README for compatibility targets and required build tools. "
        "Source, manifests, and SHA-256 checksums are included.\n\n"
        f"<!-- {MARKER} {json.dumps(state, separators=(',', ':'))} -->\n"
    )
    if mode == "stable":
        body = (
            f"Upstream release: https://github.com/{CONFIG['upstream']}/releases/tag/{label}\n\n"
            + body
        )
    else:
        body += "\nRolling prerelease; replaced after the next successful nightly build.\n"
    newer = (
        any(
            version(r["tag_name"]) and version(r["tag_name"]) > version(label)
            for r in releases
            if not r["draft"] and not r["prerelease"]
        )
        if mode == "stable"
        else True
    )
    api.update_release(
        release,
        draft=False,
        prerelease=mode == "nightly",
        body=body,
        name=f"Verilator {label}" if mode == "stable" else f"Verilator nightly ({sha[:12]})",
        make_latest="false" if newer else "true",
    )
    if mode == "nightly":
        # Mirror tags identify packaging commits, not absent upstream objects.
        api.update_tag("nightly", recipe)
        cleanup_nightly(api)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--mode", choices=["stable", "nightly", "cleanup"], required=True)
    parser.add_argument("--label")
    parser.add_argument("--sha")
    parser.add_argument("--recipe")
    args = parser.parse_args()
    api = GitHub(os.environ["GITHUB_REPOSITORY"])
    if args.mode == "cleanup":
        cleanup_nightly(api)
    else:
        if not all((args.label, args.sha, args.recipe)):
            parser.error("Publication requires --label, --sha, and --recipe")
        publish(api, args.directory, args.mode, args.label, args.sha, args.recipe)


if __name__ == "__main__":
    main()
