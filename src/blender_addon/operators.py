"""Operators: send / stop / clear / open settings / chat window / run code."""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout

import bpy

from . import i18n, preferences, prompts, snapshot, state, ui

_POPUP_WIDTH = 480
_CODE_TEXT_NAME = "AI Assistant Code"


def _system_prompt(context) -> str:
    prefs = preferences.get_prefs(context)
    lang = prefs.language.lower() if prefs is not None else i18n.DEFAULT_LANG
    text = prompts.system_prompt(lang)
    include = prefs.include_snapshot if prefs is not None else True
    if include:
        try:
            text += (
                "\n\n" + i18n.T("prompt.snapshot_header") + "\n" + snapshot.build_snapshot()
            )
        except Exception as exc:  # a broken snapshot must not block the chat
            text += f"\n\n(Snapshot unavailable: {exc})"
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
            self.report({"ERROR"}, chat.last_error or i18n.T("msg.send_failed"))
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
            state.set_last_display(chat.stream_buf + "\n\n" + i18n.T("msg.stopped"))
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
                self.report({"WARNING"}, i18n.T("msg.settings_failed", exc=exc))
                return {"CANCELLED"}
        return {"FINISHED"}


def _chat_popup_width(context) -> int:
    """Popup width in px from the add-on preferences (with a safe fallback)."""
    prefs = preferences.get_prefs(context)
    width = getattr(prefs, "chat_width", None)
    return int(width) if width else _POPUP_WIDTH


class GAA_OT_chat_window(bpy.types.Operator):
    """Открыть чат с AI-ассистентом в отдельном окне"""

    bl_idname = "gaa.chat_window"
    bl_label = "Окно чата"

    _wrap = 0  # chars per line for draw(); set in invoke()

    def invoke(self, context, event):
        width = _chat_popup_width(context)
        self._wrap = ui.popup_wrap_width(width)
        try:
            # Popup stays open until Esc / click outside; width is ours to
            # choose, unlike wm.call_panel which auto-sizes a narrow panel.
            return context.window_manager.invoke_popup(self, width=width)
        except Exception as exc:
            self.report({"ERROR"}, i18n.T("msg.window_failed", exc=exc))
            return {"CANCELLED"}

    def execute(self, context):
        # F3 search and scripts run operators in EXEC context — reuse invoke.
        return self.invoke(context, None)

    def draw(self, context):
        ui.draw_chat(
            self.layout,
            context,
            show_window_button=False,
            wrap_width=self._wrap or ui._WRAP_WIDTH,
            title=True,
        )


def _exec_context(context) -> dict:
    """Context overrides that let bpy.ops.* run as if from a 3D viewport.

    ``run_code`` is clicked from the chat popup or a sidebar panel, so
    ``context.area``/``context.region`` belong to a UI region. Context-sensitive
    operators (``object.duplicate``, ``object.join``, ``object.delete``, ...)
    then take a code path that can leave the dependency graph inconsistent;
    Blender later crashes while rebuilding it in the event loop (seen as an
    access violation in ``DepsgraphNodeBuilder::build_materials``). Re-running
    the script under a real 3D viewport window region removes that trigger.
    """
    overrides = {}
    window = getattr(context, "window", None)
    if window is not None:
        overrides["window"] = window
    screen = getattr(context, "screen", None) or getattr(window, "screen", None)
    area = None
    region = None
    if screen is not None:
        overrides["screen"] = screen
        for candidate in screen.areas:
            if candidate.type == "VIEW_3D":
                area = candidate
                for reg in candidate.regions:
                    if reg.type == "WINDOW":
                        region = reg
                        break
                break
    if area is not None:
        overrides["area"] = area
        space = area.spaces.active
        if space is not None:
            overrides["space_data"] = space
        if region is not None:
            overrides["region"] = region
    if getattr(context, "scene", None) is not None:
        overrides["scene"] = context.scene
    if getattr(context, "view_layer", None) is not None:
        overrides["view_layer"] = context.view_layer
    return overrides


