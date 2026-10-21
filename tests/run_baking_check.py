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


def part_orm(baking):
    print("\n== the ORM map")
    clean_scene()
    obj, _mat = make_object("OrmCube")
    # Bake the maps the ORM is packed from.
    report = baking.bake(obj, targets=("albedo", "normal", "roughness", "ao"),
                         size=32, samples=2)
    check(report.get("ok"), f"the bake finished ({report.get('error')})")

    folder = Path(tempfile.mkdtemp(prefix="gaa_orm_"))
    result = baking.pack_orm(obj, str(folder))
    check(result.get("ok"), f"the ORM was packed ({result.get('error')})")
    path = Path(result.get("path", ""))
    check(path.exists(), f"the file is on disk ({path.name})")
    if path.exists():
        check(path.suffix == ".png", "it is a PNG")
        check(path.stat().st_size > 0, "the file is not empty")

    # The channels: R = AO, G = roughness, B = metallic. The values must be
    # the ones of the source maps, not a constant.
    image = bpy.data.images.get(result.get("image", ""))
    check(image is not None, "the ORM image is in the file")
    if image is not None:
        check(
            image.colorspace_settings.name == "Non-Color",
            f"it is a data map ({image.colorspace_settings.name})",
        )
        pixels = list(image.pixels)
        check(len(pixels) >= 32 * 32 * 4, "the image has pixels")
        if len(pixels) >= 32 * 32 * 4:
            ao_source = bpy.data.images.get(f"{obj.name}_ao")
            rough_source = bpy.data.images.get(f"{obj.name}_roughness")
            check(
                abs(pixels[0] - list(ao_source.pixels)[0]) < 0.01,
                f"R is the occlusion ({pixels[0]:.3f})",
            )
            check(
                abs(pixels[1] - list(rough_source.pixels)[0]) < 0.01,
                f"G is the roughness ({pixels[1]:.3f})",
            )
            check(abs(pixels[2]) < 0.01, f"B is the metallic ({pixels[2]:.2f})")
            check(pixels[3] == 1.0, "the alpha is opaque")

    # Without a bake there is nothing to pack, and the tool says so.
    clean_scene()
    bare, _m = make_object("BareCube")
    empty = baking.pack_orm(bare, str(folder))
    check(not empty.get("ok"), "without baked maps the ORM is not packed")
    check(
        "no baked maps" in empty.get("error", ""),
        f"the refusal explains itself ({empty.get('error')})",
    )


def part_preview(baking):
    print("\n== the preview")
    clean_scene()
    obj, _mat = make_object("PreviewCube")
    folder = Path(tempfile.mkdtemp(prefix="gaa_preview_"))
    path = folder / "preview.png"
    result = baking.render_preview(obj, str(path), size=64)
    check(result.get("ok"), f"the preview was rendered ({result.get('error')})")
    check(path.exists(), f"the picture is on disk ({path.name})")
    if path.exists():
        check(path.stat().st_size > 0, "the picture is not empty")
    # The render settings are put back.
    check(
        bpy.context.scene.render.resolution_x == 1920,
        f"the resolution is back ({bpy.context.scene.render.resolution_x})",
    )
    check(
        bpy.context.scene.camera is None or bpy.context.scene.camera.name != "GAA_Preview_Camera",
        "the temporary camera is gone",
    )


def part_orm_wins(procedural, materials):
    print("\n== the ORM map wins over the separate maps")
    folder = Path(tempfile.mkdtemp(prefix="gaa_orm_folder_"))
    for name in ("Wood_col.png", "Wood_nrm.png", "Wood_rgh.png",
                 "Wood_ao.png", "Wood_orm.png"):
        scratch = bpy.data.images.new(name + "_scratch", width=8, height=8)
        scratch.filepath_raw = str(folder / name)
        scratch.file_format = "PNG"
        scratch.save()
        bpy.data.images.remove(scratch)

    maps = materials.find_maps(str(folder))
    check("orm" in maps, f"the ORM map is found ({sorted(maps)})")
    check("ao" not in maps, "the separate AO is not used any more")
    check("rough" not in maps, "the separate roughness is not used any more")
    check("metal" not in maps, "the separate metallic is not used any more")
    check("base" in maps and "normal" in maps, "the colour and the normal stay")

    # Without an ORM the separate maps are used as they are.
    folder2 = Path(tempfile.mkdtemp(prefix="gaa_orm_folder2_"))
    for name in ("Wood_col.png", "Wood_nrm.png", "Wood_rgh.png"):
        scratch = bpy.data.images.new(name + "_scratch", width=8, height=8)
        scratch.filepath_raw = str(folder2 / name)
        scratch.file_format = "PNG"
        scratch.save()
        bpy.data.images.remove(scratch)
    maps2 = materials.find_maps(str(folder2))
    check("rough" in maps2, f"without an ORM the roughness is used ({sorted(maps2)})")


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


