"""Normal maps from a height map.

A height map says *how high* each point is; a normal map says *which way the
surface faces* there. A game engine cannot read a height map, but it reads
normal maps perfectly - so the height map has to be converted.

The conversion is a **derivative**. For every pixel we look at the height to the
left and to the right (and above and below), and the difference tells us the
slope. A surface that goes up towards +u tilts its normal towards -u:

    n = normalize( (-dh/du * strength, -dh/dv * strength, 1) )

and the normal is then encoded into the 0..1 range the way every normal map
expects: ``rgb = n * 0.5 + 0.5``. A flat surface gives (0.5, 0.5, 1.0) - the
"no bump" value that everybody recognises.

This is the same thing xNormal and Substance do, only without leaving Blender.

Conventions worth knowing
-------------------------

* the result is a **data** map: ``Non-Color`` and a float buffer. A normal
  map saved as sRGB loses its meaning the moment the colour space changes.
* the green channel follows the **OpenGL** convention (+Y up). Blender's own
  normal maps use it, and so do most game engines. ``flip_y`` switches to
  DirectX for the projects that need it.
* the height is read from the **red channel**, which is what a grayscale
  height map has.
"""

from __future__ import annotations

import math
from typing import Optional

import bpy

from . import tools

# The value a flat surface gets: (0.5, 0.5, 1.0).
FLAT_NORMAL = (0.5, 0.5, 1.0)


def _pixels_to_array(image):
    """The pixels of *image* as a numpy array of shape (height, width, 4).

    Row 0 is the **bottom** row of the image: Blender stores its pixels
    bottom to top, so the array keeps that order and the vertical derivative
    points the right way.
    """
    import numpy as np

    width, height = image.size
    if not width or not height:
        return None
    buffer = np.empty(width * height * 4, dtype=np.float32)
    try:
        image.pixels.foreach_get(buffer)
    except Exception:  # noqa: BLE001 - an image without data
        return None
    return buffer.reshape(height, width, 4)


def normal_from_height_array(
    height_map,
    strength: float = 1.0,
    flip_y: bool = False,
):
    """The normal map of a height field, as a numpy array.

    *height_map* is a (height, width) array of the heights in 0..1. The result
    is a (height, width, 4) array in 0..1, ready to be written into an image.

    The derivative is taken **per unit of UV**, not per pixel: that makes the
    result independent of the resolution, so a relief exported at 2K and at 4K
    looks the same (only the detail grows). The *strength* is therefore "how
    much one unit of UV distance tilts the normal".
    """
    import numpy as np

    source = height_map.astype(np.float32)
    height, width = source.shape

    # Pad by one pixel on every side, repeating the edge, so the derivative
    # at the border is not a cliff.
    padded = np.pad(source, 1, mode="edge")

    dh_du = (padded[1:-1, 2:] - padded[1:-1, :-2]) * 0.5 * max(1.0, width - 1)
    dh_dv = (padded[2:, 1:-1] - padded[:-2, 1:-1]) * 0.5 * max(1.0, height - 1)

    nx = -dh_du * strength
    ny = -dh_dv * strength
    if flip_y:
        ny = -ny

    nz = np.ones_like(nx)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx = nx / length
    ny = ny / length
    nz = nz / length

    out = np.ones(source.shape + (4,), dtype=np.float32)
    out[:, :, 0] = nx * 0.5 + 0.5
    out[:, :, 1] = ny * 0.5 + 0.5
    out[:, :, 2] = nz * 0.5 + 0.5
    out[:, :, 3] = 1.0
    return out


def height_to_normal(
    source,
    strength: float = 1.0,
    flip_y: bool = False,
    name: Optional[str] = None,
):
    """Build a normal map image from a height map image.

    *source* is an ``bpy.types.Image``. Returns the new image, or ``None`` when
    the source has no pixels.
    """
    if source is None:
        return None
    pixels = _pixels_to_array(source)
    if pixels is None:
        return None

    # The height is the red channel: a grayscale height map has R = G = B.
    height_map = pixels[:, :, 0]
    result = normal_from_height_array(height_map, strength, flip_y)

    width, height = source.size
    image = bpy.data.images.new(
        name or f"{source.name}_normal",
        width=width,
        height=height,
        alpha=False,
        float_buffer=True,
    )
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(result.ravel())
    return image


def assign_to_material(image, obj=None, material=None) -> bool:
    """Put a normal map into the Normal input of a material.

    The image texture node is created when the material has none.
    """
    if image is None:
        return False
    if material is None:
        obj = obj or getattr(bpy.context, "active_object", None)
        material = getattr(obj, "active_material", None)
    if material is None:
        return False
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return False

    from . import api

    bsdf = api.find_principled(tree)
    if bsdf is None:
        return False
    normal_socket = api.get_input(bsdf, "Normal")
    if normal_socket is None:
        return False

    node = None
    for link in normal_socket.links:
        if link.from_node.type == "TEX_IMAGE":
            node = link.from_node
            break
    if node is None:
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = "NormalMap"
        node.location = (bsdf.location.x - 400, bsdf.location.y - 250)
        for link in list(normal_socket.links):
            tree.links.remove(link)
        tree.links.new(node.outputs["Color"], normal_socket)
    node.image = image
    return True


# --- the tool --------------------------------------------------------------


@tools.tool(
    "bake.normal_from_height",
    "Normal map from a height map",
    "Build a normal map from a height map: the height is differentiated into "
    "surface slopes and encoded the way every normal map expects. The result "
    "is a data map (Non-Color, float).",
    params=("image", "strength", "flip_y"),
)
def normal_from_height_tool(image=None, strength=1.0, flip_y=False, **kwargs) -> str:
    """Tool entry point: build a normal map from a height map."""
    source = None
    if image:
        source = bpy.data.images.get(str(image))
        if source is None:
            return f"There is no image named '{image}' in this file."
    else:
        # Fall back to the image the user is looking at.
        space = getattr(bpy.context, "space_data", None)
        source = getattr(space, "image", None)
        if source is None:
            return (
                "No height map given. Name an image, or open one in the Image "
                "Editor and ask again."
            )

    result = height_to_normal(source, float(strength), bool(flip_y))
    if result is None:
        return f"The image '{source.name}' has no pixels to work with."

    assigned = assign_to_material(result)
    line = f"Built the normal map '{result.name}' from '{source.name}'"
    if assigned:
        line += " and put it into the Normal input of the active material"
    return line + "."
