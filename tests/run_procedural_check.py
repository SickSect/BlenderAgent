"""Blender-side checks for the procedural material builder.

    blender --background --factory-startup --python tests/run_procedural_check.py

The builder turns a **specification** into a node graph. The tests check that
the graph is correct (the right nodes, the right connections, the right
values) and that the recipes and the matcher behave.

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


def nodes_of_type(material, node_type):
    return [n for n in material.node_tree.nodes if n.type == node_type]


# -- checks -----------------------------------------------------------------


def part_recipes(procedural):
    print("\n== the recipe catalogue")
    check(len(procedural.RECIPES) >= 10, f"there are recipes ({len(procedural.RECIPES)})")
    for name, recipe in procedural.RECIPES.items():
        check(bool(recipe.description), f"{name} has a description")
        check(bool(recipe.keywords), f"{name} has keywords")


def part_matching(procedural):
    print("\n== matching a description")
    matched = procedural.match_recipes("сделай тёмное дерево")
    check(bool(matched), "a request for wood is matched")
    if matched:
        check(matched[0][0].name == "wood", f"wood wins ({matched[0][0].name})")

    matched = procedural.match_recipes("a rusty metal surface")
    names = [r.name for r, _ in matched]
    check("rust" in names, f"rust is among the matches ({names})")

    check(
        not procedural.match_recipes("просто сделай что-нибудь"),
        "a request without a material word matches nothing",
    )
    check(not procedural.match_recipes(""), "an empty request matches nothing")


def part_build(procedural):
    print("\n== building a material")
    material = procedural.build_from_recipe("wood", "TestWood")
    check(material is not None, "the material was built")
    if material is None:
        return

    tree = material.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check(bsdf is not None, "there is a Principled BSDF")

    coords = nodes_of_type(material, "TEX_COORD")
    mapping = nodes_of_type(material, "MAPPING")
    noise = nodes_of_type(material, "TEX_NOISE")
    ramp = nodes_of_type(material, "VALTORGB")
    check(len(coords) == 1, "there are coordinates")
    check(len(mapping) == 1, "there is a mapping")
    check(len(noise) == 1, "there is a noise texture")
    check(len(ramp) == 1, "there is a colour ramp")

    # The chain: coordinates -> mapping -> texture -> ramp -> base colour.
    if coords and mapping and noise and ramp and bsdf:
        check(
            coords[0].outputs["Object"].links[0].to_node == mapping[0],
            "the coordinates go into the mapping",
        )
        check(
            mapping[0].outputs["Vector"].links[0].to_node == noise[0],
            "the mapping goes into the texture",
        )
        check(
            noise[0].outputs["Fac"].links[0].to_node == ramp[0],
            "the texture goes into the ramp",
        )
        check(
            ramp[0].outputs["Color"].links[0].to_node == bsdf,
            "the ramp goes into the Base Color",
        )

    # The wood recipe stretches the noise and has a bump.
    if mapping and noise:
        scale = mapping[0].inputs["Scale"].default_value
        check(
            abs(scale[1] - 12.0) < 0.01,
            f"the wood grain is stretched ({scale[1]})",
        )
    check(len(nodes_of_type(material, "BUMP")) == 1, "there is a bump")
    # The wood recipe varies the roughness, so it is driven by a Map Range
    # node rather than by a constant on the shader.
    rough_nodes = nodes_of_type(material, "MAP_RANGE")
    check(len(rough_nodes) == 1, "there is a roughness variation")
    if rough_nodes and bsdf is not None:
        check(
            bool(bsdf.inputs["Roughness"].links),
            "the roughness is driven by the variation",
        )
        to_min = rough_nodes[0].inputs["To Min"].default_value
        to_max = rough_nodes[0].inputs["To Max"].default_value
        check(
            abs((to_min + to_max) * 0.5 - 0.55) < 0.01,
            f"the average roughness is the recipe's ({to_min:.2f}..{to_max:.2f})",
        )
    else:
        check(
            bsdf is not None
            and abs(bsdf.inputs["Roughness"].default_value - 0.55) < 0.01,
            "the roughness is set",
        )


def part_patterns(procedural):
    print("\n== the patterns")
    for pattern, node_type in (
        ("voronoi", "TEX_VORONOI"),
        ("wave", "TEX_WAVE"),
        ("checker", "TEX_CHECKER"),
        ("gradient", "TEX_GRADIENT"),
        ("noise", "TEX_NOISE"),
    ):
        spec = procedural.ProcSpec(name=f"P_{pattern}", pattern=pattern)
        material = procedural.build_material(spec)
        check(
            len(nodes_of_type(material, node_type)) == 1,
            f"the {pattern} pattern builds a {node_type}",
        )


def part_overrides(procedural):
    print("\n== the overrides")
    material = procedural.build_from_recipe(
        "plastic", "BluePlastic", color_a="#003366", roughness=0.2
    )
    check(material is not None, "the material was built")
    if material is None:
        return
    tree = material.node_tree
    ramp = nodes_of_type(material, "VALTORGB")
    check(len(ramp) == 1, "the ramp is there")
    if ramp:
        first = ramp[0].color_ramp.elements[0]
        check(
            abs(first.color[2] - 0.4) < 0.02,
            f"the colour override was applied ({first.color[2]:.3f})",
        )
    rough_nodes = nodes_of_type(material, "MAP_RANGE")
    if rough_nodes:
        to_min = rough_nodes[0].inputs["To Min"].default_value
        to_max = rough_nodes[0].inputs["To Max"].default_value
        check(
            abs((to_min + to_max) * 0.5 - 0.2) < 0.01,
            f"the roughness override was applied ({to_min:.2f}..{to_max:.2f})",
        )
    elif bsdf is not None:
        check(
            abs(bsdf.inputs["Roughness"].default_value - 0.2) < 0.01,
            "the roughness override was applied",
        )


def part_tools(procedural):
    print("\n== the tools")
    tools = importlib.import_module("blender_addon.tools")
    check(tools.get("procedural.list") is not None, "procedural.list is registered")
    check(tools.get("procedural.build") is not None, "procedural.build is registered")

    result = tools.execute("procedural.list")
    check(result.ok, "the list tool runs")
    check("wood" in result.report, f"the list tool answers ({result.report[:60]})")

    result = tools.execute("procedural.build", {"recipe": "marble", "name": "ToolMarble"})
    check(result.ok, "the build tool runs")
    check("ToolMarble" in result.report, f"the build tool reports ({result.report[:70]})")
    check(
        bpy.data.materials.get("ToolMarble") is not None,
        "the material is really in the file",
    )

    result = tools.execute("procedural.build", {"recipe": "no_such_recipe"})
    check(result.ok, "an unknown recipe is answered, not crashed")
    check("no_such_recipe" in result.report, "the answer names the recipe")


def part_menu():
    print("\n== the node editor context menu")
    nodemenu = importlib.import_module("blender_addon.nodemenu")
    check(
        nodemenu.draw_node_menu in bpy.types.NODE_MT_node._dyn_ui_initialize(),
        "the menu was extended",
    )


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        procedural = importlib.import_module("blender_addon.procedural")

        part_recipes(procedural)
        part_matching(procedural)
        part_build(procedural)
        part_patterns(procedural)
        part_overrides(procedural)
        part_tools(procedural)
        part_menu()
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
