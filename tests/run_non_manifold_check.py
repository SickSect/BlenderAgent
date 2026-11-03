"""Blender-side checks for the non-manifold repair.

    blender --background --factory-startup --python tests/run_non_manifold_check.py

The remesh leaves branching edges behind, and a global weld makes them worse
(measured on a real asset: 165 -> 4 154). These checks pin the safer repair:
internal walls are deleted, duplicate faces are dropped, only the vertices on
a branching edge are welded, and the rest is split. Every step must leave the
mesh no worse; a good mesh must not be touched.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

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
    if not len(obj.data.uv_layers):
        obj.data.uv_layers.new(name="UVMap")
    return obj


def _perp(edge):
    v0, v1 = edge.verts
    direction = (v1.co - v0.co).normalized()
    offset = direction.cross(Vector((0, 0, 1)))
    if offset.length < 0.1:
        offset = direction.cross(Vector((0, 1, 0)))
    return offset.normalized()


def make_fin(name="Fin"):
    """A cube with one extra quad glued to a cube edge: that edge has 3 faces."""
    obj = make_cube(name)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        edge = bm.edges[0]
        v0, v1 = edge.verts
        offset = _perp(edge) * 2.0
        n0 = bm.verts.new(v0.co + offset)
        n1 = bm.verts.new(v1.co + offset)
        bm.faces.new((v0, v1, n1, n0))
        bm.normal_update()
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    return obj


def make_two_cubes(name="TwoCubes"):
    """Two cubes welded along a face, with one internal wall left inside."""
    obj = make_cube(name)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.create_cube(bm, size=2, matrix=Matrix.Translation((2, 0, 0)))
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.0)
        bm.normal_update()
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    return obj


CUBE_VERTS = [
    (-1, -1, -1),
    (1, -1, -1),
    (1, 1, -1),
    (-1, 1, -1),
    (-1, -1, 1),
    (1, -1, 1),
    (1, 1, 1),
    (-1, 1, 1),
]
CUBE_FACES = [
    (0, 1, 2, 3),
    (4, 7, 6, 5),
    (0, 4, 5, 1),
    (1, 5, 6, 2),
    (2, 6, 7, 3),
    (3, 7, 4, 0),
]


def make_duplicate_face(name="DuplicateFace"):
    """A cube with one face stored twice: four edges have three faces."""
    data = bpy.data.meshes.new(name)
    data.from_pydata(CUBE_VERTS, [], CUBE_FACES + [CUBE_FACES[0]])
    data.update()
    if not len(data.uv_layers):
        data.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_zero_face(name="ZeroFace"):
    """A cube with a collapsed quad glued to one edge: zero area, 3 faces."""
    verts = CUBE_VERTS + [(0, -1, -1), (0, -1, -1)]
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], CUBE_FACES + [(0, 1, 9, 8)])
    data.update()
    if not len(data.uv_layers):
        data.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_three_cubes(name="ThreeCubes"):
    obj = make_cube(name)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.create_cube(bm, size=2, matrix=Matrix.Translation((2, 0, 0)))
        bmesh.ops.create_cube(bm, size=2, matrix=Matrix.Translation((0, 2, 0)))
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.0)
        bm.normal_update()
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    return obj


def part_good_mesh(mesh):
    print("\n== a good mesh is not touched")
    cube = make_cube("GoodCube")
    before = mesh.analyze(cube)
    report = mesh.repair_non_manifold(cube.data)
    after = mesh.analyze(cube)
    check(report.get("ok") is True, f"the repair finished ({report.get('error')})")
    check(report.get("changed") is False, "a closed cube is left unchanged")
    check(after.non_manifold == 0, "the cube has no non-manifold edges")
    check(after.faces == before.faces, f"the faces are intact ({after.faces})")
    check(after.area == before.area, "the area is intact")
    bpy.data.objects.remove(cube, do_unlink=True)


def part_fin(mesh):
    print("\n== a fin: a three-face edge is split, not welded")
    fin = make_fin("FinCheck")
    before = mesh.analyze(fin)
    check(before.non_manifold == 1, f"the fin has one branching edge ({before.non_manifold})")
    check(before.boundary == 3, f"the fin itself has three open edges ({before.boundary})")
    uv_names = [layer.name for layer in fin.data.uv_layers]

    report = mesh.repair_non_manifold(fin.data)
    after = mesh.analyze(fin)
    check(report.get("ok") is True, f"the repair finished ({report.get('error')})")
    check(report.get("changed") is True, "the fin was changed")
    check(after.non_manifold == 0, f"the branching edge is gone ({after.non_manifold})")
    check(
        after.boundary > before.boundary,
        f"the branch became an open edge ({before.boundary} -> {after.boundary})",
    )
    check(report.get("split", 0) == 1, f"one branch was split ({report.get('split')})")
    check(report.get("welded", 0) == 0, "the fin was not globally welded")
    check(after.faces == before.faces, f"the fin face is still there ({after.faces})")
    check(abs(after.area - before.area) < 1e-6, "the area did not change")
    check(
        [layer.name for layer in fin.data.uv_layers] == uv_names,
        f"the UV layer survived ({uv_names})",
    )
    bpy.data.objects.remove(fin, do_unlink=True)


def part_internal_wall(mesh):
    print("\n== an internal wall is deleted, not split")
    two = make_two_cubes("TwoCubesCheck")
    before = mesh.analyze(two)
    check(before.non_manifold == 4, f"two cubes have four branching edges ({before.non_manifold})")
    check(abs(before.area - 44.0) < 0.01, f"the area is 44 m2 ({before.area:.2f})")

    report = mesh.repair_non_manifold(two.data)
    after = mesh.analyze(two)
    check(report.get("ok") is True, f"the repair finished ({report.get('error')})")
    check(report.get("internal_faces", 0) == 1, f"one internal face was removed ({report.get('internal_faces')})")
    check(after.non_manifold == 0, f"the branching edges are gone ({after.non_manifold})")
    check(after.boundary == 0, f"no open edges appeared ({after.boundary})")
    check(after.faces == 10, f"ten outer faces are left ({after.faces})")
    check(abs(after.area - 40.0) < 0.01, f"the hidden wall is gone ({after.area:.2f} m2)")
    check(report.get("welded", 0) == 0, "no global weld was used")

    again = mesh.repair_non_manifold(two.data)
    check(again.get("changed") is False, "a second run does nothing")
    bpy.data.objects.remove(two, do_unlink=True)

    three = make_three_cubes("ThreeCubesCheck")
    before = mesh.analyze(three)
    check(before.non_manifold == 7, f"three cubes have seven branching edges ({before.non_manifold})")
    report = mesh.repair_non_manifold(three.data)
    after = mesh.analyze(three)
    check(report.get("internal_faces", 0) == 2, f"two internal faces were removed ({report.get('internal_faces')})")
    check(after.non_manifold == 0, f"the three cubes are clean ({after.non_manifold})")
    check(after.boundary == 0, f"no open edges appeared ({after.boundary})")
    check(after.faces == 14, f"fourteen outer faces are left ({after.faces})")
    check(abs(after.area - 56.0) < 0.01, f"the area is 56 m2 ({after.area:.2f})")
    bpy.data.objects.remove(three, do_unlink=True)


def part_duplicate_face(mesh):
    print("\n== a duplicated face is dropped")
    obj = make_duplicate_face("DuplicateFaceCheck")
    before = mesh.analyze(obj)
    check(before.non_manifold == 4, f"the duplicate makes four branching edges ({before.non_manifold})")
    check(before.faces == 7, f"the duplicate face is stored ({before.faces})")
    check(abs(before.area - 28.0) < 0.01, f"the area counts it ({before.area:.2f})")

    report = mesh.repair_non_manifold(obj.data)
    after = mesh.analyze(obj)
    check(report.get("ok") is True, f"the repair finished ({report.get('error')})")
    check(report.get("duplicate_faces", 0) == 1, f"one duplicate face was removed ({report.get('duplicate_faces')})")
    check(after.non_manifold == 0, f"the branching edges are gone ({after.non_manifold})")
    check(after.boundary == 0, f"the cube is closed again ({after.boundary})")
    check(after.faces == 6, f"six faces are left ({after.faces})")
    check(abs(after.area - 24.0) < 0.01, f"the extra surface is gone ({after.area:.2f})")
    bpy.data.objects.remove(obj, do_unlink=True)


def part_zero_face(mesh):
    print("\n== a zero-area face is dropped")
    obj = make_zero_face("ZeroFaceCheck")
    before = mesh.analyze(obj)
    check(before.non_manifold == 1, f"the collapsed face makes one branching edge ({before.non_manifold})")
    check(before.zero_faces == 1, f"the face has no area ({before.zero_faces})")
    check(abs(before.area - 24.0) < 0.01, f"it adds no area ({before.area:.2f})")

    report = mesh.repair_non_manifold(obj.data)
    after = mesh.analyze(obj)
    check(report.get("ok") is True, f"the repair finished ({report.get('error')})")
    check(report.get("zero_faces", 0) == 1, f"one zero-area face was removed ({report.get('zero_faces')})")
    check(after.non_manifold == 0, f"the branching edge is gone ({after.non_manifold})")
    check(after.boundary == 0, f"the cube is closed again ({after.boundary})")
    check(after.faces == 6, f"six faces are left ({after.faces})")
    check(abs(after.area - 24.0) < 0.01, f"the area is still 24 m2 ({after.area:.2f})")
    bpy.data.objects.remove(obj, do_unlink=True)


def part_tool(mesh, tools):
    print("\n== the tool")
    check(
        tools.get("mesh.repair_non_manifold") is not None,
        "mesh.repair_non_manifold is registered",
    )
    fin = make_fin("ToolFin")
    bpy.context.view_layer.objects.active = fin
    result = tools.execute("mesh.repair_non_manifold", {})
    check(result.ok, f"the tool runs ({result.report[:80]})")
    check("non-manifold" in result.report.lower(), "the report names the problem")
    check(mesh.analyze(fin).non_manifold == 0, "the tool fixed the fin")

    again = tools.execute("mesh.repair_non_manifold", {})
    check(again.ok, f"the tool survives a second run ({again.report[:80]})")
    check(
        "no non-manifold" in again.report.lower(),
        "the second run says there is nothing to do",
    )
    bpy.data.objects.remove(fin, do_unlink=True)


def part_operator(mesh):
    print("\n== the button")
    check(
        hasattr(bpy.ops.gaa, "mesh_repair_non_manifold"),
        "gaa.mesh_repair_non_manifold is registered",
    )
    fin = make_fin("ButtonFin")
    bpy.context.view_layer.objects.active = fin
    result = bpy.ops.gaa.mesh_repair_non_manifold()
    check(result == {"FINISHED"}, f"the button finishes ({result})")
    check(mesh.analyze(fin).non_manifold == 0, "the button fixed the fin")
    bpy.data.objects.remove(fin, do_unlink=True)


def part_settings(mesh):
    print("\n== the setting")
    check(
        "gaa_mesh_fix_non_manifold" in bpy.types.Scene.bl_rna.properties,
        "Scene.gaa_mesh_fix_non_manifold exists",
    )
    settings = mesh.settings_from_scene()
    check(settings["fix_non_manifold"] is True, "the repair is on by default")
    bpy.context.scene.gaa_mesh_fix_non_manifold = False
    check(
        mesh.settings_from_scene()["fix_non_manifold"] is False,
        "the user can turn the repair off",
    )
    bpy.context.scene.gaa_mesh_fix_non_manifold = True


def part_retopo(mesh):
    print("\n== the retopology uses the repair")
    source = make_fin("RetopoFin")
    report = mesh.retopo(source, budget=400, shrinkwrap=False, fix_non_manifold=True)
    check(report.get("ok") is True, f"the retopology finished ({report.get('error')})")
    fix = report.get("non_manifold_fix") or {}
    check(bool(fix), "the report carries the non-manifold repair")
    check(fix.get("ok") is True, f"the repair finished ({fix.get('error')})")
    check(
        report.get("non_manifold", 99) <= report.get("non_manifold_before", 0),
        f"the result is no worse ({report.get('non_manifold_before')} -> "
        f"{report.get('non_manifold')})",
    )

    off = mesh.retopo(source, budget=400, shrinkwrap=False, fix_non_manifold=False)
    check(off.get("ok") is True, f"the retopology works with the repair off ({off.get('error')})")
    check(off.get("non_manifold_fix") == {}, "the repair is skipped when it is off")

    for name in (report.get("object", ""), off.get("object", "")):
        obj = bpy.data.objects.get(name)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(source, do_unlink=True)


def main():
    package = load_package()
    package.register()
    try:
        mesh = importlib.import_module("blender_addon.mesh")
        tools = importlib.import_module("blender_addon.tools")
        clean_scene()
        part_good_mesh(mesh)
        part_fin(mesh)
        part_internal_wall(mesh)
        part_duplicate_face(mesh)
        part_zero_face(mesh)
        part_tool(mesh, tools)
        part_operator(mesh)
        part_settings(mesh)
        part_retopo(mesh)
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
