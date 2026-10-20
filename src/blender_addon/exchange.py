"""The round trip with other programs: UV kit out, painted atlas back in.

The workflow an artist wants:

    Blender  →  UV kit  →  Aseprite / Krita  →  painted atlas  →  Blender

The kit is a folder:

    wood_atlas/
        layout.png          the UV layout, drawn by us
        manifest.json       the size, the gap, the density, the maps
        albedo_Crate.png    a copy of the current texture, if there is one

The kit is **self-describing**: the manifest says how big the atlas is and how
the islands are laid out, so a month later it is still obvious what the files
are and how they fit together.

Why the layout is drawn here and not by ``uv.export_layout``
------------------------------------------------------------

Blender's own operator needs a **live** Image Editor - it fails in
``--background``. A tool that only works with the mouse open is a tool that
cannot be tested. So the layout is rasterised here, on the bmesh data, which
means it works the same everywhere and is covered by the headless harness.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import bpy

from . import api, tools, uv

# The colours of the drawn layout.
FILL_COLOR = (0.62, 0.62, 0.62, 1.0)
LINE_COLOR = (0.10, 0.10, 0.10, 1.0)
BACKGROUND = (1.0, 1.0, 1.0, 1.0)

# The width of the island outline, in pixels.
LINE_WIDTH = 2.0


# --- drawing ---------------------------------------------------------------


def _triangles_uv(obj) -> List:
    """The UV triangles of *obj*, as lists of three (u, v) pairs."""
    mesh = getattr(obj, "data", None)
    if mesh is None or not getattr(mesh, "uv_layers", None):
        return []
    layer = mesh.uv_layers[0]
    triangles = []
    for polygon in mesh.polygons:
        points = [tuple(layer.data[i].uv) for i in polygon.loop_indices]
        for i in range(1, len(points) - 1):
            triangles.append((points[0], points[i], points[i + 1]))
    return triangles


def _boundary_edges_uv(obj) -> List:
    """The UV edges that lie on the border of an island."""
    mesh = getattr(obj, "data", None)
    if mesh is None or not getattr(mesh, "uv_layers", None):
        return []
    layer = mesh.uv_layers[0]
    edges = []
    for polygon in mesh.polygons:
        points = [tuple(layer.data[i].uv) for i in polygon.loop_indices]
        count = len(points)
        for i in range(count):
            a = points[i]
            b = points[(i + 1) % count]
            edges.append((a, b))
    return edges


def _fill_triangle(buffer, size, a, b, c, color) -> None:
    """Fill one triangle in a numpy buffer of pixels."""
    import numpy as np

    points = [(a[0] * size, a[1] * size), (b[0] * size, b[1] * size), (c[0] * size, c[1] * size)]
    min_x = max(0, int(min(p[0] for p in points)))
    max_x = min(size, int(max(p[0] for p in points)) + 1)
    min_y = max(0, int(min(p[1] for p in points)))
    max_y = min(size, int(max(p[1] for p in points)) + 1)
    if min_x >= max_x or min_y >= max_y:
        return

    (x0, y0), (x1, y1), (x2, y2) = points
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-12:
        return

    xs = np.arange(min_x, max_x) + 0.5
    ys = np.arange(min_y, max_y) + 0.5
    gx, gy = np.meshgrid(xs, ys)

    w0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
    w1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
    w2 = 1.0 - w0 - w1
    mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not mask.any():
        return
    patch = buffer[min_y:max_y, min_x:max_x]
    patch[mask] = color


def _stroke_edge(buffer, size, a, b, color, width=LINE_WIDTH) -> None:
    """Draw a line of the given width between two UV points."""
    import numpy as np

    ax, ay = a[0] * size, a[1] * size
    bx, by = b[0] * size, b[1] * size
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-9:
        return
    nx = -dy / length * width * 0.5
    ny = dx / length * width * 0.5
    corners = [
        (ax + nx, ay + ny),
        (bx + nx, by + ny),
        (bx - nx, by - ny),
        (ax - nx, ay - ny),
    ]
    _fill_triangle(buffer, size, corners[0], corners[1], corners[2], color)
    _fill_triangle(buffer, size, corners[0], corners[2], corners[3], color)


def render_layout(objects: Sequence, size: int = 2048, name: str = "UV_Layout"):
    """Draw the UV islands of *objects* into a new image.

    The image is the size of the atlas and uses the same space as the UV, so
    a pixel of the picture is a pixel of the texture.
    """
    import numpy as np

    buffer = np.zeros((size, size, 4), dtype=np.float32)
    buffer[:, :, 0] = BACKGROUND[0]
    buffer[:, :, 1] = BACKGROUND[1]
    buffer[:, :, 2] = BACKGROUND[2]
    buffer[:, :, 3] = 1.0

    for obj in objects:
        for triangle in _triangles_uv(obj):
            _fill_triangle(buffer, size, triangle[0], triangle[1], triangle[2], FILL_COLOR)

    for obj in objects:
        for edge in _boundary_edges_uv(obj):
            _stroke_edge(buffer, size, edge[0], edge[1], LINE_COLOR)

    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    image.pixels.foreach_set(buffer.ravel())
    image.filepath_raw = ""
    return image


# --- the kit ---------------------------------------------------------------


def _copy_texture(image, path: Path) -> bool:
    """Save a copy of *image* next to the kit."""
    try:
        image.save_render(filepath=str(path))
        return os.path.exists(str(path))
    except Exception:  # noqa: BLE001 - a broken image must not stop the export
        return False


def export_kit(
    objects: Sequence,
    folder: Optional[str] = None,
    size: Optional[int] = None,
    settings=None,
) -> Dict:
    """Write the UV kit of *objects* and return a report."""
    settings = settings or uv.settings_from_scene()
    atlas_size = int(size) if size else int(settings.atlas)
    if not folder:
        base = uv.blend_dir() or tempfile_dir()
        folder = os.path.join(base, "uv_kit")
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)

    image = render_layout(objects, atlas_size, "UV_Layout")
    layout_path = target / "layout.png"
    image.filepath_raw = str(layout_path)
    image.file_format = "PNG"
    image.save()

    manifest = {
        "atlas_size": atlas_size,
        "margin": settings.margin,
        "density": settings.density,
        "padding": settings.margin,
        "objects": [],
    }

    for obj in objects:
        entry = {
            "name": obj.name,
            "islands": 0,
            "albedo": None,
        }
        report = uv.analyze(obj)
        entry["islands"] = len(report.islands)
        if report.texture:
            entry["texture"] = report.texture
            entry["texture_size"] = list(report.texture_size)
            source = bpy.data.images.get(report.texture)
            if source is not None:
                copy_path = target / f"albedo_{obj.name}.png"
                if _copy_texture(source, copy_path):
                    entry["albedo"] = copy_path.name
        manifest["objects"].append(entry)

    manifest_path = target / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "folder": str(target),
        "layout": str(layout_path),
        "manifest": str(manifest_path),
        "objects": len(objects),
        "size": atlas_size,
    }


def tempfile_dir() -> str:
    """A writable folder for the kit when the project was never saved."""
    import tempfile

    return tempfile.gettempdir()


# --- the import ------------------------------------------------------------


def _base_color_node(material):
    """The image texture that feeds the Base Color of *material*, if any."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    bsdf = api.find_principled(tree)
    if bsdf is None:
        return None
    base = api.get_input(bsdf, "Base Color")
    if base is None or not base.links:
        return None
    source = base.links[0].from_node
    if source.type == "TEX_IMAGE":
        return source
    # The Base Color may come through a mix or a group: find the image node
    # that feeds it, one level down.
    for link in source.inputs.links if hasattr(source.inputs, "links") else []:
        if link.from_node.type == "TEX_IMAGE":
            return link.from_node
    return None


