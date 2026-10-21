"""Blender-side checks for the material presets.

    blender --background --factory-startup --python tests/run_presets_check.py

A preset is a hand-written script that builds a material. The add-on reads its
parameters, shows them to the model and overrides the ones the user cares
about. This checks that every preset really builds - a preset that only works
with the mouse open is a preset nobody can use.
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


def part_correction(state, presets):
    print("\n== the user's words win over the model's guess")
    chat = state.get_state()
    chat.turn_user_text = "сделай процедурный материал поцарапанного металла"

    wrong = chat._correct_preset({"preset": "metal_worn"})
    check(
        wrong.get("preset") == "metal_scratched",
        f"the guess is corrected ({wrong.get('preset')})",
    )
    right = chat._correct_preset({"preset": "metal_scratched"})
    check(
        right.get("preset") == "metal_scratched",
        "a correct guess is left alone",
    )
    chat.turn_user_text = "сделай ржавый металл"
    check(
        chat._correct_preset({"preset": "metal_worn"}).get("preset") == "rusty_metal",
        "another request corrects to another preset",
    )
    chat.turn_user_text = ""


def part_matching(presets):
    print("\n== the matching")
    for text, expected in (
        ("поцарапанного металла", "metal_scratched"),
        ("потёртый металл", "metal_worn"),
        ("ржавый металл", "rusty_metal"),
        ("чистый металл", "metal_clean"),
        ("дубовое дерево", "wood_oak"),
        ("сосновый", "wood_light"),
        ("тёмное дерево", "wood_dark"),
        ("сухое выветренное дерево", "wood_dry"),
        ("кора", "wood_dry_peeling_bark"),
        ("чипборд", "wood_chipboard"),
    ):
        found = presets.match(text)
        names = [preset.name for preset, _score in found]
        check(
            names and names[0] == expected,
            f"'{text}' finds {expected} ({names})",
        )


def main():
    package = load_package()
    package.register()
    try:
        presets = importlib.import_module("blender_addon.presets")
        tools = importlib.import_module("blender_addon.tools")

        found = presets.all_presets()
        print(f"\n== the presets ({len(found)})")
        check(bool(found), "there are presets")
        for preset in found:
            print(f"   {preset.name}: {preset.title}")
            print(f"      parameters: {', '.join(preset.parameter_names())}")

        check(
            all(preset.parameters for preset in found),
            "every preset exposes parameters the model can turn",
        )
        catalogue = presets.catalog()
        check(
            all(preset.name in catalogue for preset in found),
            "the catalogue names every preset",
        )

        print("\n== every preset builds")
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        for preset in found:
            material, report = presets.build(preset.name, f"Preset_{preset.name}")
            check(material is not None, f"{preset.name} builds ({report[:70]})")
            if material is None:
                continue
            check(
                material.name == f"Preset_{preset.name}",
                f"{preset.name} took the requested name ({material.name})",
            )
            nodes = [n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"]
            check(bool(nodes), f"{preset.name} has a Principled BSDF")
            check(
                len(material.node_tree.nodes) > 5,
                f"{preset.name} built a real graph "
                f"({len(material.node_tree.nodes)} nodes)",
            )
            check(
                cube.data.materials and cube.data.materials[0] == material,
                f"{preset.name} is on the object",
            )

        print("\n== the parameters can be turned")
        preset = found[0] if found else None
        if preset is not None and preset.parameters:
            # A number parameter: the others can be colours or lists, and
            # the type has to match (the tool refuses a mismatch).
            key = None
            for name in preset.parameter_names():
                current = getattr(presets._load_module(preset), name, None)
                if isinstance(current, (int, float)):
                    key = name
                    break
            check(key is not None, f"{preset.name} has a number parameter")
            if key is not None:
                before = presets.build(preset.name, "ParamTest")
                check(before[0] is not None, "the preset builds for the test")
                after, report = presets.build(
                    preset.name, "ParamTest", {key: 999.0}
                )
                check(after is not None, f"a changed parameter still builds ({report[:70]})")
                check(
                    key in report,
                    f"the report names the changed parameter ({report[-80:]})",
                )

            bad, message = presets.build(preset.name, "Bad", {"NO_SUCH_PARAM": 1})
            check(bad is None, "an unknown parameter is refused")
            check(
                "not a parameter" in message,
                f"the refusal explains itself ({message[:70]})",
            )

            # A number where a colour belongs must be refused, not crash.
            for name in preset.parameter_names():
                current = getattr(presets._load_module(preset), name, None)
                if isinstance(current, str):
                    wrong, message = presets.build(
                        preset.name, "Bad", {name: 999.0}
                    )
                    check(wrong is None, "a number for a colour is refused")
                    check(
                        "is a string" in message,
                        f"the refusal explains itself ({message[:70]})",
                    )
                    break

        print("\n== the tool")
        check(tools.get("procedural.preset") is not None, "procedural.preset is registered")
        check(
            tools.get("material.preset") is not None,
            "the alias 'material.preset' answers too",
        )
        result = tools.execute(
            "procedural.preset", {"preset": found[0].name, "name": "ToolPreset"}
        )
        check(result.ok, f"the tool builds ({result.report[:70]})")
        check(
            bpy.data.materials.get("ToolPreset") is not None,
            "the material is really in the file",
        )
        result = tools.execute("procedural.preset", {})
        check(result.ok, "the tool without a preset answers")
        check(
            found[0].name in result.report,
            "the answer lists the presets",
        )
        # The words of the request pick the preset.
        result = tools.execute(
            "procedural.preset", {"query": "поцарапанного металла"}
        )
        check(result.ok, "the tool takes a query")
        check(
            "metal_scratched" in result.report,
            f"the query found the preset ({result.report[:80]})",
        )

        part_matching(presets)
        part_correction(importlib.import_module("blender_addon.state"), presets)
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
