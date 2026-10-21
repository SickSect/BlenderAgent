"""Blender-side checks for the export.

    blender --background --factory-startup --python tests/run_export_check.py

The export is the last mile of the pipeline: a folder of textures becomes a
file the engine opens. This checks that the file is really written, that the
readiness check can refuse it, and that the tools are registered.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "src" / "blender_addon"

FAILURES = []


def check(condition, what):
    if condition:
        print(f"ok   {what}")
    else:
        FAILURES.append(what)
        print(f"FAIL {what}")


def load_package():
    package = types.ModuleType("blender_addon")
    package.__path__ = [str(ADDON_DIR)]
    package.__package__ = "blender_addon"
    sys.modules["blender_addon"] = package
    spec = importlib.util.spec_from_file_location(
        "blender_addon.__init__", ADDON_DIR / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["blender_addon.__init__"] = module
    spec.loader.exec_module(module)
    return module


def make_scene():
    """A cube with a material, selected."""
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ExportCube"
    material = bpy.data.materials.new("ExportMaterial")
    material.use_nodes = True
    cube.data.materials.append(material)
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    return cube


def main():
    package = load_package()
    package.register()
    try:
        export = importlib.import_module("blender_addon.export")
        tools = importlib.import_module("blender_addon.tools")

        print("\n== the tools")
        check(tools.get("export.glb") is not None, "export.glb is registered")
        check(tools.get("export.fbx") is not None, "export.fbx is registered")
        check(tools.get("export.check") is not None, "export.check is registered")

        print("\n== the export writes a file")
        cube = make_scene()
        folder = tempfile.mkdtemp(prefix="gaa_export_")
        result = export.export(folder, "glb")
        check(result.get("ok"), f"the export finished ({result.get('error')})")
        path = Path(result.get("path", ""))
        check(path.exists(), f"the file is there ({path.name})")
        check(path.suffix == ".glb", f"the extension is right ({path.suffix})")
        check(
            path.exists() and path.stat().st_size > 0,
            "the file is not empty",
        )
        if path.exists():
            # A GLB is a binary container: the magic says what it is.
            head = path.read_bytes()[:4]
            check(head == b"glTF", f"the file is a glTF container ({head!r})")

        print("\n== the separate gltf is readable json")
        separate = export.export(folder, "gltf")
        check(separate.get("ok"), f"the gltf export finished ({separate.get('error')})")
        gltf_path = Path(separate.get("path", ""))
        if gltf_path.exists():
            try:
                data = json.loads(gltf_path.read_text(encoding="utf-8"))
                check("meshes" in data, "the file has meshes")
                check("materials" in data, "the file has materials")
            except Exception as exc:  # noqa: BLE001
                check(False, f"the file is a readable glTF ({exc})")

        print("\n== nothing selected, nothing written")
        bpy.ops.object.select_all(action="DESELECT")
        empty = export.export(folder, "glb")
        check(not empty.get("ok"), "the export refuses without a selection")
        check(
            "Nothing is selected" in empty.get("error", ""),
            f"the refusal explains itself ({empty.get('error')})",
        )

        print("\n== the fbx")
        make_scene()
        fbx = export.export(folder, "fbx")
        check(fbx.get("ok"), f"the fbx export finished ({fbx.get('error')})")
        fbx_path = Path(fbx.get("path", ""))
        check(fbx_path.exists(), f"the fbx file is there ({fbx_path.name})")

        print("\n== the operator")
        make_scene()
        folder2 = tempfile.mkdtemp(prefix="gaa_export_op_")
        pressed = bpy.ops.gaa.export_asset(filepath=folder2, format="glb")
        check(pressed == {"FINISHED"}, f"the button works ({pressed})")
        written = list(Path(folder2).glob("*.glb"))
        check(bool(written), f"the button wrote a file ({[p.name for p in written]})")

        print("\n== the report")
        report = export._report(result)
        check(str(path.name) in report, f"the report names the file ({report[:70]})")
        check(
            "did not happen" in export._report({"ok": False, "error": "because"}),
            "a failed export says so",
        )
    finally:
        package.unregister()

    print()
    if FAILURES:
        print(f"RESULT: FAILED ({len(FAILURES)})")
        for line in FAILURES:
            print(f"  - {line}")
        sys.exit(1)
    print("RESULT: OK")


main()
