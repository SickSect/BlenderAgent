"""N-panel chat UI in the 3D Viewport sidebar (tab "AI")."""

from __future__ import annotations

import textwrap

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty

from . import preferences, state

_ROLE_ICONS = {"user": "USER", "assistant": "BOT", "error": "ERROR"}
_ROLE_LABELS = {"user": "Вы", "assistant": "AI", "error": "Ошибка"}
_WRAP_WIDTH = 54
_MAX_DETAIL_LINES = 60


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
            label = f"{_ROLE_LABELS.get(item.role, item.role)}: {preview}"
            layout.label(text=label, icon=_ROLE_ICONS.get(item.role, "DOT"))
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon=_ROLE_ICONS.get(item.role, "DOT"))


def _draw_wrapped(box, text: str) -> None:
    lines = []
    for paragraph in text.split("\n"):
        wrapped = textwrap.wrap(paragraph, width=_WRAP_WIDTH)
        lines.extend(wrapped if wrapped else [""])
    if len(lines) > _MAX_DETAIL_LINES:
        head = lines[: _MAX_DETAIL_LINES // 2]
        tail = lines[-(_MAX_DETAIL_LINES // 2):]
        lines = head + ["…"] + tail
    for line in lines:
        box.label(text=line if line else " ")


class GAA_PT_main(bpy.types.Panel):
    bl_label = "AI Assistant"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        chat = state.get_state()
        prefs = preferences.get_prefs(context)

        # -- status line ---------------------------------------------------
        row = layout.row(align=True)
        if chat.busy:
            row.label(text="Генерация…", icon="TIME")
        elif chat.last_error:
            row.label(text="Ошибка", icon="ERROR")
        elif chat.alive():
            model = ""
            if prefs is not None:
                model = (
                    prefs.ollama_model
                    if prefs.backend == "OLLAMA"
                    else prefs.api_model
                )
            row.label(text=f"Готов · {model}", icon="CHECKMARK")
        else:
            row.label(text="Готов к запуску", icon="RADIOBUT_ON")

        # -- history -------------------------------------------------------
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
                    _draw_wrapped(detail, item.body)
        else:
            box = layout.box()
            box.label(text="Спросите что-нибудь про сцену,", icon="INFO")
            box.label(text="модель, материалы или bpy-код")

        if chat.last_error and not chat.busy:
            err = layout.box()
            for line in textwrap.wrap(chat.last_error, width=_WRAP_WIDTH)[:6]:
                err.label(text=line, icon="ERROR")

        # -- input ---------------------------------------------------------
        layout.prop(wm, "gaa_input", text="", placeholder="Сообщение…")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("gaa.send", icon="PLAY")
        row.operator("gaa.stop", text="", icon="PAUSE")

        row = layout.row(align=True)
        row.operator("gaa.clear", text="Очистить", icon="TRASH")
        row.operator("gaa.settings", text="Настройки", icon="PREFERENCES")


_classes = (GAAChatMessage, GAA_UL_messages, GAA_PT_main)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.gaa_messages = CollectionProperty(type=GAAChatMessage)
    bpy.types.WindowManager.gaa_active = IntProperty(default=0)
    bpy.types.WindowManager.gaa_input = StringProperty(
        name="Сообщение",
        description="Текст запроса к AI-помощнику",
        default="",
    )


def unregister():
    del bpy.types.WindowManager.gaa_messages
    del bpy.types.WindowManager.gaa_active
    del bpy.types.WindowManager.gaa_input
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
