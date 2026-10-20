"""Compact scene snapshot injected into every request's system prompt.

The snapshot is the add-on's only channel for telling the model what Blender
actually looks like right now. Two properties matter:

* **cheap** — it rides along with every request, so it must stay small;
* **focused** — the model is usually working on *one* object or material, so
  the interesting thing deserves the detail and the rest gets trimmed.

That is why :func:`build_snapshot` takes the UI context: the editor the user
is sitting in decides what the snapshot zooms in on (a material tree, a
texture, or the whole scene).

Everything here is **read-only**: a snapshot can never break the scene.
"""

from __future__ import annotations

import math
import os

import bpy

from . import i18n

MAX_OBJECTS = 40
MAX_MATERIALS = 40
MAX_IMAGES = 20

# Nodes listed in the compact per-material summary.
MAX_NODES = 8
# Nodes/links listed in the *detailed* block for the focused material.
MAX_DETAIL_NODES = 40
MAX_DETAIL_LINKS = 40

# Images Blender always keeps around; they carry no user texture meaning.
_IMAGE_BLACKLIST = {"Render Result", "Viewer Node"}


# --- focus -----------------------------------------------------------------
#
# What the snapshot zooms in on. The value is also the key of FOCUS_LIMITS.

FOCUS_MATERIAL = "material"   # the user is in the Shader Editor
FOCUS_TEXTURES = "textures"   # the user is in the Image Editor
FOCUS_SCENE = "scene"         # the user is in the 3D viewport / elsewhere

# How much of the scene is worth describing in each focus. Detail is bought
# with breadth: focusing on a material trims the object and material lists.
FOCUS_LIMITS = {
    FOCUS_MATERIAL: {"objects": 15, "materials": 12, "images": 20},
    FOCUS_TEXTURES: {"objects": 15, "materials": 12, "images": 30},
    FOCUS_SCENE: {
        "objects": MAX_OBJECTS,
        "materials": MAX_MATERIALS,
        "images": MAX_IMAGES,
    },
}


def _vec(v) -> str:
    return f"({v.x:.2f}, {v.y:.2f}, {v.z:.2f})"


def _rot_deg(obj) -> str:
    euler = obj.rotation_euler
    return (
        f"({math.degrees(euler.x):.1f}, "
        f"{math.degrees(euler.y):.1f}, {math.degrees(euler.z):.1f})"
    )


def _describe(obj) -> str:
    line = (
        f"- {obj.name} [{obj.type}] loc={_vec(obj.location)} "
        f"rot_deg={_rot_deg(obj)} scale={_vec(obj.scale)}"
    )
    data = getattr(obj, "data", None)
    extras = []

    if obj.type == "MESH" and data is not None:
        extras.append(
            f"verts={len(data.vertices)} edges={len(data.edges)} "
            f"faces={len(data.polygons)}"
        )
        uv_layers = getattr(data, "uv_layers", None)
        if uv_layers is not None and len(uv_layers):
            extras.append("uv=" + ",".join(layer.name for layer in uv_layers[:4]))
        else:
            # No UV: a texture on this object would be stretched over every
            # face. Worth saying out loud - the model cannot see it.
            extras.append("uv=none")
        materials = getattr(data, "materials", None)
        if materials is not None and len(materials):
            names = [(m.name if m else "-") for m in materials[:6]]
            extras.append("materials=" + ",".join(names))
        vgroups = getattr(obj, "vertex_groups", None)
        if vgroups is not None and len(vgroups):
            extras.append(f"vgroups={len(vgroups)}")

    modifiers = getattr(obj, "modifiers", None)
    if modifiers is not None and len(modifiers):
        extras.append("modifiers=" + ",".join(m.type for m in modifiers[:8]))

    if getattr(obj, "hide_viewport", False):
        extras.append("hidden")

    return f"{line} {' | '.join(extras)}" if extras else line


def _fmt_value(value) -> str:
    """Compact text for a node input value (float, vector or RGBA)."""
    try:
        if hasattr(value, "__len__"):
            return "(" + ", ".join(f"{float(v):.2f}" for v in value) + ")"
        return f"{float(value):.3g}"
    except (TypeError, ValueError):
        return str(value)


def _socket(node, *names):
    """First existing input among *names*, or None.

    Socket names differ between Blender versions (e.g. "Specular" in 3.x vs
    "Specular IOR Level" in 4.x), so callers pass every known spelling.
    """
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            return socket
    return None


