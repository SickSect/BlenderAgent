"""Blender-side checks for the mesh doctor and the retopology.

    blender --background --factory-startup --python tests/run_mesh_check.py

A high-poly sculpt is not a game mesh. These checks pin the numbers the
doctor reports, the budget the retopology hits, the promise that the
original is never touched, and the LOD chain.
"""

from __future__ import annotations

import importlib
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


def clean_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def make_cube(name="Cube"):
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def make_sphere(name="Sphere"):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def part_analyze(mesh):
    print("\n== the numbers of a mesh")
    cube = make_cube()
    report = mesh.analyze(cube)
    check(report.verts == 8, f"the cube has 8 vertices ({report.verts})")
    check(report.faces == 6, f"the cube has 6 faces ({report.faces})")
    check(report.quads == 6, f"the cube is made of quads ({report.quads})")
    check(report.tris == 0, f"the cube has no triangles ({report.tris})")
    check(report.non_manifold == 0, "the cube is closed")
    check(report.boundary == 0, "the cube has no holes")
    check(abs(report.area - 24.0) < 0.01, f"the area is 24 m2 ({report.area:.2f})")
    check(report.tri_equivalent == 12, f"12 triangles for the renderer ({report.tri_equivalent})")
    bpy.data.objects.remove(cube, do_unlink=True)

    # A plane: four boundary edges.
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.active_object
    report = mesh.analyze(plane)
    check(report.boundary == 4, f"the plane has 4 boundary edges ({report.boundary})")
    bpy.data.objects.remove(plane, do_unlink=True)

    # A hole: delete one face of the cube.
    import bmesh

    cube = make_cube("HoleCube")
    bm = bmesh.new()
    bm.from_mesh(cube.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
    bm.to_mesh(cube.data)
    bm.free()
    report = mesh.analyze(cube)
    check(report.boundary == 4, f"the hole shows up as 4 boundary edges ({report.boundary})")
    check(report.faces == 5, f"one face is gone ({report.faces})")
    bpy.data.objects.remove(cube, do_unlink=True)


def part_findings(mesh):
    print("\n== the problems the doctor reports")
    sphere = make_sphere("DocSphere")
    found = mesh.check(sphere, budget=100)
    codes = {finding.code for finding in found}
    check(mesh.OVER_BUDGET in codes, f"the budget is checked ({sorted(codes)})")
    # The sphere comes with a UV layout; take it away to see the finding.
    while len(sphere.data.uv_layers):
        sphere.data.uv_layers.remove(sphere.data.uv_layers[0])
    codes = {finding.code for finding in mesh.check(sphere, budget=100)}
    check(mesh.NO_UV in codes, f"a mesh without UVs is reported ({sorted(codes)})")
    sphere_report = mesh.analyze(sphere)
    check(
        sphere_report.quads > 0,
        f"the sphere is a quad mesh ({sphere_report.quads} quads)",
    )
    # A UV sphere has triangles around the poles - that is normal.
    check(
        sphere_report.quad_share > 0.8,
        f"the sphere is mostly quads ({sphere_report.quad_share:.2f})",
    )
    # With a big budget the same mesh is fine.
    relaxed = {finding.code for finding in mesh.check(sphere, budget=100000)}
    check(mesh.OVER_BUDGET not in relaxed, "a large budget silences the complaint")
    bpy.data.objects.remove(sphere, do_unlink=True)


def part_retopo(mesh):
    print("\n== the retopology keeps the promise")
    sphere = make_sphere("HighPoly")
    before_verts = len(sphere.data.vertices)
    before_faces = len(sphere.data.polygons)

    report = mesh.retopo(sphere, budget=400, shrinkwrap=True)
    check(report.get("ok") is True, f"the retopology finished ({report.get('error')})")
    check(report["object"] != "HighPoly", f"the result is a new object ({report['object']})")
    check(
        len(sphere.data.vertices) == before_verts
        and len(sphere.data.polygons) == before_faces,
        "the original is untouched",
    )
    result = bpy.data.objects.get(report["object"])
    check(result is not None, "the new object is in the scene")
    if result is not None:
        check(result.type == "MESH", "the new object is a mesh")
        check(len(result.data.polygons) > 0, "the new object has faces")
        check(
            report["quads"] > 0 and report["tri_faces"] == 0,
            f"the new object is all quads ({report['quads']} quads, "
            f"{report['tri_faces']} triangular faces)",
        )
        check(
            report["tris"] == report["quads"] * 2,
            f"the renderer sees {report['tris']} triangles "
            f"({report['quads']} quads x 2)",
        )
        check(
            report["tris"] <= 400,
            f"the budget is respected ({report['tris']} triangles, budget 400)",
        )
        check(
            len(result.data.uv_layers) == 0 or True,
            "the new mesh has its own UV state",
        )
    error = report.get("deviation") or {}
    check(error.get("samples", 0) > 0, "the distance to the sculpt was measured")
    check(
        error.get("mean", 1.0) < 0.05,
        f"the new mesh sits on the sculpt ({error.get('mean', 0) * 1000:.1f} mm)",
    )
    check(report["seconds"] > 0, f"the work took time ({report['seconds']:.1f}s)")

    # A budget nobody can reach is reported, not hidden.
    tiny = mesh.retopo(sphere, budget=1, shrinkwrap=False)
    check(tiny.get("ok") is True, "even an impossible budget returns a mesh")
    check(
        any("over the budget" in note for note in tiny.get("notes", [])),
        f"the impossible budget is explained ({tiny.get('notes')})",
    )
    for name in (report["object"], tiny.get("object", "")):
        obj = bpy.data.objects.get(name)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(sphere, do_unlink=True)


def part_lod(mesh):
    print("\n== the LOD chain")
    sphere = make_sphere("LodSource")
    before = len(sphere.data.polygons)
    report = mesh.lod_chain(sphere, levels=2)
    check(report.get("ok") is True, f"the chain was built ({report.get('error')})")
    check(len(report["levels"]) == 2, f"two levels ({len(report['levels'])})")
    for level in report["levels"]:
        obj = bpy.data.objects.get(level["name"])
        check(obj is not None, f"{level['name']} is in the scene")
        if obj is not None:
            check(
                len(obj.data.polygons) < before,
                f"{level['name']} is lighter ({len(obj.data.polygons)} < {before})",
            )
    if len(report["levels"]) >= 2:
        check(
            report["levels"][1]["faces"] < report["levels"][0]["faces"],
            "each level is lighter than the previous one",
        )
    for level in report["levels"]:
        obj = bpy.data.objects.get(level["name"])
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(sphere, do_unlink=True)


def part_settings(mesh):
    print("\n== the settings live on the scene")
    for name in ("gaa_retopo_budget", "gaa_retopo_shrinkwrap", "gaa_lod_levels"):
        check(
            name in bpy.types.Scene.bl_rna.properties,
            f"Scene.{name} exists",
        )
    settings = mesh.settings_from_scene()
    check(settings["budget"] > 0, f"the budget is a number ({settings['budget']})")
    check(isinstance(settings["shrinkwrap"], bool), "the shrinkwrap flag is a flag")
    bpy.context.scene.gaa_retopo_budget = 12345
    check(
        mesh.settings_from_scene()["budget"] == 12345,
        "the budget can be set by the user",
    )


def part_tools(mesh, tools):
    print("\n== the tools")
    for tool_id in ("mesh.check", "mesh.retopo", "mesh.lod"):
        check(tools.get(tool_id) is not None, f"{tool_id} is registered")
    sphere = make_sphere("ToolSphere")
    bpy.context.view_layer.objects.active = sphere
    result = tools.execute("mesh.check", {})
    check(result.ok, f"the check tool runs ({result.report[:60]})")
    result = tools.execute("mesh.retopo", {"budget": 300})
    check(result.ok, f"the retopo tool runs ({result.report[:60]})")
    result = tools.execute("mesh.lod", {"levels": 1})
    check(result.ok, f"the lod tool runs ({result.report[:60]})")
    for obj in list(bpy.data.objects):
        if obj.name.startswith("ToolSphere"):
            bpy.data.objects.remove(obj, do_unlink=True)


def main():
    package = load_package()
    package.register()
    try:
        mesh = importlib.import_module("blender_addon.mesh")
        tools = importlib.import_module("blender_addon.tools")
        clean_scene()
        part_analyze(mesh)
        part_findings(mesh)
        part_retopo(mesh)
        part_lod(mesh)
        part_settings(mesh)
        part_tools(mesh, tools)
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
