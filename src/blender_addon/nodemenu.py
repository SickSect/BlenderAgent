"""The shader editor context menu.

The fastest repair is the one you do where the problem is: right-click on
the image texture that has the wrong colour space, in the node editor you are
already working in.

Blender's node menu (``NODE_MT_node``) is **extended**, not replaced - the
``append`` method adds a draw function, so the standard entries (Copy, Paste,
Delete, ...) stay where they were.

The entries only show up when the node under the mouse is an image texture,
because for any other node there is nothing to repair.
"""

from __future__ import annotations

import bpy

from . import api, i18n, textures


def _node(context):
    """The node under the mouse in the node editor."""
    return getattr(context, "node", None)


def _image(node):
    """The image of a texture node, or ``None``."""
    if node is None or node.type != "TEX_IMAGE":
        return None
    return getattr(node, "image", None)


def draw_node_menu(self, context) -> None:
    """The Asset Assistant entries of the node context menu."""
    node = _node(context)
    image = _image(node)
    if image is None:
        return

    layout = self.layout
    layout.separator()
    layout.label(text=i18n.T("menu.asset_assistant"), icon="FILE_REFRESH")
    layout.operator("gaa.node_fix_colorspace", text=i18n.T("menu.fix_colorspace"))
    layout.operator("gaa.node_pack", text=i18n.T("menu.pack"))
    layout.operator("gaa.node_make_relative", text=i18n.T("menu.make_relative"))
    layout.separator()
    layout.operator("gaa.node_set_base_color", text=i18n.T("menu.set_base_color"))
    layout.operator("gaa.node_set_roughness", text=i18n.T("menu.set_roughness"))
    layout.operator("gaa.node_set_normal", text=i18n.T("menu.set_normal"))


def register() -> None:
    """Extend Blender's node menu."""
    bpy.types.NODE_MT_node.append(draw_node_menu)


def unregister() -> None:
    """Take our entries back out."""
    try:
        bpy.types.NODE_MT_node.remove(draw_node_menu)
    except (AttributeError, RuntimeError):
        pass