def _describe_material(mat) -> str:
    line = f"- {mat.name}"
    # ``use_nodes`` is deprecated (removed in Blender 6.0); default to True.
    if not getattr(mat, "use_nodes", True) or getattr(mat, "node_tree", None) is None:
        return f"{line} simple base={_fmt_value(mat.diffuse_color)}"

    nodes = list(mat.node_tree.nodes)
    types = [node.bl_idname.replace("ShaderNode", "") for node in nodes]
    summary = "nodes=" + ",".join(types[:MAX_NODES])
    if len(types) > MAX_NODES:
        summary += f"+{len(types) - MAX_NODES}"

    extras = [summary]
    bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bits = []
        for key, names in (
            ("base", ("Base Color",)),
            ("metal", ("Metallic",)),
            ("rough", ("Roughness",)),
        ):
            socket = _socket(bsdf, *names)
            if socket is not None:
                bits.append(f"{key}={_fmt_value(socket.default_value)}")
        if bits:
            extras.append(" ".join(bits))
        # Transmission is 0 for most materials; report it only when it matters
        # (glass-like shaders) so the line stays compact.
        transmission = _socket(bsdf, "Transmission Weight", "Transmission")
        if transmission is not None:
            try:
                if float(transmission.default_value) > 0.001:
                    extras.append(
                        f"transmission={_fmt_value(transmission.default_value)}"
                    )
            except (TypeError, ValueError):
                pass

    textures = []
    for node in nodes:
        if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
            image = node.image
            textures.append(f"{image.name}[{image.colorspace_settings.name}]")
    if textures:
        extras.append("tex=" + ",".join(textures[:MAX_IMAGES]))

    return f"{line} {' | '.join(extras)}"


def _image_file_missing(image) -> bool:
    """True when the file behind a file-backed image is gone.

    ``image.has_data`` is **not** a reliable signal: Blender loads file images
    lazily, so a perfectly good texture reports ``has_data == False`` until
    something asks for its pixels - and in background mode that never
    happens. Judging by that flag marks every texture as missing.

    The filesystem is the honest check, with one exception: a packed image
    keeps its data inside the .blend, so the file on disk does not matter.
    """
    if image.source != "FILE":
        return False
    if image.packed_file is not None:
        return False
    path = image.filepath
    if not path:
        return True
    try:
        absolute = bpy.path.abspath(path)
    except Exception:
        absolute = path
    return not os.path.exists(absolute)


def _describe_image(image) -> str:
    flags = []
    if image.packed_file is not None:
        flags.append("packed")
    if image.colorspace_settings.name != "sRGB":
        flags.append(image.colorspace_settings.name)
    if _image_file_missing(image):
        flags.append("missing")
    suffix = (" " + " ".join(flags)) if flags else ""
    width, height = image.size
    return f"- {image.name} {width}x{height} src={image.source.lower()}{suffix}"


# --- focus detection -------------------------------------------------------


def _space_type(context) -> str:
    space = getattr(context, "space_data", None)
    return getattr(space, "type", "") if space is not None else ""


def detect_focus(context) -> str:
    """Which of the three focuses the snapshot should take for *context*."""
    space_type = _space_type(context)
    if space_type == "NODE_EDITOR":
        return FOCUS_MATERIAL
    if space_type == "IMAGE_EDITOR":
        return FOCUS_TEXTURES
    return FOCUS_SCENE


def _editor_tree(context):
    """The node tree the Shader Editor is showing, or ``None``."""
    space = getattr(context, "space_data", None)
    return getattr(space, "node_tree", None)


def focus_material(context):
    """The material the snapshot should describe in detail, or ``None``.

    Priority: the tree open in the Shader Editor (that is literally what the
    user is looking at), then the active object's material slot.
    """
    tree = _editor_tree(context)
    if tree is not None:
        owner = getattr(tree, "id_data", None)
        if owner is not None and getattr(owner, "type", "") == "MATERIAL":
            return owner
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    return getattr(obj, "active_material", None)


def _editor_image(context):
    """The image shown in the Image Editor, or ``None``."""
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "type", "") != "IMAGE_EDITOR":
        return None
    return getattr(space, "image", None)


# --- detailed material block ----------------------------------------------


