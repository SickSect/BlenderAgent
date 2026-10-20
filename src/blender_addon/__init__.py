"""Asset Assistant — AI chat inside Blender.

Install: build the extension zip (``python tools/build_addon.py``) and use
Edit > Preferences > Add-ons > Install from Disk (Blender 4.2+).

UI: the chat lives in the 3D Viewport sidebar (N > AI), in the Shader and
Image editor sidebars, and also opens as a standalone popup window with
Ctrl+Alt+A (``gaa.chat_window``). The last ```python block of an assistant
reply can be executed right in the current scene with the "Run code" button.

The interface is bilingual (RU/EN) via ``i18n.py``; the language is switched
in the add-on preferences and lives in ``i18n.STRINGS``.

Chat requests run in a sidecar worker process (see asset_core/worker.py);
this add-on only ever polls JSONL files from a ``bpy.app.timers`` callback,
because Python threads that outlive a script are not supported by Blender.
"""

bl_info = {
    "name": "Asset Assistant",
    "author": "Aleksei Zalogin",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > AI",
    "description": "AI chat assistant for creating assets",
    "category": "Object",
}

import bpy

from . import api, nodemenu, operators, preferences, state, ui, uv

_keymaps = []


def _on_load_post(*_args):
    """A new .blend file clears the WindowManager props; mirror that in the
    model-facing history so the two never drift apart."""
    state.get_state().reset_session()


def _register_keymap():
    """Ctrl+Alt+A — open the floating chat window from anywhere."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    kc = wm.keyconfigs.addon or wm.keyconfigs.user
    if kc is None:  # headless/background Blender has no key configs
        return
    km = kc.keymaps.new(name="Window", space_type="EMPTY")
    kmi = km.keymap_items.new(
        "gaa.chat_window", "A", "PRESS", ctrl=True, alt=True
    )
    _keymaps.append((km, kmi))


def _unregister_keymap():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (KeyError, ReferenceError, RuntimeError):
            pass
    _keymaps.clear()


def register():
    preferences.register()
    ui.register()
    uv.register()
    nodemenu.register()
    operators.register()
    # Publish the version-tolerant helpers where generated code can reach
    # them: bpy.app.driver_namespace is the standard place for a plugin to
    # share data with the files it produces.
    api.install(bpy.app.driver_namespace)
    _register_keymap()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _unregister_keymap()
    state.remove_timer()
    state.get_state().kill_worker()
    operators.unregister()
    nodemenu.unregister()
    uv.unregister()
    ui.unregister()
    preferences.unregister()
    bpy.app.driver_namespace.pop("gaa_api", None)
