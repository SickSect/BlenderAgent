"""Chat UI: one shared ``draw_chat`` rendered by several hosts.

Hosts (all render exactly the same chat, state is shared through the
``WindowManager`` props):

* ``GAA_PT_main``       — 3D Viewport sidebar (N), tab "AI"
* ``GAA_PT_shader``     — Shader/Node Editor sidebar, tab "AI"
* ``GAA_PT_image``      — Image/UV Editor sidebar, tab "AI"
* ``gaa.chat_window``   — floating popup window (see operators.py)

``wrap_width`` is in characters: sidebars are narrow, so they wrap at
``_WRAP_WIDTH``; the popup window uses the width from the add-on preferences
and wraps proportionally.
"""

from __future__ import annotations

import textwrap

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty

from . import i18n, preferences, state

# EVENT_A ("A" in a circle) — closest thing to an "AI" badge; BOT does not
# exist as an icon in Blender 4.x/5.x.
_ROLE_ICONS = {"user": "USER", "assistant": "EVENT_A", "error": "ERROR", "result": "INFO"}
_ROLE_KEYS = {"user": "role.user", "assistant": "role.assistant", "error": "role.error", "result": "role.result"}
_WRAP_WIDTH = 54          # chars — narrow sidebars
_POPUP_FONT_WIDTH = 7     # approx px per char at 100% UI scale
_POPUP_PADDING = 90       # px reserved for box borders/paddings
_MAX_DETAIL_LINES = 60
_CODE_PREVIEW_LINES = 14


class GAAChatMessage(bpy.types.PropertyGroup):
    role: StringProperty(default="assistant")
    body: StringProperty(default="")


class GAA_UL_messages(bpy.types.UIList):
    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            first_line = (item.body or "").splitlines()
            preview = first_line[0] if first_line else "..."
            if len(preview) > 70:
                preview = preview[:70] + "…"
            role_key = _ROLE_KEYS.get(item.role, "role.assistant")
            label = f"{i18n.T(role_key)}: {preview}"
            layout.label(text=label, icon=_ROLE_ICONS.get(item.role, "DOT"))
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon=_ROLE_ICONS.get(item.role, "DOT"))


