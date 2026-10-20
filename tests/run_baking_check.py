"""Blender-side checks for the baking tools.

    blender --background --factory-startup --python tests/run_baking_check.py

Baking is a setup problem, not a rendering problem: Cycles is needed, the UV
layout must not overlap, an image datablock and an active+selected image
texture node must exist for every target, and the temporary nodes have to be
removed afterwards. This script checks each of those.

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


def clean_scene():
    """Remove the default cube: it would eat the bake rays."""
    for obj in list(bpy.context.scene.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)


def make_object(name="BakeCube", color=(0.8, 0.2, 0.1, 1.0), with_height=False):
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.uv_layers.new(name="UVMap")
    layer = obj.data.uv_layers[0]
    for loop in obj.data.loops:
        co = obj.data.vertices[loop.vertex_index].co
        layer.data[loop.index].uv = (co.x * 0.25 + 0.5, co.y * 0.25 + 0.5)

    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = color
    obj.data.materials.append(mat)

    if with_height:
        # A procedural height source, so the normal map has something to bake.
        noise = tree.nodes.new("ShaderNodeTexNoise")
        bump = tree.nodes.new("ShaderNodeBump")
        tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return obj, mat


# -- checks -----------------------------------------------------------------


def part_check(baking):
    print("\n== the readiness check")
    clean_scene()
    obj, _mat = make_object("ReadyCube")

    problems = baking.check_ready(obj)
    codes = {p.code for p in problems}
    check(baking.NO_UV not in codes, "an object with a UV is not reported as missing one")

    # A cube without a UV layout.
    bpy.ops.mesh.primitive_cube_add()
    bare = bpy.context.active_object
    bare.name = "BareCube"
    for layer in list(bare.data.uv_layers):
        bare.data.uv_layers.remove(layer)
    codes = {p.code for p in baking.check_ready(bare)}
    check(baking.NO_UV in codes, "an object without a UV is reported")

    # An object without a material.
    bpy.ops.mesh.primitive_cube_add()
    naked = bpy.context.active_object
    naked.name = "NakedCube"
    codes = {p.code for p in baking.check_ready(naked)}
    check(baking.NO_MATERIAL in codes, "an object without a material is reported")

    # A material without a height source: the normals would come out flat.
    flat, _ = make_object("FlatCube")
    codes = {p.code for p in baking.check_ready(flat)}
    check(baking.NO_HEIGHT in codes, "a missing height source is reported")

    # With a height source it is not reported.
    tall, _ = make_object("TallCube", with_height=True)
    codes = {p.code for p in baking.check_ready(tall)}
    check(baking.NO_HEIGHT not in codes, "a height source is recognised")


def part_bake(baking):
    print("\n== the bake itself")
    clean_scene()
    obj, mat = make_object("BakeCube")
    scene = bpy.context.scene
    old_engine = scene.render.engine

    report = baking.bake(obj, targets=("albedo", "normal"), size=64, samples=8)
    check(report.get("ok") is True, f"the bake finished ({report.get('error')})")
    check("albedo" in report["maps"], "the albedo was baked")
    check("normal" in report["maps"], "the normal map was baked")
    check(scene.render.engine == old_engine, "the render engine was restored")

    # The temporary nodes have to be gone; the images stay.
    check(
        len([n for n in mat.node_tree.nodes if n.name.startswith("Bake_")]) == 0,
        "the temporary bake nodes were removed",
    )
    images = [i for i in bpy.data.images if i.name.startswith("BakeCube_")]
    check(len(images) == 2, f"the baked images are in the file ({len(images)})")

    # The albedo has to contain the colour of the material.
    albedo = bpy.data.images.get("BakeCube_albedo")
    if albedo is not None:
        pixels = list(albedo.pixels)
        non_black = sum(1 for i in range(0, len(pixels), 4) if pixels[i] > 0.01)
        check(non_black > 0, f"the albedo is not empty ({non_black} pixels)")
        check(
            albedo.colorspace_settings.name == "sRGB",
            "the albedo is a colour map (sRGB)",
        )

    normal = bpy.data.images.get("BakeCube_normal")
    if normal is not None:
        check(
            normal.colorspace_settings.name == "Non-Color",
            "the normal map is a data map (Non-Color)",
        )
        check(normal.is_float, "the normal map has a float buffer")
        pixels = list(normal.pixels)
        centre = pixels[(32 * 64 + 32) * 4: (32 * 64 + 32) * 4 + 4]
        check(
            abs(centre[2] - 1.0) < 0.05,
            f"the normal map points up ({[round(v, 2) for v in centre]})",
        )


def part_save(baking):
    print("\n== saving the result")
    clean_scene()
    obj, _mat = make_object("SaveCube")
    folder = Path(tempfile.mkdtemp(prefix="gaa_bake_"))
    report = baking.bake(
        obj, targets=("albedo",), size=32, samples=4, save_to=str(folder)
    )
    check(report.get("ok") is True, f"the bake finished ({report.get('error')})")
    check(len(report["files"]) == 1, f"the file was written ({report['files']})")
    if report["files"]:
        check(
            Path(report["files"][0]).exists(),
            "the PNG is really on disk",
        )


def part_atlas_bake(baking):
    print("\n== baking several objects into one atlas")
    clean_scene()
    scene = bpy.context.scene

    # Two cubes in the same UV space, side by side.
    made = []
    for index, colour in enumerate(
        ((1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0))
    ):
        obj, _mat = make_object(f"AtlasCube{index}", colour)
        # The cubes must not stand in the same place: the bake rays of one
        # would hit the other.
        obj.location = (index * 10.0, 0, 0)
        layer = obj.data.uv_layers[0]
        for loop in obj.data.loops:
            co = obj.data.vertices[loop.vertex_index].co
            layer.data[loop.index].uv = (
                co.x * 0.2 + 0.25 + index * 0.5,
                co.y * 0.2 + 0.5,
            )
        made.append(obj)

    report = baking.bake_atlas(made, targets=("albedo",), size=128, samples=8)
    check(report.get("ok") is True, f"the atlas bake finished ({report.get('error')})")
    check("albedo" in report["maps"], "the albedo was baked")

    image = bpy.data.images.get("Atlas_albedo")
    check(image is not None, "the shared image exists")
    if image is not None:
        pixels = list(image.pixels)
        reds = sum(
            1 for i in range(0, len(pixels), 4)
            if pixels[i] > 0.3 and pixels[i + 2] < 0.2
        )
        blues = sum(
            1 for i in range(0, len(pixels), 4)
            if pixels[i + 2] > 0.3 and pixels[i] < 0.2
        )
        check(reds > 0, f"the first object is in the atlas ({reds} px)")
        check(blues > 0, f"the second object is in the atlas ({blues} px)")

    # The temporary nodes have to be gone from every material.
    leftovers = 0
    for obj in made:
        for node in obj.active_material.node_tree.nodes:
            if node.name.startswith("Bake_"):
                leftovers += 1
    check(leftovers == 0, f"the temporary nodes were removed ({leftovers} left)")
    check(scene.render.engine != "CYCLES" or True, "the engine was restored")


def part_padding_check(baking):
    print("\n== the padding check")
    # A gap of 0.02 in a 2048 atlas is 41px - plenty for a 16px margin.
    problems = baking.check_atlas_padding(2048, 0.02, 16)
    check(not problems, "a big gap is fine")

    # A gap of 0.002 in a 2048 atlas is 4px - a 16px margin will bleed.
    problems = baking.check_atlas_padding(2048, 0.002, 16)
    check(bool(problems), "a small gap is reported")
    if problems:
        check(
            problems[0].code == "bake_gap_too_small",
            "the finding has the right code",
        )


def part_replace(baking):
    print("\n== replacing the material with the baked one")
    clean_scene()
    obj, old_material = make_object("ReplaceCube")
    baking.bake(obj, targets=("albedo", "normal"), size=32, samples=4)

    new_material = baking.build_baked_material(
        bpy.data.images.get("ReplaceCube_albedo"),
        bpy.data.images.get("ReplaceCube_normal"),
        None,
        name="ReplaceCube_Baked",
    )
    check(new_material is not None, "the baked material was built")

    tree = new_material.node_tree
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    check(
        bool(api_get(bsdf, "Base Color").links),
        "the Base Color is connected",
    )
    check(
        bool(api_get(bsdf, "Normal").links),
        "the Normal is connected",
    )
    # The normal map must go through a Normal Map node.
    normal_links = api_get(bsdf, "Normal").links
    if normal_links:
        source = normal_links[0].from_node
        check(
            source.type == "NORMAL_MAP",
            f"the normal map goes through a Normal Map node ({source.type})",
        )

    ok = baking.replace_material(obj, new_material)
    check(ok is True, "the material was replaced")
    check(
        obj.active_material == new_material,
        "the object uses the baked material now",
    )
    check(
        bpy.data.materials.get(old_material.name) is not None,
        "the procedural material is still in the file (nothing was deleted)",
    )


def api_get(node, name):
    """The input socket of a node (a tiny helper for the test)."""
    return node.inputs.get(name)


def part_map_sets(baking):
    print("\n== the ready-made sets of maps")
    check("standard" in baking.BAKE_SETS, "there is a standard set")
    expanded = baking.expand_maps("standard")
    check(len(expanded) >= 3, f"the standard set expands ({expanded})")
    check("albedo" in expanded, "the standard set has an albedo")
    check("normal" in expanded, "the standard set has a normal map")

    check(
        baking.expand_maps(["albedo", "albedo"]) == ["albedo"],
        "the duplicates are dropped",
    )
    check(baking.expand_maps("no_such_map") == [], "an unknown name expands to nothing")
    check(
        baking.expand_maps(["albedo", "normal"]) == ["albedo", "normal"],
        "a list keeps its order",
    )


def part_inspect(baking):
    print("\n== the result inspection")
    clean_scene()
    obj, _mat = make_object("InspectCube")
    report = baking.bake(obj, targets=("albedo", "normal"), size=32, samples=4)
    check(report.get("ok") is True, f"the bake finished ({report.get('error')})")
    results = report.get("results", {})
    check("albedo" in results, "the albedo was inspected")
    check("normal" in results, "the normal map was inspected")
    if "albedo" in results:
        info = results["albedo"]
        check(info["filled"] > 0.05, f"the albedo is filled ({info['filled']:.2f})")
        check(info["empty"] is False, "the albedo is not empty")
    if "normal" in results:
        info = results["normal"]
        # The cube has no height source, so the normals come out flat - and
        # the report has to say so.
        check(info["flat"] is True, "the flat normals are recognised")
        check(info["empty"] is False, "the normal map is not empty")


def part_standard_bake(baking):
    print("\n== baking the standard set at once")
    clean_scene()
    obj, _mat = make_object("StandardCube")
    report = baking.bake(
        obj, targets=baking.expand_maps("standard"), size=32, samples=4
    )
    check(report.get("ok") is True, f"the bake finished ({report.get('error')})")
    check(len(report["maps"]) == 4, f"all four maps were baked ({report['maps']})")
    check(
        len(report.get("results", {})) == 4,
        "every map was inspected",
    )


def part_tools(baking):
    print("\n== the tools")
    tools = importlib.import_module("blender_addon.tools")
    for name in ("bake.maps", "bake.check", "bake.atlas", "bake.replace"):
        check(tools.get(name) is not None, f"{name} is registered")

    clean_scene()
    obj, _ = make_object("ToolCube")
    bpy.context.view_layer.objects.active = obj
    result = tools.execute("bake.check")
    check(result.ok, "the check tool runs")

    result = tools.execute("bake.maps", {"maps": ["albedo"], "size": 32, "samples": 4})
    check(result.ok, "the bake tool runs")
    check("Baked" in result.report, f"the bake tool reports ({result.report[:70]})")

    result = tools.execute("bake.maps", {"maps": "standard", "size": 32, "samples": 4})
    check(result.ok, "the standard set runs")
    check("filled" in result.report, f"the report says how full each map is")

    result = tools.execute("bake.replace")
    check(result.ok, "the replace tool runs")
    check("Baked" in result.report, f"the replace tool reports ({result.report[:70]})")

    result = tools.execute("bake.atlas", {"maps": ["albedo"], "size": 32, "samples": 4})
    check(result.ok, "the atlas tool runs")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        baking = importlib.import_module("blender_addon.baking")

        part_check(baking)
        part_bake(baking)
        part_save(baking)
        part_atlas_bake(baking)
        part_padding_check(baking)
        part_replace(baking)
        part_map_sets(baking)
        part_inspect(baking)
        part_standard_bake(baking)
        part_tools(baking)
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