def import_atlas(filepath: str, obj=None) -> Dict:
    """Put a painted atlas into the material of *obj*.

    The UV coordinates are not touched, so the picture lands exactly where the
    layout said it would.
    """
    report = {"image": "", "material": "", "object": "", "ok": False}
    if not filepath or not os.path.exists(filepath):
        report["error"] = "the file was not found"
        return report

    image = bpy.data.images.load(filepath, check_existing=True)
    report["image"] = image.name

    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        report["error"] = "there is no active object"
        return report
    report["object"] = obj.name

    material = getattr(obj, "active_material", None)
    if material is None:
        report["error"] = "the object has no material"
        return report
    report["material"] = material.name

    tree = getattr(material, "node_tree", None)
    if tree is None:
        report["error"] = "the material has no node tree"
        return report

    node = _base_color_node(material)
    if node is None:
        # No texture yet: make one and wire it to the Base Color.
        bsdf = api.find_principled(tree)
        if bsdf is None:
            report["error"] = "the material has no Principled BSDF"
            return report
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = "Atlas"
        node.location = (bsdf.location.x - 400, bsdf.location.y)
        base = api.get_input(bsdf, "Base Color")
        for link in list(base.links):
            tree.links.remove(link)
        tree.links.new(node.outputs["Color"], base)
        report["created"] = True

    node.image = image
    report["ok"] = True
    return report


# --- the tools -------------------------------------------------------------


@tools.tool(
    "uv.export_kit",
    "Export the UV kit",
    "Write a UV kit for the selected objects: the UV layout as a PNG, a "
    "manifest with the atlas size, the gap and the density, and a copy of the "
    "current texture. The kit is meant to be painted in another program.",
    params=("folder", "size"),
)
def export_kit_tool(folder=None, size=None, **kwargs) -> str:
    """Tool entry point: export the UV kit."""
    settings = uv.settings_from_scene()
    targets = uv.scope_objects()
    if not targets:
        return "There are no meshes to export."
    report = export_kit(targets, folder, size, settings)
    return (
        f"UV kit written to {report['folder']}:\n"
        f"- {report['layout']} (the layout, {report['size']}px)\n"
        f"- {report['manifest']} (the manifest)\n"
        "Paint the layout in Aseprite or Krita, save the atlas as a PNG and "
        "import it back with the atlas import tool."
    )


@tools.tool(
    "uv.import_atlas",
    "Import the atlas",
    "Put a painted atlas PNG into the Base Color of the active object's "
    "material. The UV coordinates are not touched, so the picture lands "
    "exactly where the layout said it would.",
    params=("file",),
)
def import_atlas_tool(file=None, **kwargs) -> str:
    """Tool entry point: import a painted atlas."""
    if not file:
        return "No file given. Ask the user for the path to the painted PNG."
    report = import_atlas(str(file))
    if not report.get("ok"):
        return f"The atlas was not imported: {report.get('error', 'unknown')}"
    line = f"The atlas '{report['image']}' is now the Base Color of '{report['material']}'"
    if report.get("created"):
        line += " (a new texture node was created)"
    return line + "."
