"""Build a PBR material from a folder of texture maps.

Three steps, each usable on its own (and testable without a file browser):

1. :func:`find_maps` — scan a folder and guess each file's role from its name
   (albedo/base, roughness, metallic, normal, ORM, emission, height);
2. :func:`load_images` — load them as ``bpy.data.images`` and fix the colour
   space (colour maps are ``sRGB``, data maps must be ``Non-Color``);
3. :func:`build_material` — wire them into a Principled BSDF node tree.

The UI operator (``operators.GAA_OT_pbr_from_folder``) only picks the folder and
calls these functions, so the interesting logic stays headless-friendly.
"""

from __future__ import annotations

import os
import re

import bpy

# Role -> tokens that identify it in a file name. Matching is token based: the
# name is split on non-alphanumerics ("T_Wood_BaseColor_2k" -> t, wood,
# basecolor, 2k) and a role matches when one of its tokens is present. Tokens
# are checked in this order, first match wins.
MAP_RULES = (
    ("normal", ("normal", "norm", "nrm", "nor")),
    ("base", ("basecolor", "base", "albedo", "alb", "diffuse", "diff", "dif", "col", "color", "bc")),
    ("rough", ("roughness", "rough", "rgh", "rghness")),
    ("metal", ("metallic", "metalness", "metal", "mtl", "met")),
    ("orm", ("orm", "rma", "mra")),
    ("emit", ("emission", "emissive", "emiss", "emit")),
    ("height", ("height", "displacement", "disp", "hgt")),
)

# Single-letter markers are only trusted as the *last* token: packs write
# "Wood_d"/"Wood_n" (diffuse/normal) but also "M_Rock_disp", where a leading
# "m" means "material", not "metallic".
SUFFIX_RULES = {
    "d": "base",
    "n": "normal",
    "r": "rough",
    "m": "metal",
    "h": "height",
    "e": "emit",
}

# Colour space per role: only colour data is sRGB, everything else is data.
MAP_COLORSPACE = {
    "base": "sRGB",
    "emit": "sRGB",
    "normal": "Non-Color",
    "rough": "Non-Color",
    "metal": "Non-Color",
    "orm": "Non-Color",
    "height": "Non-Color",
}

IMAGE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".tga", ".bmp", ".webp",
)

# Canonical report order (base first: it is the map people care about most).
MAP_ORDER = ("base", "normal", "rough", "metal", "orm", "emit", "height")

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def ordered_roles(maps: dict):
    """Roles of ``maps`` in :data:`MAP_ORDER`, unknown ones appended sorted."""
    known = [role for role in MAP_ORDER if role in maps]
    extra = sorted(role for role in maps if role not in MAP_ORDER)
    return known + extra


def _tokens(name: str) -> list:
    stem = os.path.splitext(name)[0].lower()
    return [token for token in _TOKEN_SPLIT.split(stem) if token]


def detect_role(filename: str):
    """Best-guess map role for a file name, or ``None`` if unknown."""
    tokens = _tokens(filename)
    if not tokens:
        return None
    present = set(tokens)
    for role, keywords in MAP_RULES:
        if present & set(keywords):
            return role
    return SUFFIX_RULES.get(tokens[-1])


def find_maps(directory: str) -> dict:
    """Map ``role -> filepath`` for recognised textures in ``directory``.

    Files are visited in name order, so the first match wins when a folder
    holds several candidates for one role.
    """
    maps = {}
    if not directory or not os.path.isdir(directory):
        return maps
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(IMAGE_EXTENSIONS):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        role = detect_role(name)
        if role is not None and role not in maps:
            maps[role] = path
    return maps


def load_images(maps: dict) -> dict:
    """Load ``role -> filepath`` as images, fixing each one's colour space."""
    images = {}
    for role, path in maps.items():
        image = bpy.data.images.load(path, check_existing=True)
        try:
            image.colorspace_settings.name = MAP_COLORSPACE.get(role, "sRGB")
        except Exception:  # noqa: BLE001 — some sources refuse a colour space
            pass
        images[role] = image
    return images


def material_name_for(directory: str) -> str:
    """Material name derived from the folder (``.../Wood_2k`` -> ``Wood_2k``)."""
    name = os.path.basename(os.path.normpath(directory))
    return name or "PBR Material"


def _link(links, source, out_name, target, in_name, *aliases) -> bool:
    """Connect ``source.outputs[out_name]`` to ``target.inputs[in_name]``.

    ``aliases`` cover socket renames across Blender versions ("Emission Color"
    vs the older "Emission"). Returns ``False`` when a socket is missing instead
    of raising, so a partial texture set still produces a material.
    """
    out = source.outputs.get(out_name)
    if out is None:
        return False
    for name in (in_name,) + aliases:
        inp = target.inputs.get(name)
        if inp is not None:
            links.new(out, inp)
            return True
    return False


def _new_node(nodes, node_type: str, location):
    node = nodes.new(node_type)
    node.location = location
    return node


def build_material(name: str, images: dict):
    """Create a material from ``role -> image`` and return it.

    Roles: base -> Base Color, rough -> Roughness, metal -> Metallic,
    normal -> Normal Map, orm -> Separate Color (G=rough, B=metal),
    emit -> Emission Color, height -> Displacement.
    """
    material = bpy.data.materials.new(name)
    tree = getattr(material, "node_tree", None)
    if tree is None:
        material.use_nodes = True
        tree = material.node_tree
    nodes, links = tree.nodes, tree.links

    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = _new_node(nodes, "ShaderNodeBsdfPrincipled", (0, 0))
    output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = _new_node(nodes, "ShaderNodeOutputMaterial", (300, 0))
    if not output.inputs["Surface"].links:
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    x, y = -900, 300
    for role, image in images.items():
        texture = _new_node(nodes, "ShaderNodeTexImage", (x, y))
        texture.image = image
        _connect(nodes, links, bsdf, output, role, texture, (x + 300, y))
        y -= 320
    return material


def _connect(nodes, links, bsdf, output, role, texture, helper_location) -> None:
    """Wire one texture node to the shader according to its role."""
    if role == "base":
        _link(links, texture, "Color", bsdf, "Base Color")
    elif role == "rough":
        _link(links, texture, "Color", bsdf, "Roughness")
    elif role == "metal":
        _link(links, texture, "Color", bsdf, "Metallic")
    elif role == "emit":
        _link(links, texture, "Color", bsdf, "Emission Color", "Emission")
    elif role == "normal":
        node = _new_node(nodes, "ShaderNodeNormalMap", helper_location)
        _link(links, texture, "Color", node, "Color")
        _link(links, node, "Normal", bsdf, "Normal")
    elif role == "orm":
        node = _new_node(nodes, "ShaderNodeSeparateColor", helper_location)
        _link(links, texture, "Color", node, "Color")
        _link(links, node, "Green", bsdf, "Roughness")
        _link(links, node, "Blue", bsdf, "Metallic")
    elif role == "height":
        node = _new_node(nodes, "ShaderNodeDisplacement", helper_location)
        _link(links, texture, "Color", node, "Height")
        _link(links, node, "Displacement", output, "Displacement")
