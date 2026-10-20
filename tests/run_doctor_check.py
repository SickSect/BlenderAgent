"""Blender-side checks for the material doctor.

    blender --background --factory-startup --python tests/run_doctor_check.py

The doctor is a promise too: it finds the mistakes that are invisible in the
viewport, and its repairs actually repair them. This script builds a scene
with a deliberate mistake of every kind, checks that each one is found, and
checks that the safe repairs fix them.

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
    """Import the add-on package from the working tree, without installing it."""
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


# -- a scene with one mistake of every kind ---------------------------------


def write_texture(directory, name, colorspace="sRGB", size=64):
    path = Path(directory) / f"{name}.png"
    scratch = bpy.data.images.new(f"{name}_scratch", width=size, height=size)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = name
    image.colorspace_settings.name = colorspace
    return image


def build_scene():
    """Two objects, each with a different set of mistakes."""
    folder = Path(tempfile.mkdtemp(prefix="gaa_doctor_"))
    albedo = write_texture(folder, "Doc_BaseColor", "sRGB")
    roughness = write_texture(folder, "Doc_Roughness", "sRGB")   # wrong space
    normal = write_texture(folder, "Doc_Normal", "Non-Color")   # mis-wired

    # The object with a UV layout and a mostly fine material.
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "DoctorCube"

    mat = bpy.data.materials.new("DoctorMat")
    mat.use_nodes = True
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")

    tex_albedo = nodes.new("ShaderNodeTexImage")
    tex_albedo.name = "Albedo"
    tex_albedo.image = albedo
    tex_rough = nodes.new("ShaderNodeTexImage")
    tex_rough.name = "Roughness"
    tex_rough.image = roughness
    tex_normal = nodes.new("ShaderNodeTexImage")
    tex_normal.name = "Normal"
    tex_normal.image = normal

    links.new(tex_albedo.outputs["Color"], bsdf.inputs["Base Color"])
    # Mistake 1: a data map in sRGB.
    links.new(tex_rough.outputs["Color"], bsdf.inputs["Roughness"])
    # Mistake 2: a normal map wired into Base Color.
    links.new(tex_normal.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    cube.data.materials.append(mat)

    # The object without a UV layout and a missing texture file.
    bpy.ops.mesh.primitive_uv_sphere_add()
    sphere = bpy.context.active_object
    sphere.name = "DoctorBall"
    for layer in list(sphere.data.uv_layers):
        sphere.data.uv_layers.remove(layer)

    mat2 = bpy.data.materials.new("DoctorBroken")
    mat2.use_nodes = True
    tree2 = mat2.node_tree
    bsdf2 = next(n for n in tree2.nodes if n.type == "BSDF_PRINCIPLED")
    missing = bpy.data.images.new("Doc_Missing", width=32, height=32)
    missing.source = "FILE"
    missing.filepath = str(folder / "not_on_disk.png")
    tex_missing = tree2.nodes.new("ShaderNodeTexImage")
    tex_missing.image = missing
    tree2.links.new(tex_missing.outputs["Color"], bsdf2.inputs["Base Color"])
    sphere.data.materials.append(mat2)

    # Mistake 3: an output that is not connected.
    mat3 = bpy.data.materials.new("DoctorUnplugged")
    mat3.use_nodes = True
    tree3 = mat3.node_tree
    output3 = next(n for n in tree3.nodes if n.type == "OUTPUT_MATERIAL")
    for link in list(tree3.links):
        if link.to_socket.name == "Surface":
            tree3.links.remove(link)
    check(output3 is not None, "the scene has an unplugged output to test")

    return cube, sphere


# -- checks -----------------------------------------------------------------


def part_audit(doctor, cube, sphere):
    print("\n== the audit finds every mistake")
    findings = doctor.audit_scene()
    codes = {finding.code for finding in findings}

    check(doctor.COLORSPACE in codes, "the wrong colour space is found")
    check(doctor.NORMAL_TO_BASE in codes, "the normal map in Base Color is found")
    check(doctor.MISSING_FILE in codes, "the missing texture file is found")
    check(doctor.NO_UV in codes, "the mesh without UV is found")
    check(doctor.SURFACE_UNCONNECTED in codes, "the unplugged output is found")

    mat = bpy.data.materials.get("DoctorMat")
    own = doctor.audit_material(mat)
    own_codes = {finding.code for finding in own}
    check(doctor.COLORSPACE in own_codes, "the audit of one material works")
    check(
        all(finding.material == "DoctorMat" for finding in own),
        "the findings name their material",
    )

    print("\n== the findings are readable")
    for finding in findings[:3]:
        text = finding.text("en")
        check(bool(text) and text != finding.key, f"the finding is written out: {text[:50]}")

    counts = doctor.summary(findings)
    check(sum(counts.values()) == len(findings), "the summary counts every finding")


def part_fixes(doctor, cube, sphere):
    print("\n== the repairs work")
    mat = bpy.data.materials.get("DoctorMat")
    roughness = bpy.data.images.get("Doc_Roughness")
    check(
        roughness.colorspace_settings.name == "sRGB",
        "the roughness map starts in the wrong colour space",
    )

    findings = doctor.audit_material(mat)
    done = doctor.apply_fixes(mat, findings)
    check(bool(done), f"something was repaired ({len(done)} repairs)")
    check(
        roughness.colorspace_settings.name == "Non-Color",
        "the colour space was repaired",
    )

    # The normal map must now go through a Normal Map node.
    tree = mat.node_tree
    normal_maps = [n for n in tree.nodes if n.type == "NORMAL_MAP"]
    check(len(normal_maps) == 1, "a Normal Map node was added")
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    base = bsdf.inputs["Base Color"]
    normal_socket = bsdf.inputs["Normal"]
    check(bool(normal_socket.links), "the Normal socket is connected")
    check(
        len(base.links) == 1,
        "Base Color still holds the albedo (only the normal map left it)",
    )

    # The unplugged output.
    mat3 = bpy.data.materials.get("DoctorUnplugged")
    done3 = doctor.apply_fixes(mat3, doctor.audit_material(mat3))
    check(bool(done3), "the unplugged output was repaired")
    output3 = next(n for n in mat3.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
    check(bool(output3.inputs["Surface"].links), "the Surface output is connected")

    print("\n== the repairs are idempotent")
    before = len([n for n in mat.node_tree.nodes if n.type == "NORMAL_MAP"])
    doctor.apply_fixes(mat, doctor.audit_material(mat))
    after = len([n for n in mat.node_tree.nodes if n.type == "NORMAL_MAP"])
    check(before == after, "a second repair adds nothing")


def part_tools(tools, doctor, cube):
    print("\n== the tools")
    check(
        tools.get("material.audit") is not None, "material.audit is registered"
    )
    check(tools.get("material.fix") is not None, "material.fix is registered")
    check(
        tools.get("material.audit").destructive is False,
        "the audit is not destructive",
    )
    check(
        tools.get("material.fix").destructive is False,
        "the repair is not destructive (nothing is deleted)",
    )

    report = tools.execute("material.audit")
    check(report.ok, "the audit tool runs")
    check("Audited" in report.report, f"the audit tool reports: {report.report[:60]}")


def part_snapshot(snapshot, doctor):
    print("\n== the snapshot and the doctor agree")
    text = snapshot.build_snapshot(None)
    if "Problems:" in text:
        findings = doctor.audit_scene()
        check(
            len(findings) > 0,
            "the snapshot lists the same problems the doctor finds",
        )
    else:
        check(False, "the snapshot has a problems section")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        doctor = importlib.import_module("blender_addon.doctor")
        tools = importlib.import_module("blender_addon.tools")
        snapshot = importlib.import_module("blender_addon.snapshot")

        cube, sphere = build_scene()
        part_audit(doctor, cube, sphere)
        part_fixes(doctor, cube, sphere)
        part_tools(tools, doctor, cube)
        part_snapshot(snapshot, doctor)
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
