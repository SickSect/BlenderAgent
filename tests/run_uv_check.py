"""Blender-side checks for the UV analyser.

    blender --background --factory-startup --python tests/run_uv_check.py

The analyser is the first stage of the UV work, and it is the tool that has to
be right: everything else (unwrapping, density fixing, atlasing) builds on
its measurements. This script builds meshes with known defects and checks that
the analyser finds each one and measures the right numbers.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
import math
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


# -- a texture --------------------------------------------------------------


def make_texture(size=1024):
    """A real image of a known size, to measure the density against."""
    path = Path(tempfile.gettempdir()) / "uv_check_texture.png"
    scratch = bpy.data.images.new("uv_check_scratch", width=size, height=size)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = "UVCheck"
    return image


def give_material(obj, image):
    mat = bpy.data.materials.new(f"{obj.name}_mat")
    mat.use_nodes = True
    tree = mat.node_tree
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    obj.data.materials.append(mat)
    return mat


def _co(mesh, loop):
    """The position of the vertex a loop belongs to.

    ``MeshLoop.vertex`` was removed in Blender 5.x - only ``vertex_index``
    is left - so the lookup goes through the mesh.
    """
    return mesh.vertices[loop.vertex_index].co


# -- checks -----------------------------------------------------------------


def part_no_uv(uv):
    print("\n== a mesh without UV")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "NoUvCube"
    # The default cube comes with a UV map; remove it.
    for layer in list(cube.data.uv_layers):
        cube.data.uv_layers.remove(layer)
    report = uv.analyze(cube)
    check(report.has_uv is False, "the mesh is reported as having no UV")
    check(len(report.islands) == 0, "it has no islands")

    found = uv.report_findings(report)
    codes = {f.code for f in found}
    check(uv.NO_UV in codes, "the missing UV is a finding")


def part_density(uv, image):
    print("\n== the texel density")
    # A 2x2 metre plane with a full [0,1] unwrap and a 1024px texture:
    # the UV area is 1, the world area is 4 m^2, the texture has 1024^2 pixels.
    # density = sqrt(1 * 1024^2 / 4) = 512 px/m.
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    plane = bpy.context.active_object
    plane.name = "DensityPlane"
    plane.data.uv_layers.new(name="UVMap")
    uv_layer = plane.data.uv_layers[0]
    for loop in plane.data.loops:
        co = _co(plane.data, loop)
        uv_layer.data[loop.index].uv = (co.x / 2.0 + 0.5, co.y / 2.0 + 0.5)
    give_material(plane, image)

    report = uv.analyze(plane)
    check(report.has_uv is True, "the UV is found")
    check(len(report.islands) == 1, f"one island ({len(report.islands)})")
    if report.islands:
        island = report.islands[0]
        expected = 512.0
        check(
            island.density is not None
            and abs(island.density - expected) / expected < 0.02,
            f"the density is {expected} px/m (got {island.density})",
        )
        check(
            island.texel_size is not None
            and abs(island.texel_size - 1.0 / expected) < 0.001,
            "the texel size in metres follows",
        )


def part_scaled_object(uv, image):
    print("\n== the object scale matters")
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    plane = bpy.context.active_object
    plane.name = "ScaledPlane"
    plane.scale = (0.5, 0.5, 0.5)   # half the size in the world
    bpy.context.view_layer.update()
    plane.data.uv_layers.new(name="UVMap")
    uv_layer = plane.data.uv_layers[0]
    for loop in plane.data.loops:
        co = _co(plane.data, loop)
        uv_layer.data[loop.index].uv = (co.x / 2.0 + 0.5, co.y / 2.0 + 0.5)
    give_material(plane, image)

    report = uv.analyze(plane)
    if report.islands and report.islands[0].density:
        density = report.islands[0].density
        check(
            abs(density - 1024.0) / 1024.0 < 0.05,
            f"the halved object has twice the density ({density:.0f} px/m)",
        )
    else:
        check(False, "the scaled object has a density")


def part_outside(uv):
    print("\n== UVs outside [0,1]")
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    plane = bpy.context.active_object
    plane.name = "OutsidePlane"
    plane.data.uv_layers.new(name="UVMap")
    uv_layer = plane.data.uv_layers[0]
    for loop in plane.data.loops:
        co = _co(plane.data, loop)
        # Deliberately push one corner past 1.0.
        u = co.x / 2.0 + 0.5
        v = co.y / 2.0 + 0.5
        if co.x > 0.5:
            u = 1.4
        uv_layer.data[loop.index].uv = (u, v)

    report = uv.analyze(plane)
    check(report.outside > 0, f"the coordinates outside were counted ({report.outside})")
    codes = {f.code for f in uv.report_findings(report)}
    check(uv.OUT_OF_BOUNDS in codes, "it is a finding")


def part_overlap(uv):
    print("\n== overlapping islands")
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    first = bpy.context.active_object
    first.name = "OverlapA"
    first.data.uv_layers.new(name="UVMap")
    uv_layer = first.data.uv_layers[0]
    for loop in first.data.loops:
        co = _co(first.data, loop)
        uv_layer.data[loop.index].uv = (co.x / 2.0 + 0.5, co.y / 2.0 + 0.5)

    # A second plane with exactly the same UVs: the islands must overlap.
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    second = bpy.context.active_object
    second.name = "OverlapB"
    second.data.uv_layers.new(name="UVMap")
    uv_layer2 = second.data.uv_layers[0]
    for loop in second.data.loops:
        co = _co(second.data, loop)
        uv_layer2.data[loop.index].uv = (co.x / 2.0 + 0.5, co.y / 2.0 + 0.5)

    report_a = uv.analyze(first)
    report_b = uv.analyze(second)
    # The two objects each have one island; the overlap is between objects.
    check(len(report_a.islands) == 1, "the first object has one island")
    check(len(report_b.islands) == 1, "the second object has one island")

    # Now put two islands inside ONE object: a plane split by a seam.
    bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=2, y_subdivisions=2)
    split = bpy.context.active_object
    split.name = "SplitPlane"
    split.data.uv_layers.new(name="UVMap")
    uv_layer3 = split.data.uv_layers[0]
    for loop in split.data.loops:
        co = _co(split.data, loop)
        uv_layer3.data[loop.index].uv = (co.x / 4.0 + 0.5, co.y / 4.0 + 0.5)
    # Mark the edges along the middle column as seams so the plane splits into
    # two islands. The vertex numbering depends on the grid, so the seam is
    # found by position rather than by index.
    me = split.data
    marked = 0
    for edge in me.edges:
        a = me.vertices[edge.vertices[0]].co
        b = me.vertices[edge.vertices[1]].co
        if abs(a.x) < 0.6 and abs(b.x) < 0.6 and abs(a.x - b.x) < 1e-6:
            edge.use_seam = True
            marked += 1
    check(marked > 0, f"a seam was marked ({marked} edges)")

    report = uv.analyze(split)
    check(
        len(report.islands) >= 2,
        f"the seam split the mesh into islands ({len(report.islands)})",
    )


def part_audit(uv, image):
    print("\n== the audit and the tool")
    found = uv.audit()
    check(isinstance(found, list), "the audit returns a list")

    tools = importlib.import_module("blender_addon.tools")
    check(tools.get("uv.audit") is not None, "the uv.audit tool is registered")
    result = tools.execute("uv.audit")
    check(result.ok, "the tool runs")
    check(bool(result.report), f"the tool reports ({result.report[:60]})")


def part_report(uv, image):
    print("\n== the readable report")
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    plane = bpy.context.active_object
    plane.name = "ReportPlane"
    plane.data.uv_layers.new(name="UVMap")
    uv_layer = plane.data.uv_layers[0]
    for loop in plane.data.loops:
        co = _co(plane.data, loop)
        uv_layer.data[loop.index].uv = (co.x / 2.0 + 0.5, co.y / 2.0 + 0.5)
    give_material(plane, image)

    report = uv.analyze(plane)
    text = uv.format_report(report)
    check("ReportPlane" in text, "the report names the object")
    check("px/m" in text, "the report shows the density")
    check("island" in text, "the report lists the islands")


def part_seams(uv):
    print("\n== the seams follow the angle")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "SeamCube"

    # A cube has 90 degree edges: at a 40 degree threshold every edge of the
    # hard side must become a seam.
    count = uv.mark_seams(cube, 40.0, respect_sharp=False)
    check(count == 12, f"a cube has 12 seams at 40 deg ({count})")

    # At 120 degrees the 90 degree edges are no longer seams.
    bpy.ops.mesh.primitive_cube_add()
    other = bpy.context.active_object
    other.name = "SeamCube2"
    count_high = uv.mark_seams(other, 120.0, respect_sharp=False)
    check(count_high == 0, f"at 120 deg a cube has no seams ({count_high})")

    # A smooth sphere has no hard edges at all.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    sphere = bpy.context.active_object
    sphere.name = "SmoothSphere"
    count_smooth = uv.mark_seams(sphere, 40.0, respect_sharp=False)
    check(count_smooth == 0, f"a smooth sphere has no angle seams ({count_smooth})")


def part_settings(uv):
    print("\n== the settings")
    settings = uv.settings_from_scene()
    check(settings.angle == 40.0, f"the default angle is 40 ({settings.angle})")
    check(settings.method == "ANGLE_BASED", "the default method is angle based")
    check(settings.density == 16.0, "the default density is 16 px/m")
    check(settings.atlas == 2048, "the default atlas is 2048")

    # The settings live on the scene, so every project has its own.
    bpy.context.scene.gaa_uv_angle = 65.0
    bpy.context.scene.gaa_uv_density = 32
    settings = uv.settings_from_scene()
    check(settings.angle == 65.0, f"the scene angle is used ({settings.angle})")
    check(settings.density == 32.0, "the scene density is used")

    # A single call can override them.
    overridden = uv.apply_overrides(settings, angle=20.0, margin=0.05)
    check(overridden.angle == 20.0, "the angle can be overridden per call")
    check(overridden.margin == 0.05, "the margin can be overridden per call")
    check(settings.angle == 65.0, "the original settings are untouched")

    bpy.context.scene.gaa_uv_angle = 40.0
    bpy.context.scene.gaa_uv_density = 16


def part_unwrap(uv):
    print("\n== the unwrap")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "UnwrapCube"
    settings = uv.settings_from_scene()
    report = uv.unwrap_object(cube, settings)
    check(report.get("ok") is True, f"the cube was unwrapped ({report.get('error')})")
    check(report["seams"] == 12, f"the seams were marked ({report['seams']})")
    check(cube.data.uv_layers, "the cube has a UV layer")

    # The islands must not overlap after the unwrap.
    found = uv.audit([cube])
    overlap = [f for f in found if f.code == uv.OVERLAP]
    check(not overlap, "the unwrapped cube has no overlapping islands")

    # A smooth sphere cannot be unwrapped by angle: the fallback must kick in.
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    sphere = bpy.context.active_object
    sphere.name = "UnwrapSphere"
    report = uv.unwrap_object(sphere, settings)
    check(report.get("ok") is True, f"the sphere was unwrapped ({report.get('error')})")
    check(
        report["method"] == "smart_project",
        f"the smooth mesh fell back to smart_project ({report['method']})",
    )


def part_tools(uv):
    print("\n== the tools")
    tools = importlib.import_module("blender_addon.tools")
    for name in ("uv.audit", "uv.unwrap", "uv.mark_seams"):
        check(tools.get(name) is not None, f"{name} is registered")

    result = tools.execute("uv.unwrap", {"angle": 30})
    check(result.ok, "the unwrap tool runs")
    check("30" in result.report, f"the override reached the tool ({result.report[:70]})")

    result = tools.execute("uv.mark_seams", {"angle": 25})
    check(result.ok, "the mark seams tool runs")


def part_density_match(uv, image):
    print("\n== matching the density")
    # Two islands of clearly different density: a big one and a small one.
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "DensityCube"
    give_material(cube, image)

    settings = uv.settings_from_scene()
    settings.angle = 40.0
    report = uv.unwrap_object(cube, settings)
    check(report.get("ok") is True, "the cube was unwrapped first")

    # Now squash one island so the densities differ.
    uv_layer = cube.data.uv_layers[0]
    for loop in cube.data.loops:
        if loop.index == 0:
            co = uv_layer.data[loop.index].uv
            uv_layer.data[loop.index].uv = (co[0] * 0.4, co[1] * 0.4)

    before = uv.analyze(cube)
    densities = before.densities
    check(len(densities) > 1, f"the cube has several islands ({len(densities)})")
    if len(densities) > 1:
        check(
            max(densities) / min(densities) > 1.2,
            f"the densities really differ ({min(densities):.0f}..{max(densities):.0f})",
        )
        codes = {f.code for f in uv.report_findings(before)}
        check(uv.DENSITY_SPREAD in codes, "the difference is reported")

    # Match them to 64 px/m.
    result = uv.match_density(cube, 64.0)
    check(result.get("ok") is True, f"the density was matched ({result.get('error')})")
    after = uv.analyze(cube)
    values = after.densities
    check(bool(values), "the islands still have a density")
    if len(values) > 1:
        spread = max(values) / min(values)
        check(
            spread < 1.06,
            f"the islands now share the density (spread {spread:.3f})",
        )
        target = sum(values) / len(values)
        check(
            abs(target - 64.0) / 64.0 < 0.15,
            f"the density is close to the target ({target:.1f} px/m)",
        )


def part_density_tools(uv):
    print("\n== the density tool")
    tools = importlib.import_module("blender_addon.tools")
    check(tools.get("uv.match_density") is not None, "uv.match_density is registered")
    result = tools.execute("uv.match_density", {"target": 32})
    check(result.ok, "the tool runs")
    check("32" in result.report, f"the target reached the tool ({result.report[:70]})")


def part_padding(uv):
    print("\n== the padding")
    bpy.ops.mesh.primitive_grid_add(size=2, x_subdivisions=1, y_subdivisions=1)
    plane = bpy.context.active_object
    plane.name = "PadPlane"
    plane.data.uv_layers.new(name="UVMap")
    uv_layer = plane.data.uv_layers[0]
    for loop in plane.data.loops:
        co = _co(plane.data, loop)
        uv_layer.data[loop.index].uv = (co.x / 2.0 + 0.5, co.y / 2.0 + 0.5)

    before = uv.analyze(plane)
    size_before = before.islands[0].size_uv

    padding = 0.05
    count = uv._pad_object(plane, padding)
    check(count == 1, f"the island was padded ({count})")

    after = uv.analyze(plane)
    size_after = after.islands[0].size_uv
    grown_u = size_after[0] - size_before[0]
    grown_v = size_after[1] - size_before[1]
    check(
        abs(grown_u - padding * 2) < 0.01,
        f"the island grew by the padding on U ({grown_u:.3f})",
    )
    check(
        abs(grown_v - padding * 2) < 0.01,
        f"the island grew by the padding on V ({grown_v:.3f})",
    )


def part_atlas(uv, image):
    print("\n== the atlas")
    objects = []
    for index in range(2):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        cube.name = f"AtlasCube{index}"
        give_material(cube, image)
        objects.append(cube)

    settings = uv.settings_from_scene()
    settings.density = 32.0
    settings.atlas = 2048
    settings.margin = 0.01

    # Unwrap both objects first.
    for cube in objects:
        uv.unwrap_object(cube, settings)

    report = uv.build_atlas(objects, settings)
    check(report.get("ok") is True, f"the atlas was built ({report.get('error')})")
    check(report["islands"] >= 8, f"both cubes are in it ({report['islands']})")

    # Nothing may overlap and everything must stay inside [0,1].
    for cube in objects:
        result = uv.analyze(cube)
        for island in result.islands:
            check(
                island.uv_min[0] >= -0.001 and island.uv_max[0] <= 1.001,
                f"{cube.name}: the island stays inside [0,1] "
                f"({island.uv_min[0]:.3f}..{island.uv_max[0]:.3f})",
            )
            check(
                island.uv_min[1] >= -0.001 and island.uv_max[1] <= 1.001,
                f"{cube.name}: the island stays inside [0,1] in V",
            )

    # The two objects must share the density.
    densities = []
    for cube in objects:
        densities.extend(uv.analyze(cube).densities)
    check(bool(densities), "the islands have a density")
    if densities:
        spread = max(densities) / min(densities)
        check(
            spread < 1.15,
            f"the atlas has one density for every island ({spread:.3f})",
        )

    # The islands of the two objects must not overlap each other.
    boxes = []
    for cube in objects:
        for island in uv.analyze(cube).islands:
            boxes.append((cube.name, island.uv_min, island.uv_max))
    overlaps = 0
    for i, (name_a, min_a, max_a) in enumerate(boxes):
        for j, (name_b, min_b, max_b) in enumerate(boxes):
            if i >= j:
                continue
            if (
                min_a[0] < max_b[0]
                and min_b[0] < max_a[0]
                and min_a[1] < max_b[1]
                and min_b[1] < max_a[1]
            ):
                overlaps += 1
    check(overlaps == 0, f"the islands of the atlas do not overlap ({overlaps})")


def part_atlas_tools(uv):
    print("\n== the atlas tools")
    tools = importlib.import_module("blender_addon.tools")
    for name in ("uv.build_atlas", "uv.unwrap_own"):
        check(tools.get(name) is not None, f"{name} is registered")
    result = tools.execute("uv.build_atlas", {"density": 16, "atlas": 1024})
    check(result.ok, "the atlas tool runs")
    check("Atlas" in result.report, f"the atlas tool reports ({result.report[:70]})")

    result = tools.execute("uv.unwrap_own", {"angle": 45})
    check(result.ok, "the own-layout tool runs")


def part_own_seams(uv):
    print("\n== unwrapping along your own seams")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "SeamCube"

    # Mark one edge by hand - the kind of decision a user makes.
    mesh = cube.data
    edge = mesh.edges[0]
    edge.use_seam = True
    check(uv.count_seams(cube) == 1, "the hand-marked seam is counted")

    # The automatic seams must NOT remove it.
    uv.mark_seams(cube, 40.0, respect_sharp=False)
    check(
        uv.count_seams(cube) > 1,
        f"the automatic seams were added on top ({uv.count_seams(cube)})",
    )
    check(
        mesh.edges[0].use_seam,
        "the hand-marked seam survived the automatic pass",
    )

    # Unwrapping along the own seams only.
    settings = uv.settings_from_scene()
    settings.use_existing_seams = True
    uv.clear_seams(cube)
    mesh.edges[0].use_seam = True
    report = uv.unwrap_object(cube, settings)
    check(report.get("ok") is True, f"the unwrap finished ({report.get('error')})")
    check(report["own_seams"] is True, "the report says the own seams were used")
    check(
        report["seams"] == 1,
        f"only the hand-marked seam was used ({report['seams']})",
    )


def part_height_seams(uv):
    print("\n== a horizontal cut")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "HeightCube"
    cube.location = (0, 0, 1.0)
    bpy.context.view_layer.update()
    # The cube spans z from 0 to 2 now.
    count = uv.mark_seams_by_height(cube, 1.0, 0.01)
    check(count == 4, f"the four edges of the middle ring are seams ({count})")
    check(uv.count_seams(cube) == 4, "the seams are counted")


def part_no_seams_finding(uv):
    print("\n== a model without seams")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "NoSeamCube"
    report = uv.analyze(cube)
    codes = {f.code for f in uv.report_findings(report, None, cube)}
    check(uv.NO_SEAMS in codes, "the missing seams are reported")


def part_symmetric(uv):
    print("\n== a symmetric unwrap")
    # A mesh that is exactly symmetric about X = 0: two boxes that touch at
    # the plane (left spans -1..0, right spans 0..1).
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.5, 0, 0))
    left = bpy.context.active_object
    left.name = "SymLeft"
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.5, 0, 0))
    right = bpy.context.active_object
    right.name = "SymRight"
    bpy.context.view_layer.update()

    # Join them into one object so the mirroring has to work inside a mesh.
    bpy.ops.object.select_all(action="DESELECT")
    left.select_set(True)
    right.select_set(True)
    bpy.context.view_layer.objects.active = left
    bpy.ops.object.join()
    joined = bpy.context.active_object
    joined.name = "SymPair"

    settings = uv.settings_from_scene()
    report = uv.make_symmetric(joined, "X", 0.0, unwrap=True, settings=settings)
    check(report.get("ok") is True, f"the symmetric unwrap finished ({report.get('error')})")
    check(report["seams"] > 0, f"the symmetry seam was marked ({report['seams']})")
    check(report["mirrored"] > 0, f"the far half was mirrored ({report['mirrored']})")

    # The two halves must now share their UVs: the counterpart of a vertex on
    # the right has the same UV as its twin on the left.
    mesh = joined.data
    layer = mesh.uv_layers.active
    check(layer is not None, "the mesh has a UV layer")

    def uvs_of(vertex_index):
        return {
            tuple(round(v, 5) for v in layer.data[i].uv)
            for i, loop in enumerate(mesh.loops)
            if loop.vertex_index == vertex_index
        }

    # The symmetry plane is in world coordinates.
    matrix = joined.matrix_world
    left_verts = [
        v for v in mesh.vertices if (matrix @ v.co).x < -0.1
    ]
    right_verts = [
        v for v in mesh.vertices if (matrix @ v.co).x > 0.1
    ]
    check(bool(left_verts) and bool(right_verts), "the mesh has both halves")

    shared = 0
    for lv in left_verts:
        a = matrix @ lv.co
        for rv in right_verts:
            b = matrix @ rv.co
            if abs(a.x + b.x) > 1e-4:
                continue
            if abs(a.y - b.y) > 1e-4 or abs(a.z - b.z) > 1e-4:
                continue
            if uvs_of(lv.index) & uvs_of(rv.index):
                shared += 1
    check(shared > 0, f"the counterparts share their UVs ({shared} pairs)")


def part_symmetric_modifier(uv):
    print("\n== a model with a Mirror modifier")
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.75, 0, 0))
    obj = bpy.context.active_object
    obj.name = "MirrorCage"
    modifier = obj.modifiers.new("Mirror", "MIRROR")
    modifier.use_axis[0] = True
    modifier.use_mirror_u = True     # the classic layout wants this OFF

    report = uv.make_symmetric(obj, "X", 0.0, unwrap=True)
    check(report.get("ok") is True, f"the symmetric unwrap finished ({report.get('error')})")
    check(report["mode"] == "mirror_modifier", f"the modifier path was taken ({report['mode']})")
    check(
        modifier.use_mirror_u is False,
        "Mirror UVs was switched off (the classic layout)",
    )


def part_tools(uv):
    print("\n== the tools")
    tools = importlib.import_module("blender_addon.tools")
    for name in (
        "uv.audit",
        "uv.unwrap",
        "uv.mark_seams",
        "uv.match_density",
        "uv.symmetric",
    ):
        check(tools.get(name) is not None, f"{name} is registered")

    result = tools.execute("uv.mark_seams", {"angle": 30})
    check(result.ok, "the mark seams tool runs")
    check("seam" in result.report.lower(), f"the tool reports ({result.report[:70]})")

    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.location = (0, 0, 1.0)
    bpy.context.view_layer.update()
    result = tools.execute("uv.mark_seams", {"height": 1.0})
    check(result.ok, "the height seam tool runs")
    check("height" in result.report.lower(), f"the tool reports ({result.report[:70]})")

    result = tools.execute("uv.mark_seams", {"clear": True})
    check(result.ok, "the clear tool runs")
    check("Removed" in result.report, f"the tool reports ({result.report[:70]})")

    bpy.ops.mesh.primitive_cube_add()
    bpy.context.view_layer.objects.active = bpy.context.active_object
    result = tools.execute("uv.symmetric", {"axis": "X"})
    check(result.ok, "the symmetric tool runs")
    check("symmetric" in result.report.lower(), f"the tool reports ({result.report[:80]})")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        uv = importlib.import_module("blender_addon.uv")
        image = make_texture(1024)

        part_no_uv(uv)
        part_density(uv, image)
        part_scaled_object(uv, image)
        part_outside(uv)
        part_overlap(uv)
        part_report(uv, image)
        part_seams(uv)
        part_settings(uv)
        part_unwrap(uv)
        part_density_match(uv, image)
        part_padding(uv)
        part_atlas(uv, image)
        part_own_seams(uv)
        part_height_seams(uv)
        part_symmetric(uv)
        part_symmetric_modifier(uv)
        part_no_seams_finding(uv)
        part_audit(uv, image)
        part_tools(uv)
        part_density_tools(uv)
        part_atlas_tools(uv)
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