def _run_in_context(code: str, namespace: dict) -> None:
    """exec ``code``, then settle the depsgraph right away.

    ``view_layer.update()`` forces a dependency-graph rebuild *here*, while a
    valid context is active, instead of leaving a transient state for the event
    loop to trip over.
    """
    exec(compile(code, "<AI Assistant>", "exec"), namespace)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _exec_script(code: str, namespace: dict, stdout, context) -> None:
    """Run the assistant's code in a safe context, capturing print() output."""
    overrides = _exec_context(context)
    with redirect_stdout(stdout):
        if overrides and hasattr(bpy.context, "temp_override"):
            with bpy.context.temp_override(**overrides):
                _run_in_context(code, namespace)
        else:
            _run_in_context(code, namespace)


class GAA_OT_run_code(bpy.types.Operator):
    """Выполнить последний bpy-код из ответа ассистента в текущей сцене"""

    bl_idname = "gaa.run_code"
    bl_label = "Выполнить код"
    # UNDO: a script that edits the scene becomes a single Ctrl+Z step.
    # REGISTER keeps the operator's RNA in sync.
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        chat = state.get_state()
        # Refuse a second run of the same block: running it twice duplicates
        # whatever it created (and can stack up scene state until Blender's
        # dependency graph breaks). A new reply re-arms the button.
        return bool(chat.pending_code) and not chat.code_ran

    def execute(self, context):
        chat = state.get_state()
        code = chat.pending_code
        if not code.strip():
            return {"CANCELLED"}

        stdout = io.StringIO()
        namespace = {"bpy": bpy, "__name__": "__gaa_ai__"}
        try:
            _exec_script(code, namespace, stdout, context)
        except Exception:
            state.add_display(
                "error", i18n.T("msg.code_error") + "\n" + traceback.format_exc()
            )
        else:
            message = i18n.T("msg.code_done")
            printed = stdout.getvalue().strip()
            if printed:
                message += "\n\n" + i18n.T("msg.output") + "\n" + printed
            state.add_display("result", message)
        # One-shot: the button is hidden once code_ran is set (see ui.py), so a
        # stray second click cannot duplicate the scene again. The code text
        # stays on screen for reference and for "To Text Editor".
        chat.code_ran = True
        state._redraw()
        return {"FINISHED"}


class GAA_OT_code_to_text(bpy.types.Operator):
    """Сохранить последний код ассистента в Text Editor"""

    bl_idname = "gaa.code_to_text"
    bl_label = "В Text Editor"

    @classmethod
    def poll(cls, context):
        return bool(state.get_state().pending_code)

    def execute(self, context):
        text = bpy.data.texts.get(_CODE_TEXT_NAME) or bpy.data.texts.new(
            _CODE_TEXT_NAME
        )
        text.clear()
        text.write(state.get_state().pending_code)
        self.report({"INFO"}, i18n.T("msg.code_saved", name=text.name))
        return {"FINISHED"}


_classes = (
    GAA_OT_send,
    GAA_OT_stop,
    GAA_OT_clear,
    GAA_OT_settings,
    GAA_OT_chat_window,
    GAA_OT_run_code,
    GAA_OT_code_to_text,
)

# bl_label and the class docstring (Blender's tooltip) are copied into RNA at
# registration time, so a language switch has to re-register the operators.
_OPERATOR_KEYS = {
    "gaa.send": "op.send",
    "gaa.stop": "op.stop",
    "gaa.clear": "op.clear",
    "gaa.settings": "op.settings",
    "gaa.chat_window": "op.chat_window",
    "gaa.run_code": "op.run_code",
    "gaa.code_to_text": "op.code_to_text",
}


def _apply_labels() -> None:
    for cls in _classes:
        key = _OPERATOR_KEYS.get(cls.bl_idname)
        if key:
            cls.bl_label = i18n.T(key + ".label")
            cls.__doc__ = i18n.T(key + ".desc")


def refresh_labels() -> None:
    """Re-register operators so RNA picks up the current language.

    Done in two phases: if any class cannot be unregistered (a modal operator
    instance is alive, e.g. the chat popup during an F3 search), everything
    already unregistered is put back, so the add-on never stays half-wired.
    """
    removed = []
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ReferenceError):
            for back in reversed(removed):
                bpy.utils.register_class(back)
            return  # labels update on the next enable
        removed.append(cls)
    _apply_labels()
    for cls in _classes:
        bpy.utils.register_class(cls)


def register():
    _apply_labels()
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
