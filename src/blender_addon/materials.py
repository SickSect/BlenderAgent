"""Build a PBR material from a folder of texture maps.

Three steps, each usable on its own (and testable without a file browser):

1. :func:`find_maps` — scan a folder and guess each file's role from its name
   (albedo/base, roughness, metallic, normal, ORM, emission, height);
2. :func:`load_images` — load them as ``bpy.data.images`` and fix the colour
   space (colour maps are ``sRGB``, data maps must be ``Non-Color``);
3. :func:`build_material` — wire them into a Principled BSDF node tree.

The UI operator (``operators.GAA_OT_pbr_from_folder``) only picks the folder and
calls these functions, so the interesting logic stays headless-friendly. The
same logic backs the ``material.build_from_folder`` tool, so the button and the
model-callable action are literally the same code.
"""

from __future__ import annotations

import os
import re

import bpy

from . import api, tools

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

# Custom property on a material: the folder it was built from. It makes the
# "PBR from folder" action repeatable — the second run updates the existing
# material instead of creating "Wood_2k.001".
SOURCE_FOLDER_KEY = "gaa_source_folder"

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


def _same_folder(first, second) -> bool:
    """True when the two paths point at the same folder.

    The comparison has to survive the spelling: a trailing slash or a
    different case must not make the add-on build "Wood.001" next to "Wood".
    """
    if not first or not second:
        return False
    try:
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )
    except (TypeError, ValueError, OSError):
        return str(first) == str(second)


def find_map_candidates(directory: str) -> dict:
    """``role -> [filepaths]`` for the recognised textures in *directory*.

    A folder can hold several candidates for one role: "Wood_albedo.png" and
    "Wood_col.png" are both the colour map. The first one in name order wins,
    and the others are reported, so the collision is never silent.
    """
    found: dict = {}
    if not directory or not os.path.isdir(directory):
        return found
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(IMAGE_EXTENSIONS):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        role = detect_role(name)
        if role is not None:
            found.setdefault(role, []).append(path)
    return found


def find_maps(directory: str) -> dict:
    """Map ``role -> filepath`` for recognised textures in *directory*.

    Files are visited in name order, so the first match wins when a folder
    holds several candidates for one role.
    """
    return {
        role: paths[0]
        for role, paths in find_map_candidates(directory).items()
    }
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
    return api.link(links, source, out_name, target, in_name, *aliases)


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


def assign_material(obj, material) -> str:
    """Put ``material`` on ``obj``; return the object's name or ``""``.

    Replaces the active slot when the object already has materials (what an
    artist expects when re-texturing) and appends otherwise. Objects without a
    material list (empties, lights, ...) are left alone.
    """
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return ""
    if len(slots):
        index = getattr(obj, "active_material_index", 0)
        if not 0 <= index < len(slots):
            index = 0
        slots[index] = material
    else:
        slots.append(material)
    return obj.name


def build_pbr_from_folder(directory: str, assign_to=None):
    """Build a PBR material from the texture maps in ``directory``.

    Returns ``(material, maps, assigned_name, reused)``. ``assigned_name`` is
    empty when no object was given or when the object cannot hold a material;
    ``reused`` says whether an existing material was updated instead of a new
    one being created.

    Calling it twice with the same folder must not leave two "Wood_2k"
    materials behind, so the material remembers which folder it came from
    (a custom property) and is reused when the folder matches.

    This is the whole "PBR from folder" feature as one headless-friendly
    function: the button and the model-callable tool both call it.
    """
    maps = find_maps(directory)
    if not maps:
        raise ValueError(f"no texture maps found in {directory}")
    images = load_images(maps)
    name = material_name_for(directory)

    material = bpy.data.materials.get(name)
    reused = material is not None and _same_folder(
        material.get(SOURCE_FOLDER_KEY), directory
    )
    if reused:
        # The same folder was already built here: refresh the textures
        # instead of piling up "Wood_2k.001" duplicates.
        for image in images.values():
            try:
                image.reload()
            except Exception:  # noqa: BLE001 - a locked file is not fatal
                pass
    else:
        material = build_material(name, images)
        material[SOURCE_FOLDER_KEY] = directory

    assigned = assign_material(assign_to, material) if assign_to is not None else ""
    return material, maps, assigned, reused


# --- the tool --------------------------------------------------------------
#
# Registering the action here means the button and the model call the very
# same code - there is no second implementation to keep in sync.


@tools.tool(
    "material.build_from_folder",
    "PBR from folder",
    "Build a PBR material from a folder of texture maps (albedo, normal, "
    "roughness, metallic, ORM, emission, height) and assign it to the active "
    "object. Call this instead of writing bpy code.",
    params=("folder",),
)
def build_from_folder_tool(folder=None, **kwargs) -> str:
    """Tool entry point: build a PBR material from a texture folder."""
    if not folder:
        return (
            "No folder was given. Ask the user for the path to the texture "
            "folder, or tell them to press the 'PBR from folder' button."
        )
    obj = getattr(bpy.context, "active_object", None) or getattr(
        bpy.context, "object", None
    )
    material, maps, assigned, reused = build_pbr_from_folder(
        folder, assign_to=obj
    )
    roles = ", ".join(ordered_roles(maps))
    if reused:
        report = f"Updated existing material '{material.name}' from {folder}"
    else:
        report = f"Built material '{material.name}' from {folder}"
    report += f" (maps: {roles})."
    # A folder can hold two files for one role ("Wood_albedo" and "Wood_col").
    # The first one wins - the collision has to be said out loud.
    collisions = {
        role: paths
        for role, paths in find_map_candidates(folder).items()
        if len(paths) > 1
    }
    for role, paths in collisions.items():
        names = ", ".join(os.path.basename(path) for path in paths)
        report += f" Note: {len(paths)} files match '{role}' ({names}) - took the first."
    if assigned:
        report += f" Assigned to '{assigned}'."
    else:
        report += " No active object to assign it to."
    return report
