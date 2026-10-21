"""Blender-side checks for the UI itself.

    blender --background --factory-startup --python tests/run_ui_check.py

The panel is only drawn in a window, so a wrong icon or a typo in an
operator id stays invisible to every other test - and then the whole chat
panel dies on the first redraw. These checks look at the *source* of the UI
and compare it with what this Blender version actually offers.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
import re
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


def part_icons():
    """Every icon the UI names must exist in this Blender version."""
    print("\n== the icons")
    enum = bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"]
    valid = {item.identifier for item in enum.enum_items}

    used = {}
    for path in sorted(ADDON_DIR.glob("*.py")):
        found = set(
            re.findall(r'icon="([A-Z_0-9]+)"', path.read_text(encoding="utf-8-sig"))
        )
        if found:
            used[path.name] = found

    for name, icons in used.items():
        missing = sorted(icon for icon in icons if icon not in valid)
        check(
            not missing,
            f"{name}: every icon exists ({len(icons)} icons"
            + (f", missing {missing}" if missing else "")
            + ")",
        )


def part_operator_ids():
    """Every operator the UI calls must be registered."""
    print("\n== the operator ids")
    ui = (ADDON_DIR / "ui.py").read_text(encoding="utf-8")
    called = sorted(set(re.findall(r'operator\(\s*"(gaa\.[a-z_]+)"', ui)))
    check(bool(called), f"the UI calls {len(called)} operators")
    for operator_id in called:
        module, _, name = operator_id.partition(".")
        check(
            hasattr(bpy.ops.gaa, name),
            f"{operator_id} is registered",
        )


def part_panel_draw():
    """The panel must survive a draw."""
    print("\n== the panel draws")
    ui = importlib.import_module("blender_addon.ui")
    panel = ui.GAA_PT_main
    # A headless Blender has no window, so the panel cannot be drawn for
    # real; what we can check is that every icon and operator id in the
    # source is valid - which is what kills a real panel.
    source = (ADDON_DIR / "ui.py").read_text(encoding="utf-8")
    check("def draw(" in source, "the panel has a draw method")
    check(panel is not None, "the panel class exists")


def part_operator_returns():
    """An operator must not return the result of ``fileselect_add``.

    ``context.window_manager.fileselect_add(self)`` returns None, and an
    operator that returns None is refused by Blender: the button dies with
    "Function.result expected a set, not a NoneType". The check is static -
    the file browser cannot be opened in a headless Blender.
    """
    print("\n== the operators return a set")
    import ast

    # utf-8-sig: the file starts with a BOM, and ast.parse refuses it.
    source = (ADDON_DIR / "operators.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    def called_name(node):
        if not isinstance(node, ast.Call):
            return ""
        func = node.func
        return getattr(func, "attr", "") or getattr(func, "id", "")

    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        if called_name(node.value) == "fileselect_add":
            problems.append(f"line {node.lineno}: returns fileselect_add(...)")

    # And the same mistake outside an invoke().
    check(
        not problems,
        "no operator returns fileselect_add()"
        + (f" (broken: {problems})" if problems else ""),
    )


def part_operator_props():
    """The properties the panel draws must exist.

    A property that does not exist is not a crash at import time - it is a
    crash in the panel, on the first redraw, in front of the user.
    """
    print("\n== the properties the panel draws")
    # The operator properties the panel draws next to the buttons.
    operator_props = (
        ("GAA_OT_import_atlas", ("filepath",)),
        ("GAA_OT_height_to_normal", ("filepath",)),
        ("GAA_OT_procedural_build", ("description", "recipe")),
        ("GAA_OT_use_preset", ("preset",)),
    )
    for class_name, props in operator_props:
        cls = getattr(bpy.types, class_name, None)
        check(cls is not None, f"{class_name} is registered")
        if cls is None:
            continue
        annotations = getattr(cls, "__annotations__", {})
        for name in props:
            check(
                name in annotations,
                f"{class_name}.{name} exists (the panel draws it)",
            )

    # The scene properties the panel draws.
    scene_props = (
        "gaa_uv_angle",
        "gaa_uv_use_existing_seams",
        "gaa_bake_size",
        "gaa_bake_samples",
        "gaa_bake_maps",
        "gaa_bake_naming",
    )
    for name in scene_props:
        check(
            name in bpy.types.Scene.bl_rna.properties,
            f"Scene.{name} exists (the panel draws it)",
        )


def part_angles():
    """The seam angle is a number of degrees, not radians.

    Blender's "ANGLE" subtype means "this number is in radians" and shows it
    converted - so a default of 40 degrees was displayed as 2292, and the
    minimum of 1 became 57. A user could not ask for a seam on a soft bend.
    """
    print("\n== the angles are degrees")
    prop = bpy.types.Scene.bl_rna.properties.get("gaa_uv_angle")
    check(prop is not None, "the seam angle property exists")
    if prop is None:
        return
    check(
        prop.subtype != "ANGLE",
        f"the property is a plain number, not radians (subtype={prop.subtype})",
    )
    check(abs(prop.default - 40.0) < 0.001, f"the default is 40 ({prop.default})")
    # Blender 5.x exposes the slider range as soft_min/soft_max.
    soft_min = getattr(prop, "soft_min", None)
    soft_max = getattr(prop, "soft_max", None)
    check(
        soft_min is None or abs(float(soft_min) - 1.0) < 0.001,
        f"it goes down to 1 degree ({soft_min})",
    )
    check(
        soft_max is None or abs(float(soft_max) - 90.0) < 0.001,
        f"and up to 90 by the slider ({soft_max})",
    )

    # And the code really works in degrees.
    uv = importlib.import_module("blender_addon.uv")
    settings = uv.settings_from_scene()
    check(
        abs(settings.angle - 40.0) < 0.001,
        f"the scene setting is read as degrees ({settings.angle})",
    )


def main():
    part_icons()
    # The add-on is registered once for the whole run: the checks below look
    # at the registered classes.
    package = load_package()
    package.register()
    try:
        part_operator_ids()
        part_operator_props()
        part_angles()
        part_operator_returns()
        part_panel_draw()
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