def _node_extras(node) -> str:
    """Type-specific facts about a node, as short ``key=value`` pairs."""
    bits = []
    node_type = node.type

    if node_type == "TEX_IMAGE":
        image = getattr(node, "image", None)
        if image is not None:
            width, height = image.size
            bits.append(f"image={image.name}")
            bits.append(f"{width}x{height}")
            bits.append(image.colorspace_settings.name)
        else:
            bits.append("image=none")
        for name in ("extension", "interpolation", "projection"):
            socket = node.inputs.get(name)
            if socket is not None and socket.type == "MENU":
                bits.append(f"{name}={socket.default_value}")
    elif node_type == "BSDF_PRINCIPLED":
        # Canonical socket names, not the short ones the compact summary uses:
        # the model writes code with these names, so it must see them verbatim.
        for key, names in (
            ("Base Color", ("Base Color",)),
            ("Metallic", ("Metallic",)),
            ("Roughness", ("Roughness",)),
        ):
            socket = _socket(node, *names)
            if socket is not None:
                bits.append(f"{key}={_fmt_value(socket.default_value)}")
    elif node_type == "NORMAL_MAP":
        socket = node.inputs.get("Strength")
        if socket is not None:
            bits.append(f"strength={_fmt_value(socket.default_value)}")
    elif node_type == "TEX_UV":
        socket = node.inputs.get("UV Map")
        if socket is not None:
            bits.append(f"uv_map={socket.default_value}")
    elif node_type == "MAPPING":
        socket = node.inputs.get("Location")
        if socket is not None:
            bits.append(f"location={_fmt_value(socket.default_value)}")
    elif node_type == "TEX_ENVIRONMENT":
        image = getattr(node, "image", None)
        if image is not None:
            bits.append(f"image={image.name}")
    elif node_type in ("MIX_RGB", "MIX"):
        socket = node.inputs.get("Factor")
        if socket is not None:
            bits.append(f"factor={_fmt_value(socket.default_value)}")
    elif node_type == "BSDF_GLASS":
        socket = node.inputs.get("IOR")
        if socket is not None:
            bits.append(f"ior={_fmt_value(socket.default_value)}")

    return " ".join(bits)


def _describe_node(node) -> str:
    """One line per node: type, name and the facts worth knowing."""
    label = getattr(node, "label", "") or ""
    head = node.bl_idname.replace("ShaderNode", "")
    extras = _node_extras(node)
    parts = [head]
    if label:
        parts.append(f"label={label!r}")
    if extras:
        parts.append(extras)
    return f"{node.name} [{node.type}] " + " ".join(parts)


def _describe_link(link) -> str:
    """One line per connection: source socket -> target socket."""
    return (
        f"{link.from_node.name}.{link.from_socket.name}"
        f" -> {link.to_node.name}.{link.to_socket.name}"
    )


def _material_users(mat):
    """Names of the objects that use *mat*."""
    users = []
    for obj in bpy.context.scene.objects:
        slots = getattr(getattr(obj, "data", None), "materials", None)
        if slots is None:
            continue
        for slot in slots:
            if slot is not None and slot == mat:
                users.append(obj.name)
                break
    return users


def _detail_material_lines(mat) -> list:
    """The detailed block for the focused material."""
    if mat is None:
        return []
    lines = [f"Material in focus: {mat.name}"]

    tree = getattr(mat, "node_tree", None)
    if tree is None:
        lines.append("  (no node tree - simple material)")
        return lines

    nodes = list(tree.nodes)
    links = list(tree.links)
    lines.append(f"  Nodes ({len(nodes)}):")
    for node in nodes[:MAX_DETAIL_NODES]:
        lines.append("    - " + _describe_node(node))
    if len(nodes) > MAX_DETAIL_NODES:
        lines.append(f"    ... and {len(nodes) - MAX_DETAIL_NODES} more nodes")

    lines.append(f"  Links ({len(links)}):")
    for link in links[:MAX_DETAIL_LINKS]:
        lines.append("    - " + _describe_link(link))
    if len(links) > MAX_DETAIL_LINKS:
        lines.append(f"    ... and {len(links) - MAX_DETAIL_LINKS} more links")

    textures = []
    for node in nodes:
        if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
            image = node.image
            textures.append(
                f"{image.name} ({image.colorspace_settings.name}, "
                f"{image.size[0]}x{image.size[1]})"
            )
    if textures:
        lines.append("  Textures: " + ", ".join(textures[:MAX_IMAGES]))

    users = _material_users(mat)
    if users:
        lines.append("  Used by: " + ", ".join(users[:12]))

    return lines


def _shader_editor_lines(context) -> list:
    """What the user is looking at in the Shader Editor."""
    tree = _editor_tree(context)
    if tree is None:
        return []
    nodes = tree.nodes
    active = getattr(nodes, "active", None)
    selected = [node for node in nodes if getattr(node, "select", False)]
    bits = [f'Shader editor: tree "{tree.name}"']
    if active is not None:
        bits.append(f"active node: {active.name} ({active.type})")
    if selected:
        names = ", ".join(node.name for node in selected[:6])
        bits.append(f"selected: {names}")
    return ["  " + " | ".join(bits)]