def _draw_wrapped(box, text: str, wrap_width: int = _WRAP_WIDTH) -> None:
    lines = []
    for paragraph in text.split("\n"):
        wrapped = textwrap.wrap(paragraph, width=wrap_width)
        lines.extend(wrapped if wrapped else [""])
    if len(lines) > _MAX_DETAIL_LINES:
        head = lines[: _MAX_DETAIL_LINES // 2]
        tail = lines[-(_MAX_DETAIL_LINES // 2):]
        lines = head + ["…"] + tail
    for line in lines:
        box.label(text=line if line else " ")


def popup_wrap_width(width_px: int) -> int:
    """Characters per line for a popup of ``width_px`` pixels."""
    return max(30, (width_px - _POPUP_PADDING) // _POPUP_FONT_WIDTH)


def draw_chat(
    layout,
    context,
    *,
    show_window_button: bool = True,
    wrap_width: int = _WRAP_WIDTH,
    title: bool = False,
) -> None:
    """Shared chat drawing: status, history, pending code, input, actions.

    ``show_window_button`` — render the "open floating window" button (panels
    only: inside the popup itself it would be pointless).
    ``wrap_width`` — chars per line for wrapped text, matched to host width.
    ``title`` — draw a header line (the popup window has no native title bar).
    """
    wm = context.window_manager
    chat = state.get_state()
    prefs = preferences.get_prefs(context)

    if title:
        head = layout.row()
        head.label(text=i18n.T("chat.title"), icon="EVENT_A")

    # -- status line -----------------------------------------------------
    row = layout.row(align=True)
    if chat.busy:
        row.label(text=i18n.T("status.busy"), icon="TIME")
    elif chat.last_error:
        row.label(text=i18n.T("status.error"), icon="ERROR")
    elif chat.alive():
        model = ""
        if prefs is not None:
            model = (
                prefs.ollama_model if prefs.backend == "OLLAMA" else prefs.api_model
            )
        row.label(text=i18n.T("status.ready_model", model=model), icon="CHECKMARK")
    else:
        row.label(text=i18n.T("status.ready"), icon="RADIOBUT_ON")

    # -- history ---------------------------------------------------------
    if len(wm.gaa_messages):
        layout.template_list(
            "GAA_UL_messages",
            "",
            wm,
            "gaa_messages",
            wm,
            "gaa_active",
            rows=7,
        )
        active = wm.gaa_active
        if 0 <= active < len(wm.gaa_messages):
            item = wm.gaa_messages[active]
            if item.body.strip():
                detail = layout.box()
                _draw_wrapped(detail, item.body, wrap_width)
    else:
        box = layout.box()
        box.label(text=i18n.T("history.empty_1"), icon="INFO")
        box.label(text=i18n.T("history.empty_2"))

    if chat.last_error and not chat.busy:
        err = layout.box()
        for line in textwrap.wrap(chat.last_error, width=wrap_width)[:6]:
            err.label(text=line, icon="ERROR")

    # -- pending bpy code --------------------------------------------------
    if chat.pending_code and not chat.busy:
        box = layout.box()
        box.label(text=i18n.T("pending.title"), icon="SCRIPT")
        preview = chat.pending_code.splitlines()[:_CODE_PREVIEW_LINES]
        for line in preview:
            clipped = line if len(line) <= wrap_width else line[:wrap_width] + "…"
            box.label(text=clipped if clipped else " ")
        rest = len(chat.pending_code.splitlines()) - len(preview)
        if rest > 0:
            box.label(text=i18n.T("pending.more", n=rest))
        row = box.row(align=True)
        row.scale_y = 1.3
        if chat.code_ran:
            # The script has already been executed for this reply; running it
            # again would duplicate whatever it created (see operators.py).
            row.label(text=i18n.T("pending.ran"), icon="CHECKMARK")
        else:
            row.operator("gaa.run_code", icon="PLAY")
        row.operator("gaa.code_to_text", text="", icon="TEXT")

    # -- input -----------------------------------------------------------
    layout.prop(wm, "gaa_input", text="", placeholder=i18n.T("input.placeholder"))

    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator("gaa.send", icon="PLAY")
    row.operator("gaa.stop", text="", icon="PAUSE")

    row = layout.row(align=True)
    row.operator("gaa.clear", icon="TRASH")
    row.operator("gaa.settings", icon="PREFERENCES")

    if show_window_button:
        layout.operator("gaa.chat_window", icon="WINDOW")


class _GAAChatPanelMixin:
    """One draw implementation for every host panel.

    Blender binds a panel to a single (space, region) pair, so the same chat
    in three editors means three tiny Panel subclasses — the content itself
    lives in :func:`draw_chat` and is shared.
    """

    bl_label = "AI Assistant"
    bl_category = "AI"

    def draw(self, context):
        draw_chat(self.layout, context)


class GAA_PT_main(_GAAChatPanelMixin, bpy.types.Panel):
    """3D Viewport sidebar (N) → tab "AI"."""

    bl_idname = "GAA_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"


class GAA_PT_shader(_GAAChatPanelMixin, bpy.types.Panel):
    """Shader/Node Editor sidebar → tab "AI" (materials stage)."""

    bl_idname = "GAA_PT_shader"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"


class GAA_PT_image(_GAAChatPanelMixin, bpy.types.Panel):
    """Image/UV Editor sidebar → tab "AI" (textures stage)."""

    bl_idname = "GAA_PT_image"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"


_classes = (GAAChatMessage, GAA_UL_messages, GAA_PT_main, GAA_PT_shader, GAA_PT_image)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.gaa_messages = CollectionProperty(type=GAAChatMessage)
    bpy.types.WindowManager.gaa_active = IntProperty(default=0)
    bpy.types.WindowManager.gaa_input = StringProperty(
        name="Сообщение / Message",
        description="Текст запроса к AI-помощнику / Request text for the AI assistant",
        default="",
    )


def unregister():
    del bpy.types.WindowManager.gaa_messages
    del bpy.types.WindowManager.gaa_active
    del bpy.types.WindowManager.gaa_input
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
