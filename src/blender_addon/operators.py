"""Operators: send / stop / clear / open settings / chat window / run code."""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout

import bpy
from bpy.props import StringProperty

from . import i18n, materials, preferences, prompts, snapshot, state, ui

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


def _find_screen(context):
    """The ``Screen`` behind the current window, or ``None`` in background mode."""
    screen = getattr(context, "screen", None)
    if screen is None:
        screen = getattr(getattr(context, "window", None), "screen", None)
    return screen


def _find_area(context, target_type: str):
    """Return an ``Area`` able to host an editor of ``target_type``.

    Blender's UI is a tree: ``Screen`` → ``Area`` → ``Region`` → ``Space``.
    An editor is a ``Space`` inside an ``Area``, and ``Area.type`` selects
    which one (``"NODE_EDITOR"``, ``"IMAGE_EDITOR"``, ``"VIEW_3D"``, ...).

    To *show* the Shader/Image editor we therefore either reuse an area that
    already hosts it, or repurpose another area by flipping its ``type``. We
    never repurpose the area the button was clicked from — switching it would
    replace the very sidebar the chat is drawn in.
    """
    screen = _find_screen(context)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type == target_type:
            return area
    origin = getattr(context, "area", None)
    # NOTE: bpy structs hand out a fresh Python wrapper on every access, so
    # ``area is not origin`` would always be True. ``!=`` compares the actual
    # Blender pointers, which is what we mean here.
    candidates = [area for area in screen.areas if area != origin]
    if not candidates:
        candidates = list(screen.areas)
    if not candidates:
        return None
    area = max(candidates, key=lambda a: a.width * a.height)
    area.type = target_type
    return area


def _active_object_material(context):
    """The material in the active slot of the active object, if any."""
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    if obj is None:
        return None, None
    return obj, getattr(obj, "active_material", None)


