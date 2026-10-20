"""Blender-side checks for the scene snapshot.

    blender --background --factory-startup --python tests/run_snapshot_check.py

The snapshot is the add-on's only channel for telling the model what the
scene looks like. This script builds a deliberately broken scene and checks
that the snapshot says everything the model cannot see for itself:

* the material the user is editing, with its nodes and links;
* that the object has no UV;
* that a texture file is missing;
* that a data map is in the wrong colour space (through the node detail);
* that the focus follows the editor the user is in.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib.util
import sys
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


def load_snapshot():
    """Import snapshot.py from the working tree, without the add-on package.

    ``snapshot.py`` does ``from . import i18n``, so the directory is registered
    as a package first. The package ``__init__`` is *not* executed — that
    would touch the add-on registration, which this script does not need.
    """
    import types

    package = types.ModuleType("blender_addon")
    package.__path__ = [str(ADDON_DIR)]
    sys.modules["blender_addon"] = package

    spec = importlib.util.spec_from_file_location(
        "blender_addon.snapshot", ADDON_DIR / "snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["blender_addon.snapshot"] = module
    spec.loader.exec_module(module)
    return module


# -- a stub UI context ------------------------------------------------------


class FakeSpace:
    def __init__(self, space_type="NODE_EDITOR", node_tree=None, image=None):
        self.type = space_type
        self.node_tree = node_tree
        self.image = image


class FakeContext:
    def __init__(self, space=None, active_object=None):
        self.space_data = space
        self.active_object = active_object
        self.object = active_object


# -- the scene --------------------------------------------------------------


def build_scene():
    """A cube with a textured material and three deliberate problems."""
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.name = "AuditCube"
    # The default cube comes with a UV map; remove it so the "textured
    # material on a mesh without UV" problem is real.
    for layer in list(obj.data.uv_layers):
        obj.data.uv_layers.remove(layer)

    mat = bpy.data.materials.new("AuditMat")
    mat.use_nodes = True
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links

    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")

    # A colour map (sRGB is right) and a data map (sRGB is wrong).
    colour = bpy.data.images.new("Audit_Albedo", width=64, height=64)
    rough = bpy.data.images.new("Audit_Rough", width=64, height=64)
    rough.colorspace_settings.name = "sRGB"  # deliberate mistake

    tex_colour = nodes.new("ShaderNodeTexImage")
    tex_colour.image = colour
    tex_colour.name = "Albedo_Tex"
    tex_rough = nodes.new("ShaderNodeTexImage")
    tex_rough.image = rough
    tex_rough.name = "Rough_Tex"

    normal = nodes.new("ShaderNodeNormalMap")
    normal.name = "Normal_Map"

    links.new(tex_colour.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(tex_rough.outputs["Color"], bsdf.inputs["Roughness"])
    links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    obj.data.materials.append(mat)

    # A texture whose file is gone. Blender refuses to load a missing file,
    # so the datablock is built by hand: a file-backed image pointing at a
    # path that does not exist.
    missing = bpy.data.images.new("Audit_Missing", width=8, height=8)
    missing.source = "FILE"
    missing.filepath = str(Path(ROOT) / "does_not_exist_diffuse.png")
    tex_missing = nodes.new("ShaderNodeTexImage")
    tex_missing.image = missing

    # A texture whose file is fine - it must NOT be reported as missing.
    good = bpy.data.images.new("Audit_Good", width=8, height=8)
    good.source = "FILE"
    good.filepath = str(Path(ROOT) / "tests" / "run_snapshot_check.py")
    tex_good = nodes.new("ShaderNodeTexImage")
    tex_good.image = good

    return obj, mat


# -- checks -----------------------------------------------------------------


def part_focus(snapshot, obj, mat):
    print("\n== focus detection")
    check(snapshot.detect_focus(None) == snapshot.FOCUS_SCENE, "no context -> scene focus")

    node_ctx = FakeContext(
        space=FakeSpace("NODE_EDITOR", node_tree=mat.node_tree), active_object=obj
    )
    check(
        snapshot.detect_focus(node_ctx) == snapshot.FOCUS_MATERIAL,
        "shader editor -> material focus",
    )
    check(
        snapshot.focus_material(node_ctx) == mat,
        "the material being edited is the focus",
    )

    image_ctx = FakeContext(
        space=FakeSpace("IMAGE_EDITOR", image=bpy.data.images["Audit_Albedo"]),
        active_object=obj,
    )
    check(
        snapshot.detect_focus(image_ctx) == snapshot.FOCUS_TEXTURES,
        "image editor -> textures focus",
    )
    check(
        snapshot.focus_material(image_ctx) == mat,
        "the active object's material is the focus outside the shader editor",
    )


def part_detail(snapshot, obj, mat):
    print("\n== detailed material block")
    ctx = FakeContext(
        space=FakeSpace("NODE_EDITOR", node_tree=mat.node_tree), active_object=obj
    )
    text = snapshot.build_snapshot(ctx)

    check("Material in focus: AuditMat" in text, "the focused material is named")
    check("Nodes (" in text, "the node count is reported")
    check("Principled BSDF" in text, "the Principled node is described")
    check("Links (" in text, "the connections are reported")
    check(
        "Rough_Tex.Color -> Principled BSDF.Roughness" in text,
        "a link names both ends",
    )
    check("Audit_Albedo" in text, "the texture list names the images")
    check("Used by: AuditCube" in text, "the users of the material are listed")
    check("Roughness=0.5" in text, "the Principled values are reported")

    print("\n== problems")
    check("Problems:" in text, "a problems section appears")
    check("not found" in text, "the missing texture file is reported")
    check(
        not any(
            "Audit_Good" in line and "not found" in line
            for line in text.split("Problems:")[1].splitlines()
        ),
        "a texture whose file exists is not reported as missing",
    )
    check(
        "no UV layout" in text,
        "the object without UV is reported (the cube has no unwrap)",
    )
    check(
        'Audit_Rough" in material "AuditMat" feeds "Roughness"' in text,
        "the data map in the wrong colour space is reported",
    )
    check(
        'Audit_Albedo" in material "AuditMat" feeds "Roughness"' not in text,
        "a colour map is not reported as a colour space problem",
    )

    print("\n== shader editor context")
    check("Shader editor:" in text, "the shader editor state is reported")
    check('tree "' in text, "the edited tree is named")

    print("\n== image editor context")
    image_ctx = FakeContext(
        space=FakeSpace("IMAGE_EDITOR", image=bpy.data.images["Audit_Albedo"]),
        active_object=obj,
    )
    text2 = snapshot.build_snapshot(image_ctx)
    check("Image editor:" in text2, "the image editor state is reported")
    check("Audit_Albedo" in text2, "the shown image is named")


def part_scene_focus(snapshot, obj, mat):
    print("\n== scene focus")
    text = snapshot.build_snapshot(None)
    count = len(bpy.context.scene.objects)
    check(f"Objects: {count}" in text, "objects are counted")
    check("uv=none" in text, "the missing UV is stated in the object line")
    check("Materials: " in text, "materials are counted")
    check("Images: " in text, "images are counted")
    check("AuditMat" in text, "the material shows up in the summary")


def part_budget(snapshot, obj, mat):
    print("\n== budget")
    ctx = FakeContext(
        space=FakeSpace("NODE_EDITOR", node_tree=mat.node_tree), active_object=obj
    )
    text = snapshot.build_snapshot(ctx)
    print(f"   snapshot size: {len(text)} chars")
    check(len(text) < 12000, "the snapshot stays inside a sane budget")


# -- runner -----------------------------------------------------------------


def main():
    snapshot = load_snapshot()
    obj, mat = build_scene()
    part_focus(snapshot, obj, mat)
    part_detail(snapshot, obj, mat)
    part_scene_focus(snapshot, obj, mat)
    part_budget(snapshot, obj, mat)
    print()
    if FAILURES:
        print(f"RESULT: FAILED ({len(FAILURES)})")
        for line in FAILURES:
            print(f"  - {line}")
        sys.exit(1)
    print("RESULT: OK")


main()
