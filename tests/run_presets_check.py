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
            key = preset.parameter_names()[0]
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

        print("\n== the tool")
        check(tools.get("procedural.preset") is not None, "procedural.preset is registered")
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