def _first_material_image(material):
    """First image referenced by an Image Texture node of ``material``."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
            return node.image
    return None


class GAA_OT_open_shader_editor(bpy.types.Operator):
    """Открыть Shader Editor для материала активного объекта"""

    bl_idname = "gaa.open_shader_editor"
    bl_label = "Shader Editor"
    bl_options = {"REGISTER"}

    def execute(self, context):
        area = _find_area(context, "NODE_EDITOR")
        if area is None or area.spaces.active is None:
            self.report({"WARNING"}, i18n.T("msg.editor_no_area"))
            return {"CANCELLED"}

        _obj, mat = _active_object_material(context)
        if mat is None:
            self.report({"INFO"}, i18n.T("msg.no_material"))
            return {"FINISHED"}

        try:
            space = area.spaces.active
            # shader_type OBJECT = "Material" mode; pin=False makes the editor
            # follow the active object, so it lands on the material we report.
            space.shader_type = "OBJECT"
            space.pin = False
        except Exception as exc:  # noqa: BLE001 — any RNA refusal is non-fatal
            self.report({"WARNING"}, i18n.T("msg.editor_failed", exc=exc))
            return {"CANCELLED"}

        self.report({"INFO"}, i18n.T("msg.shader_opened", name=mat.name))
        return {"FINISHED"}


class GAA_OT_open_image_editor(bpy.types.Operator):
    """Открыть Image Editor для текстуры материала активного объекта"""

    bl_idname = "gaa.open_image_editor"
    bl_label = "Image Editor"
    bl_options = {"REGISTER"}

    def execute(self, context):
        area = _find_area(context, "IMAGE_EDITOR")
        if area is None or area.spaces.active is None:
            self.report({"WARNING"}, i18n.T("msg.editor_no_area"))
            return {"CANCELLED"}

        _obj, mat = _active_object_material(context)
        image = _first_material_image(mat) if mat is not None else None
        if image is None:
            # No texture on the active material: show *something* useful rather
            # than refuse — a rendered result, or the first image in the file.
            image = bpy.data.images.get("Render Result")
        if image is None and len(bpy.data.images):
            image = bpy.data.images[0]
        if image is None:
            self.report({"INFO"}, i18n.T("msg.no_image"))
            return {"FINISHED"}

        try:
            area.spaces.active.image = image
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, i18n.T("msg.editor_failed", exc=exc))
            return {"CANCELLED"}

        self.report({"INFO"}, i18n.T("msg.image_opened", name=image.name))
        return {"FINISHED"}


class GAA_OT_use_preset(bpy.types.Operator):
    """Подставить готовый запрос в поле ввода чата"""

    bl_idname = "gaa.use_preset"
    bl_label = "Material preset"
    bl_options = {"REGISTER"}

    # The preset id ("matte", "metal", ...) is carried as an operator property,
    # set by the button in ui.py. We use a plain StringProperty instead of an
    # EnumProperty because enum item names are RNA-baked once at registration
    # and would not follow the UI language (see i18n.py's known limitation).
    preset: StringProperty(default="matte")

    def execute(self, context):
        key = "preset.%s.text" % self.preset
        lang = i18n.get_lang()
        if key not in i18n.STRINGS[lang] and key not in i18n.STRINGS[i18n.DEFAULT_LANG]:
            return {"CANCELLED"}
        context.window_manager.gaa_input = i18n.T(key)
        return {"FINISHED"}


def _assign_material(context, material) -> str:
    """Put ``material`` on the active object; return the object's name or "".

    Replaces the active slot when the object already has materials (what an
    artist expects when re-texturing) and appends otherwise. Objects without a
    material list (empties, lights, ...) are left alone.
    """
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return ""
    if len(slots):
        index = getattr(obj, "active_material_index", 0)
        if not 0 <= index < len(slots):
            index = 0
        slots[index] = material
    else:
        slots.append(material)
    return obj.name


class GAA_OT_pbr_from_folder(bpy.types.Operator):
    """Собрать PBR-материал из текстурных карт в выбранной папке"""

    bl_idname = "gaa.pbr_from_folder"
    bl_label = "PBR из папки"
    bl_options = {"REGISTER", "UNDO"}

    # subtype="DIR_PATH" makes Blender's file browser accept a *folder* instead
    # of a file; the chosen path lands back in this property on execute().
    filepath: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        # Opens the file browser asynchronously: it runs execute() once the user
        # confirms, so nothing below happens on the first click.
        return context.window_manager.fileselect_add(self)

    def execute(self, context):
        if not self.filepath:
            return {"CANCELLED"}
        try:
            # All the real work lives in materials.py (headless-testable): scan
            # the folder, load images with the right colour space, wire nodes.
            maps = materials.find_maps(self.filepath)
            if not maps:
                self.report({"WARNING"}, i18n.T("msg.pbr_empty"))
                return {"CANCELLED"}
            images = materials.load_images(maps)
            material = materials.build_material(
                materials.material_name_for(self.filepath), images
            )
            assigned = _assign_material(context, material)
        except Exception as exc:  # a bad folder must not kill the UI thread
            self.report({"ERROR"}, i18n.T("msg.pbr_failed", exc=exc))
            return {"CANCELLED"}

        maps_text = ", ".join(
            i18n.T("pbr.map." + role) for role in materials.ordered_roles(maps)
        )
        message = i18n.T("msg.pbr_done", name=material.name, maps=maps_text)
        if assigned:
            message += " · " + i18n.T("msg.pbr_assigned", obj=assigned)
        self.report({"INFO"}, message)
        return {"FINISHED"}


_classes = (
    GAA_OT_send,
    GAA_OT_stop,
    GAA_OT_clear,
    GAA_OT_settings,
    GAA_OT_chat_window,
    GAA_OT_run_code,
    GAA_OT_code_to_text,
    GAA_OT_open_shader_editor,
    GAA_OT_open_image_editor,
    GAA_OT_use_preset,
    GAA_OT_pbr_from_folder,
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
    "gaa.open_shader_editor": "op.open_shader",
    "gaa.open_image_editor": "op.open_image",
    "gaa.use_preset": "op.use_preset",
    "gaa.pbr_from_folder": "op.pbr_folder",
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
