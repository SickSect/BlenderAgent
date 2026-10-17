"""Compact scene snapshot injected into every request's system prompt."""

from __future__ import annotations

import math

import bpy

MAX_OBJECTS = 40


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
        lines.append(f"... и ещё {len(objects) - max_objects} объектов")

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

    return "\n".join(lines)
