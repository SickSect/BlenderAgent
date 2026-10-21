#!/usr/bin/env python
"""Prepare a release of the Blender add-on.

The publishing itself is manual (a GitHub release is a web form), so this
script does the boring half: it keeps the version in one place, builds the
installable zip and prints exactly what to click next.

    python tools/release.py --bump 0.5.10   # write the new version everywhere
    python tools/release.py                 # build the zip and print the steps

It never touches the network.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "blender_addon" / "blender_manifest.toml"
INIT = ROOT / "src" / "blender_addon" / "__init__.py"
README = ROOT / "README.md"

VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
INIT_RE = re.compile(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)')
README_RE = re.compile(r"asset_assistant-(\d+\.\d+\.\d+)\.zip")


def read_version() -> str:
    """The version the add-on claims to be."""
    match = VERSION_RE.search(MANIFEST.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"no version in {MANIFEST}")
    return match.group(1)


def check_consistency(version: str) -> list:
    """The places that have to agree about the version."""
    problems = []
    init = INIT.read_text(encoding="utf-8")
    match = INIT_RE.search(init)
    if match is None:
        problems.append(f"{INIT.name}: no version")
    else:
        numbers = ".".join(match.groups())
        if numbers != version:
            problems.append(f"{INIT.name}: {numbers} != {version}")
    readme = README.read_text(encoding="utf-8")
    for found in set(README_RE.findall(readme)):
        if found != version:
            problems.append(f"README.md: mentions {found}")
    return problems


def write_version(version: str) -> None:
    """Write *version* into the manifest, ``__init__.py`` and the README."""
    parts = [int(part) for part in version.split(".")]
    if len(parts) != 3:
        raise SystemExit("the version must be major.minor.patch")

    manifest = MANIFEST.read_text(encoding="utf-8")
    manifest = VERSION_RE.sub(f'version = "{version}"', manifest, count=1)
    MANIFEST.write_text(manifest, encoding="utf-8")

    init = INIT.read_text(encoding="utf-8")
    init = INIT_RE.sub(
        '"version": ({}, {}, {})'.format(*parts), init, count=1
    )
    INIT.write_text(init, encoding="utf-8")

    readme = README.read_text(encoding="utf-8")
    readme = README_RE.sub(f"asset_assistant-{version}.zip", readme)
    README.write_text(readme, encoding="utf-8")
    print(f"version set to {version}")


def git(*args) -> str:
    """Run a git command and return its output ("" when it fails)."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True
        )
        return result.stdout if result.returncode == 0 else ""
    except OSError:
        return ""


def commits_since_the_last_tag() -> list:
    """The commits of this release, as one-liners."""
    tags = git("tag", "--list").split()
    if not tags:
        return git("log", "--oneline", "-20").splitlines()
    last = sorted(tags)[-1]
    out = git("log", "--oneline", f"{last}..HEAD").splitlines()
    return out or git("log", "--oneline", "-20").splitlines()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bump", metavar="VERSION", help="write a new version everywhere"
    )
    args = parser.parse_args()

    if args.bump:
        write_version(args.bump.strip())

    version = read_version()
    problems = check_consistency(version)
    if problems:
        print("the version does not agree everywhere:")
        for line in problems:
            print(f"  - {line}")
        raise SystemExit(1)

    sys.path.insert(0, str(ROOT / "tools"))
    import build_addon  # noqa: E402 - the builder lives next to this script

    zip_path = build_addon.build()
    print(f"\nbuilt: {zip_path.relative_to(ROOT)}")

    print("\nNext:")
    print(f"  1. git add -A && git commit -m \"release: {version}\"")
    print(f"  2. git tag v{version} && git push origin main v{version}")
    print("  3. GitHub -> Releases -> Draft a new release -> pick the tag "
          f"v{version}")
    print(f"  4. Attach the file: {zip_path.name}")
    print("  5. Paste the notes (RELEASE_NOTES.md) and publish")

    commits = commits_since_the_last_tag()
    if commits:
        print("\nWhat this release contains:")
        for line in commits:
            print(f"  {line}")


if __name__ == "__main__":
    main()
