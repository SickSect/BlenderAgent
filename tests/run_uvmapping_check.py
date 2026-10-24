"""Blender-side checks for the texture-mapping -> UV conversion.

    blender --background --factory-startup --python tests/run_uvmapping_check.py

A Mapping node is evaluated by Blender but not by the engines. The conversion
moves the same affine chain into one UV layer per object. These checks pin the
maths against the formula Blender uses, the per-face result, the refusals, the
shared-material rule and the restore that keeps the .blend untouched.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import types
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "src" / "blender_addon"

FAILURES = []
TOLERANCE = 1e-4


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


def mapped_matrix(vector_type, loc, rot, scale):
    point = (
        Matrix.Translation(Vector(loc))
        @ Euler(Vector(rot), "XYZ").to_matrix().to_4x4()
        @ Matrix.Diagonal((*scale, 1.0))
    )
    if vector_type == "TEXTURE":
        return point.inverted()
    return point


PARAMS = {"loc": (0.1, 0.2, 0.0), "rot": (0.5, 0.3, 0.2), "scale": (3.0, 2.0, 1.0)}


def image(name="Tex"):
    return bpy.data.images.new(name, width=64, height=64)


def principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def tiled_material(name, tex_image, coord="Object", vector_type="TEXTURE", params=PARAMS):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = tex_image
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.vector_type = vector_type
    mapping.inputs["Location"].default_value = params["loc"]
    mapping.inputs["Rotation"].default_value = params["rot"]
    mapping.inputs["Scale"].default_value = params["scale"]
    coords = tree.nodes.new("ShaderNodeTexCoord")
    tree.links.new(coords.outputs[coord], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    tree.links.new(tex.outputs["Color"], principled(mat).inputs["Base Color"])
    return mat


def plain_material(name, tex_image):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = tex_image
    tree.links.new(tex.outputs["Color"], principled(mat).inputs["Base Color"])
    return mat


def two_material_cube(name, tiled, plain):
    bpy.ops.mesh.primitive_cube_add(size=2, location=(2.0, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(tiled)
    obj.data.materials.append(plain)
    # Faces 0..2 use the tiled material, faces 3..5 the plain one.
    for polygon in obj.data.polygons:
        polygon.material_index = 0 if polygon.index < 3 else 1
    return obj


def layer_names(obj):
    return [layer.name for layer in obj.data.uv_layers]


def image_vector_source(mat):
    tex = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
    links = tex.inputs["Vector"].links
    return links[0].from_node.type if links else None


def part_matrix(uvmapping):
    print("\n== the Mapping matrices match Blender's maths")
    tree_mat = bpy.data.materials.new("MatrixProbe")
    tree_mat.use_nodes = True
    tree = tree_mat.node_tree
    for vector_type in ("POINT", "TEXTURE", "VECTOR"):
        node = tree.nodes.new("ShaderNodeMapping")
        node.vector_type = vector_type
        if "Location" in node.inputs:
            node.inputs["Location"].default_value = PARAMS["loc"]
        node.inputs["Rotation"].default_value = PARAMS["rot"]
        node.inputs["Scale"].default_value = PARAMS["scale"]
        got = uvmapping._matrix_for(node)
        if vector_type == "VECTOR":
            want = mapped_matrix("POINT", (0, 0, 0), PARAMS["rot"], PARAMS["scale"])
        else:
            want = mapped_matrix(vector_type, PARAMS["loc"], PARAMS["rot"], PARAMS["scale"])
        same = got is not None and all(
            abs(got[r][c] - want[r][c]) < TOLERANCE for r in range(4) for c in range(4)
        )
        check(same, f"{vector_type} matrix matches the reference")

    normal = tree.nodes.new("ShaderNodeMapping")
    normal.vector_type = "NORMAL"
    check(uvmapping._matrix_for(normal) is None, "NORMAL mapping is refused")

    linked = tree.nodes.new("ShaderNodeMapping")
    source = tree.nodes.new("ShaderNodeValue")
    tree.links.new(source.outputs[0], linked.inputs["Scale"])
    check(uvmapping._matrix_for(linked) is None, "a Mapping with a linked input is refused")

    flat = tree.nodes.new("ShaderNodeMapping")
    flat.inputs["Scale"].default_value = (0.0, 1.0, 1.0)
    check(uvmapping._matrix_for(flat) is None, "a zero scale is refused")
    bpy.data.materials.remove(tree_mat)


def part_convert(uvmapping):
    print("\n== one combined layer, values checked per face")
    tex = image("Tex")
    tiled = tiled_material("Tiled", tex)
    plain = plain_material("Plain", tex)
    obj = two_material_cube("Walls", tiled, plain)
    original = [tuple(d.uv) for d in obj.data.uv_layers[0].data]
    before_names = layer_names(obj)

    session = uvmapping.convert([obj], prefix="EXPORT")
    report = session.report
    check(not report.skipped, f"nothing skipped ({report.skipped})")
    check(set(report.converted) == {"Tiled -> GAA_EXPORT", "Plain -> GAA_EXPORT"},
          f"both materials point at the layer ({report.converted})")
    check(layer_names(obj) == ["GAA_EXPORT"],
          f"only the prepared layer remains during export ({layer_names(obj)})")
    check(obj.data.uv_layers.active.name == "GAA_EXPORT", "the prepared layer is active")
    check(image_vector_source(tiled) == "UVMAP", "tiled image reads the layer")
    check(image_vector_source(plain) == "UVMAP", "plain image reads the layer")

    # Faces of the tiled material: the object-space chain, evaluated per loop.
    expected = mapped_matrix("TEXTURE", PARAMS["loc"], PARAMS["rot"], PARAMS["scale"])
    layer = obj.data.uv_layers["GAA_EXPORT"]
    worst_tiled = 0.0
    worst_plain = 0.0
    for polygon in obj.data.polygons:
        for loop_index in polygon.loop_indices:
            loop = obj.data.loops[loop_index]
            got = Vector((layer.data[loop_index].uv[0], layer.data[loop_index].uv[1]))
            if polygon.material_index == 0:
                co = obj.data.vertices[loop.vertex_index].co
                want = expected @ co
                worst_tiled = max(worst_tiled, (got - Vector((want.x, want.y))).length)
            else:
                worst_plain = max(
                    worst_plain,
                    (got - Vector(original[loop_index])).length,
                )
    check(worst_tiled < TOLERANCE, f"tiled faces follow the mapping (error {worst_tiled:.2e})")
    check(worst_plain < TOLERANCE, f"plain faces keep their UVs (error {worst_plain:.2e})")

    session.restore()
    check(layer_names(obj) == before_names, f"the UV layers come back ({layer_names(obj)})")
    check([tuple(d.uv) for d in obj.data.uv_layers[0].data] == original,
          "the original UV values come back")
    check(image_vector_source(tiled) == "MAPPING", "the tiled material is wired as before")
    plain_tex = next(n for n in plain.node_tree.nodes if n.type == "TEX_IMAGE")
    check(not plain_tex.inputs["Vector"].links, "the plain image is unlinked again")
    check(
        not any(n.type == "UVMAP" for n in tiled.node_tree.nodes)
        and not any(n.type == "UVMAP" for n in plain.node_tree.nodes),
        "restore removes the UV Map nodes",
    )
    bpy.data.objects.remove(obj, do_unlink=True)


def part_refusals(uvmapping):
    print("\n== what cannot become UVs is refused, not guessed")
    tex = image("Refuse")
    normal_mat = tiled_material("NormalCoords", tex, coord="Normal")
    obj = two_material_cube("NormalWall", normal_mat, plain_material("P2", tex))
    session = uvmapping.convert([obj], prefix="EXPORT")
    check(any("not affine" in item for item in session.report.skipped),
          f"normal coordinates are reported ({session.report.skipped})")
    check(not session.report.converted, "nothing is changed for them")
    check(layer_names(obj) == ["UVMap"], "no layer was added")
    session.restore()
    bpy.data.objects.remove(obj, do_unlink=True)

    mixed = bpy.data.materials.new("Mixed")
    mixed.use_nodes = True
    tree = mixed.node_tree
    tex_a = tree.nodes.new("ShaderNodeTexImage")
    tex_a.image = tex
    tex_b = tree.nodes.new("ShaderNodeTexImage")
    tex_b.image = tex
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.vector_type = "TEXTURE"
    mapping.inputs["Scale"].default_value = (2.0, 2.0, 1.0)
    coords = tree.nodes.new("ShaderNodeTexCoord")
    tree.links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], tex_a.inputs["Vector"])
    obj = two_material_cube("MixedWall", mixed, plain_material("P3", tex))
    session = uvmapping.convert([obj], prefix="EXPORT")
    check(any("different coordinate chains" in item for item in session.report.skipped),
          f"two chains in one material are refused ({session.report.skipped})")
    check(layer_names(obj) == ["UVMap"], "a mixed material adds no layer")
    session.restore()
    bpy.data.objects.remove(obj, do_unlink=True)


def part_shared(uvmapping):
    print("\n== a material shared with a refused one stays on its own UVs")
    tex = image("Shared")
    good = tiled_material("GoodTile", tex)
    bad = tiled_material("BadTile", tex, coord="Normal")
    first = two_material_cube("FirstWall", good, plain_material("P4", tex))
    second = two_material_cube("SecondWall", bad, good)
    second.location = (-4.0, 0.0, 0.0)
    session = uvmapping.convert([first], prefix="EXPORT")
    check(layer_names(first) == ["UVMap"],
          f"the first object is left out too ({layer_names(first)})")
    check(image_vector_source(good) == "MAPPING",
          "the shared material keeps its mapping")
    session.restore()
    bpy.data.objects.remove(first, do_unlink=True)
    bpy.data.objects.remove(second, do_unlink=True)


def part_export(export, uvmapping):
    print("\n== export writes the prepared UVs and restores the scene")
    tex = image("ExportTex")
    tiled = tiled_material("ExportTiled", tex)
    obj = two_material_cube("ExportWall", tiled, plain_material("ExportPlain", tex))
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    folder = tempfile.mkdtemp(prefix="gaa_mapping_")
    result = export.export(folder, "fbx", objects=[obj], strict=False)
    check(result.get("ok"), f"the FBX export finished ({result.get('error')})")
    check(any("ExportTiled" in item for item in result.get("prepared", [])),
          f"the export reports the prepared material ({result.get('prepared')})")
    check(layer_names(obj) == ["UVMap"], f"the layers are restored ({layer_names(obj)})")
    check(image_vector_source(tiled) == "MAPPING", "the mapping is wired back")
    text = export._report(result)
    check("Moved texture mapping into UVs" in text, "the report says what was moved")
    bpy.data.objects.remove(obj, do_unlink=True)


def main():
    package = load_package()
    package.register()
    try:
        uvmapping = importlib.import_module("blender_addon.uvmapping")
        export = importlib.import_module("blender_addon.export")
        clean_scene()
        part_matrix(uvmapping)
        part_convert(uvmapping)
        part_refusals(uvmapping)
        part_shared(uvmapping)
        part_export(export, uvmapping)
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
