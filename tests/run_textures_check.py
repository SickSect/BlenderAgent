"""Blender-side checks for the texture tools.

    blender --background --factory-startup --python tests/run_textures_check.py

The texture tools make a project travel: they pack the textures into the
.blend, make the paths relative and find lost files again. This script builds
a project with textures in three states (normal, absolute path, lost file) and
checks every tool against them.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
import os
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


# -- a project with textures ------------------------------------------------


def write_png(path: Path, name: str):
    """A real PNG on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = bpy.data.images.new(f"{name}_scratch", width=32, height=32)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = name
    return image


def build_project():
    """A saved .blend with a good texture, an absolute path and a lost one."""
    workspace = Path(tempfile.mkdtemp(prefix="gaa_tex_"))
    textures = workspace / "textures"
    blend = workspace / "project.blend"

    good = write_png(textures / "Good_BaseColor.png", "Good_BaseColor")

    # A texture that lives outside the project.
    outside = Path(tempfile.mkdtemp(prefix="gaa_outside_"))
    absolute = write_png(outside / "Far_Roughness.png", "Far_Roughness")

    # A texture whose file is gone.
    lost = bpy.data.images.new("Lost_Normal", width=32, height=32)
    lost.source = "FILE"
    lost.filepath = str(textures / "Lost_Normal.png")

    # Something has to be in the file for the paths to be relative to.
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))

    # Blender rewrites external paths to relative when it saves ("Relative
    # Paths" is on by default), so an absolute path has to be set afterwards.
    absolute.filepath = str(outside / "Far_Roughness.png")

    return workspace, textures, blend, good, absolute, lost


# -- checks -----------------------------------------------------------------


def part_report(textures, good, absolute, lost):
    print("\n== the report")
    text = textures.report()
    check("Good_BaseColor" in text, "the report lists the textures")
    check("Far_Roughness" in text, "the report lists the absolute one too")
    check("MISSING" in text, "the report marks the lost file")
    check("1024x1024" not in text, "the report does not invent sizes")


def part_colorspace(textures, good):
    print("\n== colour spaces")
    good.colorspace_settings.name = "sRGB"
    fixed = textures.fix_colorspace()
    check(
        good.colorspace_settings.name == "sRGB",
        "a colour map keeps sRGB (the graph decides)",
    )
    check(isinstance(fixed, list), "the fix returns a list")

    # An image that is not in a graph is judged by its file name.
    loose = bpy.data.images.new("Loose_Roughness", width=8, height=8)
    loose.source = "FILE"
    loose.filepath = str(Path(tempfile.gettempdir()) / "Loose_Roughness.png")
    loose.colorspace_settings.name = "sRGB"
    textures.fix_colorspace()
    check(
        loose.colorspace_settings.name == "Non-Color",
        "a data map by file name is set to Non-Color",
    )


def part_conflict(textures):
    print("\n== a conflicting wiring is not 'fixed' by colour space")
    # A normal map wired into Base Color: the wiring is wrong, and turning the
    # image into sRGB would only hide that.
    mat = bpy.data.materials.new("ConflictMat")
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    image = bpy.data.images.new("Conflict_Normal", width=8, height=8)
    image.source = "FILE"
    image.filepath = str(Path(tempfile.gettempdir()) / "Conflict_Normal.png")
    image.colorspace_settings.name = "Non-Color"
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    check(
        textures.expected_colorspace(image) is None,
        "a conflicting image gets no colour space advice",
    )
    check(
        textures.colorspace_conflict(image) == "Base Color",
        "the conflict names the socket",
    )
    textures.fix_colorspace()
    check(
        image.colorspace_settings.name == "Non-Color",
        "the tool leaves the conflicting image alone",
    )


def part_pack(textures, good, absolute, lost):
    print("\n== packing")
    packed = textures.pack_all()
    check("Good_BaseColor" in packed, "the good texture was packed")
    check(good.packed_file is not None, "the image has a packed file")
    again = textures.pack_all()
    check(not again, "packing twice changes nothing")


def part_paths(textures, good, absolute, lost, blend):
    print("\n== paths")
    check(os.path.isabs(absolute.filepath), "the outside texture starts absolute")
    changed = textures.make_relative()
    check(
        any("Far_Roughness" in name for name in changed),
        f"the absolute path was made relative ({changed})",
    )
    check(
        absolute.filepath.startswith("//"),
        f"the path is now relative ({absolute.filepath})",
    )
    again = textures.make_relative()
    check(
        not any("Far_Roughness" in name for name in again),
        "a relative path is left alone",
    )

    absolute_back = textures.make_absolute()
    check(
        any("Far_Roughness" in name for name in absolute_back),
        "and can be made absolute again",
    )
    check(
        os.path.isabs(absolute.filepath),
        "the absolute path is absolute again",
    )


def part_relink(textures, good, absolute, lost, workspace):
    print("\n== relinking a lost file")
    missing = textures.find_missing()
    check(any("Lost_Normal" in name for name in missing), "the lost file is found")

    # Put the file back somewhere else: the image still points at the old,
    # now empty path, so only a search can find it.
    restored = workspace / "found" / "Lost_Normal.png"
    write_png(restored, "Lost_Normal_copy")

    relinked = textures.relink()
    check(
        any("Lost_Normal" in name for name in relinked),
        f"the lost texture was relinked ({relinked})",
    )
    check(
        not any("Lost_Normal" in name for name in textures.find_missing()),
        "nothing is missing any more",
    )


def part_tools(tools, textures):
    print("\n== the tools")
    for name in (
        "textures.pack_all",
        "textures.unpack_all",
        "textures.make_relative",
        "textures.relink",
        "textures.fix_colorspace",
        "textures.report",
    ):
        check(tools.get(name) is not None, f"{name} is registered")
    check(
        tools.get("textures.unpack_all").destructive is True,
        "unpacking is destructive (it writes files)",
    )
    check(
        tools.get("textures.pack_all").destructive is False,
        "packing is not destructive (it can be undone)",
    )
    result = tools.execute("textures.report")
    check(result.ok, "the report tool runs")
    check("Textures" in result.report, f"the report tool answers: {result.report[:60]}")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        textures = importlib.import_module("blender_addon.textures")
        tools = importlib.import_module("blender_addon.tools")

        workspace, tex_dir, blend, good, absolute, lost = build_project()
        part_report(textures, good, absolute, lost)
        part_colorspace(textures, good)
        part_conflict(textures)
        part_pack(textures, good, absolute, lost)
        part_paths(textures, good, absolute, lost, blend)
        part_relink(textures, good, absolute, lost, workspace)
        part_tools(tools, textures)
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
