"""Blender-side checks for the round trip with other programs.

    blender --background --factory-startup --python tests/run_exchange_check.py

The round trip is: Blender -> UV kit -> Aseprite/Krita -> painted atlas ->
Blender. Two things have to hold:

* the exported layout has to be **the same space** as the UV, so a pixel of the
  picture is a pixel of the texture;
* the import has to put the picture into the material **without touching the
  UV**, so it lands exactly where the layout said it would.

Both are checked here, together with the manifest, which is what makes the kit
self-describing.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
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
    """A cube with a textured material and a UV layout."""
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "KitCube"

    image = bpy.data.images.new("Kit_BaseColor", width=64, height=64)
    mat = bpy.data.materials.new("KitMat")
    mat.use_nodes = True
    tree = mat.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.name = "Albedo"
    tex.image = image
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    cube.data.materials.append(mat)

    # A UV layout to export.
    cube.data.uv_layers.new(name="UVMap")
    layer = cube.data.uv_layers[0]
    for loop in cube.data.loops:
        co = cube.data.vertices[loop.vertex_index].co
        layer.data[loop.index].uv = (co.x * 0.25 + 0.5, co.y * 0.25 + 0.5)
    return cube, mat, tex


# -- checks -----------------------------------------------------------------


def part_layout(exchange, uv, cube):
    print("\n== the layout is drawn")
    image = exchange.render_layout([cube], 256, "TestLayout")
    check(image is not None, "the layout image was created")
    check(
        tuple(image.size) == (256, 256),
        f"the layout has the atlas size ({tuple(image.size)})",
    )

    # The islands must actually be drawn: the picture is not empty.
    pixels = list(image.pixels)
    check(any(p < 0.99 for p in pixels), "the picture is not empty")

    # The drawn islands must match the UV space: the corner of the cube that
    # sits at UV (0.5, 0.5) has to be inside a drawn island.
    import numpy as np

    buffer = np.empty(256 * 256 * 4, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    buffer = buffer.reshape(256, 256, 4)
    # Pixel (128, 128) is UV (0.5, 0.5) - the middle of the layout.
    centre = buffer[128, 128]
    check(
        centre[0] < 0.99,
        f"the middle of the layout is drawn ({centre[0]:.2f})",
    )


def part_kit(exchange, uv, cube):
    print("\n== the kit")
    folder = Path(tempfile.mkdtemp(prefix="gaa_kit_"))
    report = exchange.export_kit([cube], str(folder), 256)
    check(Path(report["layout"]).exists(), "the layout was written")
    check(Path(report["manifest"]).exists(), "the manifest was written")

    manifest = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    check(manifest["atlas_size"] == 256, "the manifest says the atlas size")
    check("density" in manifest, "the manifest says the target density")
    check("margin" in manifest, "the manifest says the gap")
    check(len(manifest["objects"]) == 1, "the manifest lists the objects")
    check(
        manifest["objects"][0]["islands"] >= 1,
        "the manifest says how many islands each object has",
    )
    check(
        manifest["objects"][0].get("texture") == "Kit_BaseColor",
        "the manifest names the texture",
    )
    check(
        (folder / "albedo_KitCube.png").exists(),
        "a copy of the texture was written",
    )


def part_import(exchange, uv, cube, mat, tex):
    print("\n== the import")
    # A painted atlas: a different picture, same size.
    folder = Path(tempfile.mkdtemp(prefix="gaa_paint_"))
    painted = folder / "painted.png"
    import numpy as np

    buffer = np.zeros((64, 64, 4), dtype=np.float32)
    buffer[:, :, 0] = 1.0
    buffer[:, :, 3] = 1.0
    scratch = bpy.data.images.new("Painted", width=64, height=64, alpha=True)
    scratch.pixels.foreach_set(buffer.ravel())
    scratch.filepath_raw = str(painted)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)

    uv_before = [
        tuple(layer.uv) for layer in cube.data.uv_layers[0].data
    ]

    report = exchange.import_atlas(str(painted), cube)
    check(report.get("ok") is True, f"the atlas was imported ({report.get('error')})")
    check(report["material"] == "KitMat", "the material was found")
    check(
        tex.image.name.lower().startswith("painted"),
        f"the texture node got the new image ({tex.image.name})",
    )

    uv_after = [
        tuple(layer.uv) for layer in cube.data.uv_layers[0].data
    ]
    check(
        uv_before == uv_after,
        "the UV coordinates were NOT touched - the picture lands where it should",
    )


def part_import_into_empty_material(exchange, uv):
    print("\n== the import into a material without a texture")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "BareCube"
    mat = bpy.data.materials.new("BareMat")
    mat.use_nodes = True
    cube.data.materials.append(mat)

    folder = Path(tempfile.mkdtemp(prefix="gaa_bare_"))
    path = folder / "atlas.png"
    scratch = bpy.data.images.new("Bare", width=32, height=32, alpha=True)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)

    report = exchange.import_atlas(str(path), cube)
    check(report.get("ok") is True, f"the atlas was imported ({report.get('error')})")
    check(report.get("created") is True, "a texture node was created")
    tree = mat.node_tree
    textures = [n for n in tree.nodes if n.type == "TEX_IMAGE"]
    check(len(textures) == 1, "the material has one texture node")


def part_tools(exchange):
    print("\n== the tools")
    tools = importlib.import_module("blender_addon.tools")
    for name in ("uv.export_kit", "uv.import_atlas"):
        check(tools.get(name) is not None, f"{name} is registered")

    folder = Path(tempfile.mkdtemp(prefix="gaa_toolkit_"))
    result = tools.execute("uv.export_kit", {"folder": str(folder), "size": 128})
    check(result.ok, "the export tool runs")
    check("UV kit" in result.report, f"the export tool reports ({result.report[:60]})")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        exchange = importlib.import_module("blender_addon.exchange")
        uv = importlib.import_module("blender_addon.uv")

        cube, mat, tex = make_scene()
        part_layout(exchange, uv, cube)
        part_kit(exchange, uv, cube)
        part_import(exchange, uv, cube, mat, tex)
        part_import_into_empty_material(exchange, uv)
        part_tools(exchange)
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
