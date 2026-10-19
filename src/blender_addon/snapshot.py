"""Compact scene snapshot injected into every request's system prompt."""

from __future__ import annotations

import math

import bpy

from . import i18n

MAX_OBJECTS = 40
MAX_MATERIALS = 40
MAX_IMAGES = 20
MAX_NODES = 8

# Images Blender always keeps around; they carry no user texture meaning.
_IMAGE_BLACKLIST = {"Render Result", "Viewer Node"}


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
                    extras.append(f"transmission={_fmt_value(transmission.default_value)}")
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


def _describe_image(image) -> str:
    flags = []
    if image.packed_file is not None:
        flags.append("packed")
    if image.colorspace_settings.name != "sRGB":
        flags.append(image.colorspace_settings.name)
    if image.source == "FILE" and not image.has_data:
        flags.append("missing")
    suffix = (" " + " ".join(flags)) if flags else ""
    width, height = image.size
    return f"- {image.name} {width}x{height} src={image.source.lower()}{suffix}"


def _material_lines() -> list:
    try:
        materials = list(bpy.data.materials)
    except Exception:
        return []
    if not materials:
        return []
    lines = [f"Materials: {len(materials)}"]
    for mat in materials[:MAX_MATERIALS]:
        lines.append(_describe_material(mat))
    if len(materials) > MAX_MATERIALS:
        lines.append(
            i18n.T("snapshot.more_materials", n=len(materials) - MAX_MATERIALS)
        )
    return lines


def _image_lines() -> list:
    try:
        images = [img for img in bpy.data.images if img.name not in _IMAGE_BLACKLIST]
    except Exception:
        return []
    if not images:
        return []
    lines = [f"Images: {len(images)}"]
    for image in images[:MAX_IMAGES]:
        lines.append(_describe_image(image))
    if len(images) > MAX_IMAGES:
        lines.append(i18n.T("snapshot.more_images", n=len(images) - MAX_IMAGES))
    return lines


def build_snapshot(max_objects: int = MAX_OBJECTS) -> str:
    """Short human/model-readable description of the current scene."""
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
    for obj in objects[:max_objects]:
        lines.append(_describe(obj))
    if len(objects) > max_objects:
        lines.append(i18n.T("snapshot.more_objects", n=len(objects) - max_objects))

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

    lines.extend(_material_lines())
    lines.extend(_image_lines())

    return "\n".join(lines)
