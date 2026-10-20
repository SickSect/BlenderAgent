"""Build a demo scene with deliberately broken materials.

    blender --background --factory-startup --python tools/demo_scene.py

The scene is built to make the add-on's new snapshot visible: every problem
the "Problems" section knows about is present on purpose.

* ``Wood_2k`` (on the crate)
    - the roughness map is still in ``sRGB`` — data read through the colour
      curve, the classic silent lighting bug;
    - the normal map is wired straight into ``Base Color`` instead of
      through a Normal Map node.
* ``Metal_Broken`` (on the ball)
    - ``Metal_Diffuse.png`` points at a file that is not on disk;
    - the ball has no UV layer at all.

Nothing is deleted: the demo lives in its own scene, so the default scene
stays untouched. The file is written next to the repository as
``demo/materials_demo.blend``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "demo"
OUT_FILE = OUT_DIR / "materials_demo.blend"

MISSING_TEX = "D:/textures/does_not_exist_Metal_Diffuse.png"
TEXTURE_DIR = OUT_DIR / "textures"


def write_texture(name, colorspace="sRGB", size=1024):
    """Write a real PNG to disk and load it back as a file-backed image.

    Real files matter: the add-on decides whether a texture is missing by
    looking at the filesystem, so a texture that only pretends to be a file
    would be reported as broken.
    """
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = TEXTURE_DIR / f"{name}.png"

    scratch = bpy.data.images.new(f"{name}_scratch", width=size, height=size)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)

    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = name
    image.colorspace_settings.name = colorspace
    return image


def broken_texture(name, filepath=MISSING_TEX, colorspace="sRGB"):
    """A file-backed image whose file is deliberately not on disk."""
    image = bpy.data.images.new(name, width=1024, height=1024)
    image.source = "FILE"
    image.filepath = filepath
    image.colorspace_settings.name = colorspace
    return image


def build_material_wood():
    """Albedo + roughness (wrong colour space) + a normal map mis-wired."""
    mat = bpy.data.materials.new("Wood_2k")
    mat.use_nodes = True
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links

    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.name = "Principled BSDF"
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")

    albedo = write_texture("Wood_BaseColor", colorspace="sRGB")
    roughness = write_texture("Wood_Roughness", colorspace="sRGB")  # WRONG on purpose
    normal = write_texture("Wood_Normal", colorspace="Non-Color")

    tex_albedo = nodes.new("ShaderNodeTexImage")
    tex_albedo.name = "Albedo"
    tex_albedo.image = albedo
    tex_albedo.location = (-800, 400)

    tex_rough = nodes.new("ShaderNodeTexImage")
    tex_rough.name = "Roughness"
    tex_rough.image = roughness
    tex_rough.location = (-800, 100)

    tex_normal = nodes.new("ShaderNodeTexImage")
    tex_normal.name = "Normal"
    tex_normal.image = normal
    tex_normal.location = (-800, -200)

    normal_map = nodes.new("ShaderNodeNormalMap")
    normal_map.name = "Normal Map"
    normal_map.location = (-500, -200)

    links.new(tex_albedo.outputs["Color"], bsdf.inputs["Base Color"])
    # Wrong colour space on purpose: the map is data, it must be Non-Color.
    links.new(tex_rough.outputs["Color"], bsdf.inputs["Roughness"])
    # Wrong wiring on purpose: a normal map belongs in a Normal Map node.
    links.new(tex_normal.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    return mat


def build_material_metal():
    """A texture file that is not on disk, plus a proper roughness map."""
    mat = bpy.data.materials.new("Metal_Broken")
    mat.use_nodes = True
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links

    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.name = "Principled BSDF"
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    bsdf.inputs["Metallic"].default_value = 1.0

    missing = broken_texture("Metal_Diffuse", MISSING_TEX, colorspace="sRGB")
    roughness = write_texture("Metal_Roughness", colorspace="Non-Color")

    tex_missing = nodes.new("ShaderNodeTexImage")
    tex_missing.name = "Diffuse"
    tex_missing.image = missing
    tex_missing.location = (-800, 300)

    tex_rough = nodes.new("ShaderNodeTexImage")
    tex_rough.name = "Roughness"
    tex_rough.image = roughness
    tex_rough.location = (-800, 0)

    links.new(tex_missing.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(tex_rough.outputs["Color"], bsdf.inputs["Roughness"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    return mat


def build_scene():
    """A new scene with a crate and a ball, both with broken materials."""
    scene = bpy.data.scenes.new("GAA Demo")
    bpy.context.window.scene = scene

    # The crate: a plain cube with a UV map.
    bpy.ops.mesh.primitive_cube_add(size=2, location=(-1.5, 0, 1))
    crate = bpy.context.active_object
    crate.name = "Crate"
    crate.data.materials.append(build_material_wood())

    # The ball: a sphere without any UV layout.
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(1.5, 0, 1))
    ball = bpy.context.active_object
    ball.name = "Ball"
    for layer in list(ball.data.uv_layers):
        ball.data.uv_layers.remove(layer)
    ball.data.materials.append(build_material_metal())

    # A floor, so the scene is not floating in the void.
    bpy.ops.mesh.primitive_plane_add(size=12, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "Floor"

    bpy.ops.object.select_all(action="DESELECT")
    crate.select_set(True)
    bpy.context.view_layer.objects.active = crate
    return scene


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_scene()

    # Save first, then make the texture paths relative to the .blend, then
    # save again - so the demo folder can be moved without losing textures.
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT_FILE))
    try:
        bpy.ops.file.make_paths_relative()
        bpy.ops.wm.save_mainfile()
    except Exception as exc:  # noqa: BLE001 - cosmetic, the demo still works
        print(f"could not make paths relative: {exc}")

    print(f"Saved: {OUT_FILE}")
    print(f"Scenes: {[s.name for s in bpy.data.scenes]}")
    print(f"Textures: {sorted(p.name for p in TEXTURE_DIR.glob('*.png'))}")


if __name__ == "__main__":
    main()