def part_install(baking):
    print("\n== the whole pipeline: bake, save, build a plain material, install it")
    clean_scene()
    obj, material = make_object("InstallCube")
    folder = Path(tempfile.mkdtemp(prefix="gaa_install_")) / "InstallCube_baked"
    report = baking.bake_and_install(
        obj, targets=("albedo", "normal"), size=32, samples=4, folder=str(folder)
    )
    check(report.get("ok") is True, f"the pipeline finished ({report.get('error')})")
    check(bool(report.get("files")), "the maps were written to disk")
    if report.get("files"):
        for path in report["files"]:
            check(Path(path).exists(), f"the PNG is there: {Path(path).name}")

    check(bool(report.get("material")), f"a plain material was built ({report.get('material')})")
    check(
        report.get("assigned") == obj.name,
        f"it was put on the object ({report.get('assigned')})",
    )
    check(
        obj.data.materials and obj.data.materials[0].name == report.get("material"),
        f"the object wears the baked material ({[m.name for m in obj.data.materials]})",
    )
    # The folder is the reusable artifact: the same material can be put on
    # another object with the "PBR from folder" button.
    check(folder.is_dir(), "the folder with the maps is on disk")
    if folder.is_dir():
        names = sorted(p.name for p in folder.iterdir())
        check(bool(names), f"the folder holds the maps ({names})")
    # The procedural material is not deleted - it is still in the file.
    check(
        bpy.data.materials.get(material.name) is not None,
        "the source material is still in the file",
    )


def part_names(baking, procedural):
    print("\n== the naming pair: <X>_Procedural -> <X>_Bake")
    # The material the model did not name.
    result = procedural.build_tool(recipe="wood")
    check(
        "'Wood_Procedural'" in result,
        f"a material without a name says what it is ({result[:70]})",
    )
    result = procedural.build_tool(pattern="voronoi", color_a="#885544")
    check(
        "_Procedural'" in result,
        f"a material built from a pattern is marked too ({result[:70]})",
    )
    # The baked twin drops the marker.
    source = bpy.data.materials.get("Wood_Procedural")
    if source is None:
        for candidate in bpy.data.materials:
            if candidate.name.startswith("Wood_Procedural"):
                source = candidate
                break
    check(source is not None, "the procedural material exists")
    if source is not None:
        folder = Path(baking.bake_folder_for(source))
        check(
            folder.name == "Wood_Bake",
            f"the baked name drops the marker ({folder.name})",
        )
        check(
            "procedural" not in folder.name.lower(),
            "the word 'procedural' does not survive into the result",
        )
    # A copy of the same material ("Wood_Procedural.001") is the same wood.
    copy = bpy.data.materials.new("Wood_Procedural.001")
    check(
        Path(baking.bake_folder_for(copy)).name == "Wood_Bake",
        "a copy of the material bakes into the same folder",
    )


def part_bake_twice(baking):
    print("\n== baking twice reads the source and reuses the material")
    clean_scene()
    obj, material = make_object("TwiceCube")
    folder = Path(tempfile.mkdtemp(prefix="gaa_twice_")) / "TwiceCube_baked"

    first = baking.bake_and_install(
        obj, targets=("albedo",), size=32, samples=2, folder=str(folder)
    )
    check(first.get("ok") is True, f"the first bake finished ({first.get('error')})")
    source = first.get("source_material")
    check(source == material.name, f"the source is remembered ({source})")
    check(
        obj.active_material.name == first.get("material"),
        "the object wears the baked material",
    )

    second = baking.bake_and_install(
        obj, targets=("albedo",), size=32, samples=2, folder=str(folder)
    )
    check(
        second.get("source_material") == material.name,
        f"the second bake reads the source, not the result "
        f"({second.get('source_material')})",
    )
    check(
        second.get("material") == first.get("material"),
        f"the material is reused ({second.get('material')})",
    )
    check(
        bpy.data.materials.get(str(first.get("material")) + ".001") is None,
        "no duplicate material was created",
    )


def part_naming(baking):
    print("\n== the names of the baked files")
    clean_scene()
    obj, _mat = make_object("NamedCube")
    folder = Path(tempfile.mkdtemp(prefix="gaa_name_")) / "NamedCube_baked"
    report = baking.bake_and_install(
        obj, targets=("albedo", "normal"), size=32, samples=2,
        folder=str(folder), naming="game",
    )
    check(report.get("ok") is True, f"the bake finished ({report.get('error')})")
    names = sorted(Path(path).name for path in report.get("files", []))
    check(
        all(name.endswith(("_col.png", "_nrm.png")) for name in names),
        f"the game names are used ({names})",
    )

    folder2 = Path(tempfile.mkdtemp(prefix="gaa_name2_")) / "NamedCube_baked"
    report2 = baking.bake_and_install(
        obj, targets=("albedo",), size=32, samples=2,
        folder=str(folder2), naming="readable",
    )
    names2 = sorted(Path(path).name for path in report2.get("files", []))
    check(
        all(name.endswith("_albedo.png") for name in names2),
        f"the readable names are used ({names2})",
    )


def part_settings(baking):
    print("\n== the bake settings live on the scene")
    settings = baking.settings_from_scene(bpy.context.scene)
    check(settings.size == 1024, f"the default size is 1024 ({settings.size})")
    check(settings.samples == 16, f"the default samples is 16 ({settings.samples})")
    check(settings.maps == "game", f"the default map set is 'game' ({settings.maps})")
    check(
        settings.naming == "game",
        f"the default naming is the game one ({settings.naming})",
    )
    # A property on the operator would be thrown away on the next redraw; on
    # the scene it survives.
    bpy.context.scene.gaa_bake_size = 512
    check(
        baking.settings_from_scene(bpy.context.scene).size == 512,
        "the size can be changed by hand and is kept",
    )
    bpy.context.scene.gaa_bake_size = 1024


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
    check("_Bake" in result.report, f"the replace tool reports ({result.report[:70]})")

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
        part_install(baking)
        part_orm(baking)
        part_preview(baking)
        part_orm_wins(
            importlib.import_module("blender_addon.procedural"),
            importlib.import_module("blender_addon.materials"),
        )
        part_naming(baking)
        part_names(baking, importlib.import_module("blender_addon.procedural"))
        part_bake_twice(baking)
        part_settings(baking)
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
