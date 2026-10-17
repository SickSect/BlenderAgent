"""Operators: send / stop / clear / open settings."""

from __future__ import annotations

import bpy

from . import preferences, prompts, snapshot, state


def _system_prompt(context) -> str:
    prefs = preferences.get_prefs(context)
    text = prompts.SYSTEM_PROMPT
    include = prefs.include_snapshot if prefs is not None else True
    if include:
        try:
            text += "\n\n## Снимок текущей сцены\n" + snapshot.build_snapshot()
        except Exception as exc:  # a broken snapshot must not block the chat
            text += f"\n\n(Снимок сцены недоступен: {exc})"
    return text


class GAA_OT_send(bpy.types.Operator):
    """Send the current message to the assistant"""

    bl_idname = "gaa.send"
    bl_label = "Отправить"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        chat = state.get_state()
        if chat.busy:
            return False
        return bool(context.window_manager.gaa_input.strip())

    def execute(self, context):
        wm = context.window_manager
        text = wm.gaa_input.strip()
        if not text:
            return {"CANCELLED"}

        prefs = preferences.get_prefs(context)
        max_chars = (
            prefs.context_chars if prefs is not None else state.DEFAULT_CONTEXT_CHARS
        )
        ok = state.get_state().send(
            config=preferences.config_dict(prefs),
            system_prompt=_system_prompt(context),
            user_text=text,
            max_chars=max_chars,
        )
        if not ok:
            chat = state.get_state()
            self.report({"ERROR"}, chat.last_error or "Не удалось отправить запрос")
            return {"CANCELLED"}

        wm.gaa_input = ""
        return {"FINISHED"}


class GAA_OT_stop(bpy.types.Operator):
    """Stop generation and kill the worker"""

    bl_idname = "gaa.stop"
    bl_label = "Стоп"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return state.get_state().busy or state.get_state().alive()

    def execute(self, context):
        chat = state.get_state()
        if chat.busy and chat.stream_buf:
            chat.history.append({"role": "assistant", "content": chat.stream_buf})
            state.set_last_display(chat.stream_buf + "\n\n[остановлено]")
            chat.stream_buf = ""
        chat.busy = False
        chat.kill_worker()
        state._redraw()
        return {"FINISHED"}


class GAA_OT_clear(bpy.types.Operator):
    """Clear chat history"""

    bl_idname = "gaa.clear"
    bl_label = "Очистить"
    bl_options = {"INTERNAL", "UNDO"}

    @classmethod
    def poll(cls, context):
        wm = context.window_manager
        return bool(len(wm.gaa_messages)) or bool(state.get_state().history)

    def execute(self, context):
        chat = state.get_state()
        chat.kill_worker()
        chat.reset_session()
        wm = context.window_manager
        wm.gaa_messages.clear()
        wm.gaa_active = 0
        state._redraw()
        return {"FINISHED"}


class GAA_OT_settings(bpy.types.Operator):
    """Open add-on settings"""

    bl_idname = "gaa.settings"
    bl_label = "Настройки"

    def execute(self, context):
        try:
            bpy.ops.preferences.addon_show(module=__package__)
        except Exception:
            try:
                bpy.ops.screen.userpref_show()
            except Exception as exc:
                self.report({"WARNING"}, f"Не удалось открыть настройки: {exc}")
                return {"CANCELLED"}
        return {"FINISHED"}


_classes = (GAA_OT_send, GAA_OT_stop, GAA_OT_clear, GAA_OT_settings)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
