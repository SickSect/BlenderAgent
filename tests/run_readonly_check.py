"""Do the analysis buttons touch the scene?

    blender --background --factory-startup --python tests/run_readonly_check.py

A button that only *reports* must not change anything: not a vertex, not a
UV, not a socket value, not the mode, not the selection. This takes a
fingerprint of the whole scene, runs every reporting tool and operator, and
compares.

A tool that reports and also acts (the doctor that repairs, the bake) is not
in this list - those are supposed to change things.
"""

from __future__ import annotations

import importlib
import json
import sys
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


# --- the fingerprint ---------------------------------------------------------


def _socket_value(socket):
    try:
        value = socket.default_value
    except Exception:  # noqa: BLE001
        return None
    try:
        return [round(float(v), 6) for v in value]
    except TypeError:
        return round(float(value), 6)
    except Exception:  # noqa: BLE001
        return str(value)


def fingerprint() -> dict:
    """Everything about the scene that a report must not change."""
    scene = bpy.context.scene
    data = {
        "mode": bpy.context.mode,
        "engine": scene.render.engine,
        "frame": scene.frame_current,
        "active": getattr(bpy.context.view_layer.objects.active, "name", None),
        "selected": sorted(o.name for o in bpy.context.selected_objects),
        "settings": {
            name: str(getattr(scene, name, None))
            for name in (
                "gaa_uv_angle",
                "gaa_uv_method",
                "gaa_uv_margin",
                "gaa_uv_density",
                "gaa_uv_atlas",
                "gaa_uv_use_existing_seams",
                "gaa_bake_size",
                "gaa_bake_samples",
                "gaa_bake_maps",
                "gaa_bake_naming",
            )
        },
        "objects": [],
        "materials": [],
        "images": [],
    }
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        entry = {
            "name": obj.name,
            "type": obj.type,
            "matrix": [round(v, 6) for row in obj.matrix_world for v in row],
            "modifiers": [(m.name, m.type) for m in obj.modifiers],
            "materials": [m.name if m else None for m in obj.material_slots],
            "vertices": [],
            "uvs": [],
        }
        mesh = getattr(obj, "data", None)
        if mesh is not None and hasattr(mesh, "vertices"):
            entry["vertices"] = [
                (round(v.co.x, 6), round(v.co.y, 6), round(v.co.z, 6))
                for v in mesh.vertices
            ]
            for layer in mesh.uv_layers:
                entry["uvs"].append(
                    [
                        (round(uv.uv[0], 6), round(uv.uv[1], 6))
                        for uv in layer.data
                    ]
                )
        data["objects"].append(entry)

    for mat in sorted(bpy.data.materials, key=lambda m: m.name):
        entry = {"name": mat.name, "use_nodes": getattr(mat, "use_nodes", None),
                 "custom": {k: str(v) for k, v in mat.items()}, "nodes": [], "links": []}
        tree = getattr(mat, "node_tree", None)
        if tree is not None:
            for node in tree.nodes:
                entry["nodes"].append(
                    [
                        node.name,
                        node.type,
                        node.location[:],
                        [(s.name, _socket_value(s)) for s in node.inputs],
                    ]
                )
            for link in tree.links:
                entry["links"].append(
                    [link.from_node.name, link.from_socket.name,
                     link.to_node.name, link.to_socket.name]
                )
        data["materials"].append(entry)

    for image in sorted(bpy.data.images, key=lambda i: i.name):
        data["images"].append(
            [
                image.name,
                image.filepath,
                image.colorspace_settings.name,
                bool(image.packed_file),
                list(image.size),
            ]
        )
    return data


def _diff(before: dict, after: dict) -> list:
    """What changed between the two fingerprints."""
    problems = []
    for key in ("mode", "engine", "frame", "active", "selected", "settings"):
        if before[key] != after[key]:
            problems.append(f"{key}: {before[key]!r} -> {after[key]!r}")
    for key in ("objects", "materials", "images"):
        old = json.dumps(before[key], sort_keys=True, default=str)
        new = json.dumps(after[key], sort_keys=True, default=str)
        if old != new:
            problems.append(f"{key} changed")
    return problems


# --- the scene ---------------------------------------------------------------


def build_scene():
    """A scene with something to break: a textured mesh, a UV, a loose image."""
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ReadOnlyCube"

    material = bpy.data.materials.new("ReadOnlyMaterial")
    material.use_nodes = True
    cube.data.materials.append(material)

    # A texture in the wrong colour space and a second material on the side.
    scratch = bpy.data.images.new("ReadOnlyTexture", width=8, height=8)
    scratch.colorspace_settings.name = "sRGB"
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = scratch
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])

    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    return cube


def main():
    package = load_package()
    package.register()
    try:
        tools = importlib.import_module("blender_addon.tools")
        operators = importlib.import_module("blender_addon.operators")

        build_scene()
        # A UV layer, so the UV audit has something to read.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project()
        bpy.ops.object.mode_set(mode="OBJECT")

        print("\n== the reporting tools")
        reporting_tools = [
            "material.audit",
            "export.check",
            "textures.report",
            "uv.audit",
            "bake.check",
            "procedural.list",
            "procedural.find",
            "materials.find_users",
            "tools.list",
            "tools.history",
        ]
        for tool_id in reporting_tools:
            if tools.get(tool_id) is None:
                check(False, f"{tool_id} is registered")
                continue
            before = fingerprint()
            result = tools.execute(tool_id, {})
            after = fingerprint()
            problems = _diff(before, after)
            check(
                not problems,
                f"{tool_id} changes nothing ({'; '.join(problems[:3])})",
            )
            check(result.ok, f"{tool_id} answers ({result.report[:60]})")

        print("\n== the reporting buttons")
        reporting_buttons = [
            "gaa.material_doctor",
            "gaa.check_export",
            "gaa.texture_report",
            "gaa.check_uv",
            "gaa.find_material_users",
        ]
        for operator_id in reporting_buttons:
            before = fingerprint()
            result = bpy.ops.ui_dummy() if False else None
            try:
                getattr(bpy.ops.gaa, operator_id.split(".")[1])()
            except Exception as exc:  # noqa: BLE001
                check(False, f"{operator_id} runs ({exc})")
                continue
            after = fingerprint()
            problems = _diff(before, after)
            check(
                not problems,
                f"{operator_id} changes nothing ({'; '.join(problems[:3])})",
            )
        print("\n== the acting buttons leave no mess")
        acting_buttons = [
            "gaa.unwrap_uv",
            "gaa.match_density",
            "gaa.build_atlas",
            "gaa.height_to_normal",
            "gaa.material_fix",
            "gaa.fix_paths",
            "gaa.pack_textures",
            "gaa.procedural_build",
            "gaa.assign_to_selection",
        ]
        for operator_id in acting_buttons:
            name = operator_id.split(".")[1]
            if not hasattr(bpy.ops.gaa, name):
                check(False, f"{operator_id} is registered")
                continue
            bpy.ops.object.mode_set(mode="OBJECT")
            try:
                getattr(bpy.ops.gaa, name)()
            except Exception as exc:  # noqa: BLE001
                check(False, f"{operator_id} runs without raising ({exc})")
                continue
            check(True, f"{operator_id} runs")
            check(
                bpy.context.mode == "OBJECT",
                f"{operator_id} leaves the mesh in object mode "
                f"(it is {bpy.context.mode})",
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
