"""Game Asset Assistant — AI chat inside the Blender sidebar.

Install: build the extension zip (``python tools/build_addon.py``) and use
Edit > Preferences > Add-ons > Install from Disk (Blender 4.2+).

Chat requests run in a sidecar worker process (see asset_core/worker.py);
this add-on only ever polls JSONL files from a ``bpy.app.timers`` callback,
because Python threads that outlive a script are not supported by Blender.
"""

bl_info = {
    "name": "Game Asset Assistant",
    "author": "Aleksei Zalogin",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > AI",
    "description": "AI chat assistant for creating game assets",
    "category": "Object",
}

import bpy

from . import operators, preferences, state, ui


def _on_load_post(*_args):
    """A new .blend file clears the WindowManager props; mirror that in the
    model-facing history so the two never drift apart."""
    state.get_state().reset_session()


def register():
    preferences.register()
    ui.register()
    operators.register()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    state.remove_timer()
    state.get_state().kill_worker()
    operators.unregister()
    ui.unregister()
    preferences.unregister()
