"""Blender-side checks for the height map -> normal map conversion.

    blender --background --factory-startup --python tests/run_heightmap_check.py

A normal map is a **derivative** of a height map. The tests check the maths
against known height fields: a ramp, a flat field, a bump, and the vertical
flip.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
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


def make_height_image(name, size=64, kind="ramp"):
    """A height map with a known shape."""
    import numpy as np

    buffer = np.zeros((size, size, 4), dtype=np.float32)
    ys = np.arange(size) / (size - 1)
    xs = np.arange(size) / (size - 1)
    if kind == "ramp":
        # The height grows towards +u.
        field = np.tile(xs, (size, 1))
    elif kind == "flat":
        field = np.zeros((size, size), dtype=np.float32)
    elif kind == "bump":
        field = np.exp(-(((xs[None, :] - 0.5) ** 2) / 0.01))
    else:
        field = np.tile(xs, (size, 1))

    buffer[:, :, 0] = field
    buffer[:, :, 1] = field
    buffer[:, :, 2] = field
    buffer[:, :, 3] = 1.0

    image = bpy.data.images.new(name, width=size, height=size, alpha=False)
    image.pixels.foreach_set(buffer.ravel())
    return image


def read_field(image):
    """The red channel of an image as a numpy array."""
    import numpy as np

    width, height = image.size
    buffer = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    return buffer.reshape(height, width, 4)


# -- checks -----------------------------------------------------------------


def part_flat(heightmap):
    print("\n== a flat height map")
    source = make_height_image("FlatHeight", kind="flat")
    result = heightmap.height_to_normal(source, 1.0, False, "FlatNormal")
    check(result is not None, "the normal map was built")
    if result is None:
        return
    field = read_field(result)
    centre = field[32, 32]
    check(
        abs(centre[0] - 0.5) < 0.01 and abs(centre[1] - 0.5) < 0.01,
        f"the flat map gives a flat normal ({[round(v, 3) for v in centre]})",
    )
    check(
        abs(centre[2] - 1.0) < 0.01,
        "the blue channel is 1 (towards the viewer)",
    )
    check(result.is_float, "the normal map has a float buffer")
    check(
        result.colorspace_settings.name == "Non-Color",
        "the normal map is a data map",
    )


def part_ramp(heightmap):
    print("\n== a ramp: the height grows towards +u")
    source = make_height_image("RampHeight", kind="ramp")
    result = heightmap.height_to_normal(source, 1.0, False, "RampNormal")
    field = read_field(result)
    centre = field[32, 32]
    # The surface goes up towards +u, so the normal leans towards -u.
    check(
        centre[0] < 0.35,
        f"the normal leans towards -u ({centre[0]:.3f})",
    )
    check(
        abs(centre[1] - 0.5) < 0.02,
        f"the normal has no vertical tilt ({centre[1]:.3f})",
    )


def part_resolution(heightmap):
    print("\n== the result does not depend on the resolution")
    import numpy as np

    small = heightmap.normal_from_height_array(
        np.tile(np.arange(64) / 63.0, (64, 1)), 1.0, False
    )
    big = heightmap.normal_from_height_array(
        np.tile(np.arange(256) / 255.0, (256, 1)), 1.0, False
    )
    check(
        abs(small[32, 32, 0] - big[128, 128, 0]) < 0.01,
        f"the same relief gives the same normal "
        f"({small[32, 32, 0]:.3f} vs {big[128, 128, 0]:.3f})",
    )


def part_strength(heightmap):
    print("\n== the strength scales the slope")
    source = make_height_image("StrengthHeight", kind="ramp")
    weak = read_field(heightmap.height_to_normal(source, 1.0, False, "Weak"))[32, 32]
    strong = read_field(heightmap.height_to_normal(source, 4.0, False, "Strong"))[32, 32]
    check(
        strong[0] < weak[0],
        f"a stronger slope tilts the normal more ({strong[0]:.3f} < {weak[0]:.3f})",
    )


def part_flip(heightmap):
    print("\n== the vertical flip")
    import numpy as np

    # A ramp that grows towards +v (along the rows).
    vertical = np.tile((np.arange(32) / 31.0)[:, None], (1, 32))
    normal = heightmap.normal_from_height_array(vertical, 1.0, False)
    flipped = heightmap.normal_from_height_array(vertical, 1.0, True)
    check(
        abs(normal[16, 16, 1] - flipped[16, 16, 1]) > 0.1,
        f"the green channel changes "
        f"({normal[16, 16, 1]:.3f} vs {flipped[16, 16, 1]:.3f})",
    )
    check(
        abs(normal[16, 16, 0] - flipped[16, 16, 0]) < 1e-5,
        "the red channel does not change",
    )


def part_assign(heightmap):
    print("\n== putting it into the material")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "NormalCube"
    mat = bpy.data.materials.new("NormalMat")
    mat.use_nodes = True
    cube.data.materials.append(mat)

    source = make_height_image("AssignHeight", size=32)
    normal = heightmap.height_to_normal(source, 1.0, False, "AssignNormal")
    ok = heightmap.assign_to_material(normal, cube, mat)
    check(ok is True, "the normal map was assigned")
    tree = mat.node_tree
    textures = [n for n in tree.nodes if n.type == "TEX_IMAGE"]
    check(len(textures) == 1, "the material has one texture node")
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    check(
        bool(bsdf.inputs["Normal"].links),
        "the Normal input is connected",
    )


def part_tools(heightmap):
    print("\n== the tool")
    tools = importlib.import_module("blender_addon.tools")
    check(
        tools.get("bake.normal_from_height") is not None,
        "bake.normal_from_height is registered",
    )
    source = make_height_image("ToolHeight", size=32)
    result = tools.execute("bake.normal_from_height", {"image": source.name})
    check(result.ok, "the tool runs")
    check("normal map" in result.report.lower(), f"the tool reports ({result.report[:70]})")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        heightmap = importlib.import_module("blender_addon.heightmap")

        part_flat(heightmap)
        part_ramp(heightmap)
        part_resolution(heightmap)
        part_strength(heightmap)
        part_flip(heightmap)
        part_assign(heightmap)
        part_tools(heightmap)
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
