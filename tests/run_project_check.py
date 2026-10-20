"""Blender-side checks for the project pipeline and the export check.

    blender --background --factory-startup --python tests/run_project_check.py

Two things are checked here:

* the **end-to-end scenario** - a folder of textures in, a ready project out;
* the **export check** - what will break or be lost when the project leaves
  this machine.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
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


# -- a texture folder ------------------------------------------------------


def write_png(path: Path, name: str, size=64):
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = bpy.data.images.new(f"{name}_scratch", width=size, height=size)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = name
    return image


def build_folder():
    """A folder with three maps, named the way artists name them."""
    folder = Path(tempfile.mkdtemp(prefix="gaa_proj_")) / "Wood_2k"
    write_png(folder / "Wood_BaseColor.png", "Wood_BaseColor")
    write_png(folder / "Wood_Roughness.png", "Wood_Roughness")
    write_png(folder / "Wood_Normal.png", "Wood_Normal")
    return folder


# -- checks -----------------------------------------------------------------


def part_pipeline(project, textures, objects, folder):
    print("\n== the end-to-end scenario")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ProjectCube"
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    cube.select_set(True)

    report = project.import_and_prepare(str(folder))
    check(len(report["steps"]) >= 4, f"every step reported ({report['steps']})")
    check(report["material"] == "Wood_2k", "the material is named after the folder")

    material = bpy.data.materials.get("Wood_2k")
    check(material is not None, "the material was created")
    check(
        cube.data.materials and cube.data.materials[0] == material,
        "the material was assigned to the selection",
    )
    packed = [
        image
        for image in textures.file_images()
        if image.packed_file is not None
    ]
    check(len(packed) >= 3, f"the textures were packed ({len(packed)})")
    check(
        any("Assigned" in step for step in report["steps"]),
        "the assignment is in the report",
    )
    check(
        any("Export check" in step for step in report["steps"]),
        "the export check is part of the scenario",
    )


def part_repeat(project, folder):
    print("\n== running it twice")
    before = len(bpy.data.materials)
    report = project.import_and_prepare(str(folder))
    check(
        any("Updated" in step for step in report["steps"]),
        "the second run updates the material instead of duplicating it",
    )
    check(
        bpy.data.materials.get("Wood_2k.001") is None,
        "no duplicate material was left behind",
    )
    check(
        len(bpy.data.materials) == before,
        "the number of materials did not change",
    )


def part_export(export, textures, objects):
    print("\n== the export check")
    findings = export.check_export()
    codes = {finding.code for finding in findings}
    check(export.CUSTOM_SHADER not in codes, "a normal material is not flagged")

    # A shader the exporters cannot map.
    weird = bpy.data.materials.new("WeirdMat")
    weird.use_nodes = True
    tree = weird.node_tree
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    glass = tree.nodes.new("ShaderNodeBsdfGlass")
    tree.links.new(glass.outputs["BSDF"], output.inputs["Surface"])

    findings = export.check_export()
    codes = {finding.code for finding in findings}
    check(export.CUSTOM_SHADER in codes, "a custom shader is flagged")
    check(
        any(finding.material == "WeirdMat" for finding in findings),
        "the finding names the material",
    )

    # Displacement.
    disp = bpy.data.materials.new("DispMat")
    disp.use_nodes = True
    dtree = disp.node_tree
    doutput = next(n for n in dtree.nodes if n.type == "OUTPUT_MATERIAL")
    height = dtree.nodes.new("ShaderNodeTexImage")
    dtree.links.new(height.outputs["Color"], doutput.inputs["Displacement"])
    codes = {f.code for f in export.check_export()}
    check(export.DISPLACEMENT in codes, "a displacement is flagged")

    # A mesh without UV - but only a *textured* mesh matters.
    bpy.ops.mesh.primitive_uv_sphere_add()
    ball = bpy.context.active_object
    for layer in list(ball.data.uv_layers):
        ball.data.uv_layers.remove(layer)
    codes = {f.code for f in export.check_export()}
    check(
        "no_uv" not in codes,
        "a mesh without UV but without textures is not flagged",
    )

    textured = bpy.data.materials.new("TexturedMat")
    textured.use_nodes = True
    ttree = textured.node_tree
    ttex = ttree.nodes.new("ShaderNodeTexImage")
    ttex.image = bpy.data.images.get("Wood_BaseColor")
    ball.data.materials.append(textured)
    codes = {f.code for f in export.check_export()}
    check("no_uv" in codes, "a textured mesh without UV is flagged")

    # A texture that is too large.
    big = write_png(Path(tempfile.gettempdir()) / "BigTexture.png", "BigTexture", 8)
    codes = {f.code for f in export.check_export(max_texture=4)}
    check(
        export.TEXTURE_TOO_LARGE in codes,
        "an oversized texture is flagged against the limit",
    )


def part_tools(tools):
    print("\n== the tools")
    check(tools.get("project.import_textures") is not None, "the pipeline tool is registered")
    check(tools.get("export.check") is not None, "the export check tool is registered")
    check(
        tools.get("project.import_textures").destructive is False,
        "the pipeline is not destructive",
    )


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        project = importlib.import_module("blender_addon.project")
        export = importlib.import_module("blender_addon.export")
        textures = importlib.import_module("blender_addon.textures")
        objects = importlib.import_module("blender_addon.objects")
        tools = importlib.import_module("blender_addon.tools")

        folder = build_folder()
        part_pipeline(project, textures, objects, folder)
        part_repeat(project, folder)
        part_export(export, textures, objects)
        part_tools(tools)
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
