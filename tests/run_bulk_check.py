"""Blender-side checks for the mass operations.

    blender --background --factory-startup --python tests/run_bulk_check.py

Assigning a material to eighty objects, swapping one material for another,
finding out who still uses the old one — these are bookkeeping, and
bookkeeping is what code is for. This script checks that every operation does
what it says and leaves the scene consistent.

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


# -- a scene ---------------------------------------------------------------


def build_scene():
    """Three cubes: one with the "old" material, two with the "new" one."""
    old = bpy.data.materials.new("OldMat")
    old.use_nodes = True
    new = bpy.data.materials.new("NewMat")
    new.use_nodes = True

    names = []
    for index in range(3):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        cube.name = f"BulkCube{index}"
        cube.data.materials.append(old if index == 0 else new)
        names.append(cube.name)
    return old, new, names


# -- checks -----------------------------------------------------------------


def part_assign(objects, old, new):
    print("\n== assign to selection")
    cubes = [obj for obj in bpy.context.scene.objects if obj.name.startswith("BulkCube")]
    for cube in cubes:
        cube.select_set(False)
    cubes[0].select_set(True)
    cubes[1].select_set(True)
    bpy.context.view_layer.objects.active = cubes[0]

    changed = objects.assign_to_selection(old)
    check(len(changed) == 2, f"two selected objects were touched ({changed})")
    check(
        cubes[1].data.materials[0] == old,
        "the selected object got the material",
    )

    # An object with two slots keeps the assignment in its active slot.
    cubes[2].data.materials.append(new)
    cubes[2].active_material_index = 1
    cubes[2].select_set(True)
    changed2 = objects.assign_to_selection(old)
    check(
        cubes[2].data.materials[1] == old,
        "the active slot was replaced, not appended",
    )
    check(
        cubes[2].data.materials[0] == new,
        "the other slot was left alone",
    )
    check(len(cubes[2].data.materials) == 2, "no extra slot was added")


def part_find_users(objects, old):
    print("\n== find users")
    users = objects.find_users(old)
    check(len(users) == 3, f"three users found ({users})")
    for name, index, faces in users:
        check(index == 0 or index == 1, "the slot index is reported")
    total_faces = sum(faces for _, _, faces in users)
    # Two cubes use the material on their faces, the third only has it in an
    # unused slot - a slot can exist without any face pointing at it.
    check(total_faces == 12, f"the face counts are right ({total_faces})")
    check(
        any(faces == 0 for _, _, faces in users),
        "a slot without faces is reported as zero",
    )


def part_replace(objects, old, new):
    print("\n== replace everywhere")
    changed = objects.replace_everywhere(old, new)
    check(len(changed) == 3, f"three objects changed ({changed})")
    for cube in bpy.context.scene.objects:
        if cube.name.startswith("BulkCube"):
            check(
                all(slot != old for slot in cube.data.materials),
                f"{cube.name} no longer uses the old material",
            )
    check(
        objects.replace_everywhere(old, new) == [],
        "replacing again changes nothing",
    )


def part_instance(objects):
    print("\n== instances")
    cubes = [obj for obj in bpy.context.scene.objects if obj.name.startswith("BulkCube")]
    source, target = cubes[0], cubes[1]
    check(source.data is not target.data, "the cubes start with separate meshes")
    changed = objects.make_instance(source, [target])
    check(changed == [target.name], f"the target became an instance ({changed})")
    check(target.data is source.data, "the mesh data is shared now")

    # Editing the shared mesh changes both.
    before = len(source.data.vertices)
    source.data.vertices[0].co.z += 0.5
    check(
        len(target.data.vertices) == before,
        "the instance has the same mesh",
    )

    single = objects.make_single_user([target])
    check(single == [target.name], "the instance was broken again")
    check(target.data is not source.data, "the mesh data is separate now")


def part_variant(objects, materials_module, new):
    print("\n== variants")
    master = bpy.data.materials.get("NewMat")
    variant = objects.make_variant(master, "NewMat_Red", (1.0, 0.4, 0.4, 1.0))
    check(variant is not None, "the variant was created")
    check(variant.name == "NewMat_Red", "the variant is named")
    check(variant != master, "the variant is a different material")

    tree = variant.node_tree
    tint_nodes = [n for n in tree.nodes if n.type == "MIX" and n.name == "Variant Tint"]
    check(len(tint_nodes) == 1, "the tint node was inserted")
    if tint_nodes:
        mix = tint_nodes[0]
        check(mix.blend_type == "MULTIPLY", "the tint multiplies")
        rgb = next((n for n in tree.nodes if n.type == "RGB"), None)
        check(rgb is not None, "the tint colour node was inserted")
        if rgb is not None:
            value = tuple(rgb.outputs["Color"].default_value)
            check(
                abs(value[0] - 1.0) < 1e-4 and abs(value[1] - 0.4) < 1e-4,
                f"the tint colour is the one asked for ({value})",
            )
    check(
        len([n for n in master.node_tree.nodes if n.name == "Variant Tint"]) == 0,
        "the master was not touched",
    )


def part_cleanup(objects, materials_module):
    print("\n== cleanup")
    orphan = bpy.data.materials.new("OrphanMat")
    check(orphan.users == 0, "the orphan material has no users")
    removed = objects.cleanup_orphans()
    check("OrphanMat" in removed, f"the orphan was removed ({removed})")
    check(bpy.data.materials.get("OrphanMat") is None, "it is really gone")


def part_tools(tools):
    print("\n== the tools")
    for name in (
        "materials.assign_to_selection",
        "materials.replace_everywhere",
        "materials.find_users",
        "materials.make_instance",
        "materials.make_single_user",
        "materials.make_variant",
        "materials.cleanup_orphans",
    ):
        check(tools.get(name) is not None, f"{name} is registered")
    check(
        tools.get("materials.cleanup_orphans").destructive is True,
        "the cleanup is destructive (it deletes data)",
    )
    for name in (
        "materials.assign_to_selection",
        "materials.replace_everywhere",
        "materials.make_variant",
    ):
        check(
            tools.get(name).destructive is False,
            f"{name} is not destructive",
        )


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        objects = importlib.import_module("blender_addon.objects")
        materials_module = importlib.import_module("blender_addon.materials")
        tools = importlib.import_module("blender_addon.tools")

        old, new, names = build_scene()
        part_assign(objects, old, new)
        part_find_users(objects, old)
        part_replace(objects, old, new)
        part_instance(objects)
        part_variant(objects, materials_module, new)
        part_cleanup(objects, materials_module)
        part_tools(tools)
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
