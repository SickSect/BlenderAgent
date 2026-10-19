#!/usr/bin/env python
"""Build the Blender extension zip for the Asset Assistant.

Vendors ``src/asset_core`` into the add-on so the archive is self-contained:

    dist/asset_assistant-<version>.zip
    ├── blender_manifest.toml
    ├── __init__.py
    ├── ...add-on modules...
    └── asset_core/          (vendored copy)

Install with Edit > Preferences > Add-ons > Install from Disk (Blender 4.2+),
or validate first:

    blender --command extension validate dist/asset_assistant-0.1.0.zip

If BLENDER_BIN is set, validation runs automatically after the build.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON_SRC = ROOT / "src" / "blender_addon"
CORE_SRC = ROOT / "src" / "asset_core"
DIST = ROOT / "dist"
PACKAGE_ID = "asset_assistant"
EXCLUDE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo")


def read_version() -> str:
    manifest = (ADDON_SRC / "blender_manifest.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def stage_copy(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(*EXCLUDE_PATTERNS),
        dirs_exist_ok=True,
    )


def build() -> Path:
    if not ADDON_SRC.is_dir():
        raise SystemExit(f"add-on source not found: {ADDON_SRC}")
    if not CORE_SRC.is_dir():
        raise SystemExit(f"core source not found: {CORE_SRC}")

    version = read_version()
    stage = Path(tempfile.mkdtemp(prefix="gaa_build_"))
    try:
        stage_copy(ADDON_SRC, stage)
        stage_copy(CORE_SRC, stage / "asset_core")

        # Extension zips keep blender_manifest.toml + __init__.py at the root.
        manifest = stage / "blender_manifest.toml"
        if not manifest.is_file():
            raise SystemExit("blender_manifest.toml missing after staging")
        if not (stage / "__init__.py").is_file():
            raise SystemExit("__init__.py missing after staging")

        DIST.mkdir(exist_ok=True)
        out = DIST / f"{PACKAGE_ID}-{version}.zip"
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        return out
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def validate(zip_path: Path) -> None:
    blender = os.environ.get("BLENDER_BIN")
    if not blender:
        print("BLENDER_BIN не задан — пропускаю валидацию blender --command extension validate")
        return
    result = subprocess.run(
        [blender, "--command", "extension", "validate", str(zip_path)],
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(f"extension validate failed: {result.returncode}")


def main() -> None:
    zip_path = build()
    print(f"Built: {zip_path}")
    validate(zip_path)


if __name__ == "__main__":
    main()