def _image_editor_lines(context) -> list:
    """What the user is looking at in the Image Editor."""
    image = _editor_image(context)
    if image is None:
        return []
    width, height = image.size
    return [
        f'  Image editor: "{image.name}" ({width}x{height}, '
        f"{image.colorspace_settings.name})"
    ]


# --- problems ---------------------------------------------------------------


def _problem_lines() -> list:
    """The problems of the scene, in the doctor's own words.

    The doctor (chapter 14) is the single source of truth: a problem is
    described the same way to the user and to the model. The text is English
    here because it is read by the model.
    """
    from . import doctor

    try:
        findings = doctor.audit_scene()
    except Exception:
        return []
    if not findings:
        return []
    lines = ["Problems:"]
    # Worst first: the model must not lose an error to a pile of notes.
    for finding in doctor.sorted_findings(findings)[:20]:
        lines.append("- " + finding.text("en"))
    return lines


# --- sections ---------------------------------------------------------------


def _material_lines(limit: int) -> list:
    try:
        materials = list(bpy.data.materials)
    except Exception:
        return []
    if not materials:
        return []
    lines = [f"Materials: {len(materials)}"]
    for mat in materials[:limit]:
        lines.append(_describe_material(mat))
    if len(materials) > limit:
        lines.append(i18n.T("snapshot.more_materials", n=len(materials) - limit))
    return lines


def _image_lines(limit: int) -> list:
    try:
        images = [img for img in bpy.data.images if img.name not in _IMAGE_BLACKLIST]
    except Exception:
        return []
    if not images:
        return []
    lines = [f"Images: {len(images)}"]
    for img in images[:limit]:
        lines.append(_describe_image(img))
    if len(images) > limit:
        lines.append(i18n.T("snapshot.more_images", n=len(images) - limit))
    return lines


def build_snapshot(context=None, max_objects: int | None = None) -> str:
    """Short human/model-readable description of the current scene.

    *context* is the UI context the request was made from. It decides the
    focus: in the Shader Editor the snapshot zooms in on the material being
    edited, in the Image Editor on the texture, in the 3D viewport on the
    scene. Without a context the snapshot falls back to the scene focus.
    """
    focus = detect_focus(context) if context is not None else FOCUS_SCENE
    limits = FOCUS_LIMITS.get(focus, FOCUS_LIMITS[FOCUS_SCENE])
    object_limit = max_objects if max_objects is not None else limits["objects"]

    scene = bpy.context.scene
    lines = [
        f"Blender {bpy.app.version_string}",
        (
            f"Scene: {scene.name} | fps={scene.render.fps} | "
            f"units={scene.unit_settings.system} | "
            f"render={scene.render.engine}"
        ),
    ]

    try:
        collections = [c.name for c in bpy.data.collections]
        if collections:
            lines.append("Collections: " + ", ".join(collections[:20]))
    except Exception:
        pass

    objects = list(scene.objects)
    lines.append(f"Objects: {len(objects)}")
    for obj in objects[:object_limit]:
        lines.append(_describe(obj))
    if len(objects) > object_limit:
        lines.append(i18n.T("snapshot.more_objects", n=len(objects) - object_limit))

    try:
        selected = [o.name for o in bpy.context.selected_objects]
    except Exception:
        selected = []
    lines.append("Selected: " + (", ".join(selected[:30]) or "-"))

    try:
        active = bpy.context.view_layer.objects.active
    except Exception:
        active = None
    if active is not None:
        lines.append(
            f"Active: {active.name} | mode={bpy.context.mode} | {_describe(active)}"
        )

    lines.extend(_material_lines(limits["materials"]))
    lines.extend(_image_lines(limits["images"]))

    # The detail block: what the user is actually looking at.
    detail = _detail_material_lines(focus_material(context))
    if detail:
        lines.extend(detail)
    editor_lines = _shader_editor_lines(context) + _image_editor_lines(context)
    if editor_lines:
        lines.extend(editor_lines)

    problems = _problem_lines()
    if problems:
        lines.extend(problems)

    uv_settings = _uv_settings_lines(context)
    if uv_settings:
        lines.extend(uv_settings)

    return "\n".join(lines)


def _uv_settings_lines(context) -> list:
    """The UV settings of the project - the model needs to know them."""
    from . import uv

    try:
        settings = uv.settings_from_scene(getattr(context, "scene", None))
    except Exception:
        return []
    return [
        "UV settings: "
        f"seams at {settings.angle:.0f} deg, "
        f"method {settings.method}, "
        f"gap {settings.margin:.3f}, "
        f"target density {settings.density:.0f} px/m, "
        f"atlas {settings.atlas}"
    ]
