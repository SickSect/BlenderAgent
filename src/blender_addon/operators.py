"""Operators: send / stop / clear / open settings / chat window / run code."""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout

import bpy
from bpy.props import StringProperty

from . import (
    baking,
    doctor,
    exchange,
    export,
    findings,
    heightmap,
    i18n,
    materials,
    nodemenu,
    objects,
    preferences,
    procedural,
    project,
    prompts,
    skills,
    snapshot,
    state,
    textures,
    tools,
    ui,
    uv,
)

_POPUP_WIDTH = 480
_CODE_TEXT_NAME = "AI Assistant Code"


def _system_prompt(context) -> str:
    prefs = preferences.get_prefs(context)
    lang = prefs.language.lower() if prefs is not None else i18n.DEFAULT_LANG
    text = prompts.system_prompt(lang)

    # The tool catalog comes first: a small local model follows the protocol
    # better when the rule and the list are close to the base rules than when
    # they are buried behind the skills and the scene snapshot.
    tool_catalog = tools.catalog()
    if tool_catalog:
        text += "\n\n" + i18n.T("prompt.tools_header") + "\n" + tool_catalog

    # The catalog is always present; a body is only pulled in when the model
    # asks for it (state.py handles that round trip).
    catalog = skills.catalog(state.skill_library())
    if catalog:
        text += "\n\n" + i18n.T("prompt.skills_header") + "\n" + catalog

    include = prefs.include_snapshot if prefs is not None else True
    if include:
        try:
            text += (
                "\n\n" + i18n.T("prompt.snapshot_header") + "\n"
                + snapshot.build_snapshot(context)
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

    Thin wrapper over :func:`materials.assign_material` so that the operator
    and the tool share one implementation.
    """
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    if obj is None:
        return ""
    return materials.assign_material(obj, material)


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
            obj = getattr(context, "active_object", None) or getattr(
                context, "object", None
            )
            material, maps, assigned, _reused = materials.build_pbr_from_folder(
                self.filepath, assign_to=obj
            )
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


def _doctor_report(findings) -> str:
    """The findings as a chat message: a title, the counts and the list."""
    counts = doctor.summary(findings)
    lines = [i18n.T("doctor.title")]
    lines.append(
        i18n.T(
            "doctor.counts",
            errors=counts[doctor.ERROR],
            warnings=counts[doctor.WARNING],
            notes=counts[doctor.INFO],
        )
    )
    lines.append("")
    lines.append(doctor.format_findings(findings))
    return "\n".join(lines)


class GAA_OT_material_doctor(bpy.types.Operator):
    """Проверить материалы сцены и найти в них ошибки"""

    bl_idname = "gaa.material_doctor"
    bl_label = "Доктор материалов"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            findings = doctor.audit_scene()
        except Exception as exc:  # noqa: BLE001 - a check must not kill the UI
            self.report({"ERROR"}, i18n.T("doctor.failed", exc=exc))
            return {"CANCELLED"}

        # The report goes into the chat panel: it is longer than a status
        # line, and the user wants to read it next to the conversation.
        state.add_display("result", _doctor_report(findings))
        counts = doctor.summary(findings)
        self.report(
            {"INFO"},
            i18n.T(
                "doctor.counts",
                errors=counts[doctor.ERROR],
                warnings=counts[doctor.WARNING],
                notes=counts[doctor.INFO],
            ),
        )
        state._redraw()
        return {"FINISHED"}


class GAA_OT_material_fix(bpy.types.Operator):
    """Исправить то, что доктор материалов может починить сам"""

    bl_idname = "gaa.material_fix"
    bl_label = "Починить"
    # UNDO: the whole repair is one Ctrl+Z step.
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            findings = doctor.audit_scene()
            done = []
            for finding in findings:
                if finding.fix not in doctor.SAFE_FIXES:
                    continue
                mat = bpy.data.materials.get(finding.material)
                if mat is None:
                    continue
                message = doctor.fix(mat, finding)
                if message:
                    done.append(message)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("doctor.failed", exc=exc))
            return {"CANCELLED"}

        if not done:
            state.add_display("result", i18n.T("doctor.nothing_to_fix"))
            self.report({"INFO"}, i18n.T("doctor.nothing_to_fix"))
            state._redraw()
            return {"FINISHED"}

        after = doctor.audit_scene()
        counts = doctor.summary(after)
        report = (
            i18n.T("doctor.fixed", count=len(done))
            + "\n"
            + "\n".join(f"- {line}" for line in done)
            + "\n\n"
            + i18n.T(
                "doctor.counts",
                errors=counts[doctor.ERROR],
                warnings=counts[doctor.WARNING],
                notes=counts[doctor.INFO],
            )
        )
        state.add_display("result", report)
        self.report({"INFO"}, i18n.T("doctor.fixed", count=len(done)))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_pack_textures(bpy.types.Operator):
    """Упаковать все файловые текстуры внутрь .blend"""

    bl_idname = "gaa.pack_textures"
    bl_label = "Упаковать текстуры"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            packed = textures.pack_all()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("textures.failed", exc=exc))
            return {"CANCELLED"}

        if not packed:
            message = i18n.T("textures.nothing_to_pack")
        else:
            message = i18n.T("textures.packed", n=len(packed))
            state.add_display("result", message + "\n" + "\n".join(f"- {n}" for n in packed))
        self.report({"INFO"}, message)
        state._redraw()
        return {"FINISHED"}


class GAA_OT_fix_paths(bpy.types.Operator):
    """Сделать пути к текстурам относительными и найти потерянные файлы"""

    bl_idname = "gaa.fix_paths"
    bl_label = "Пути и потерянные"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            relative = textures.make_relative()
            missing_before = textures.find_missing()
            relinked = textures.relink()
            missing_after = textures.find_missing()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("textures.failed", exc=exc))
            return {"CANCELLED"}

        lines = []
        if relative:
            lines.append(i18n.T("textures.relative", n=len(relative)))
        if relinked:
            lines.append(i18n.T("textures.relinked", n=len(relinked)))
        if missing_before and not relinked:
            lines.append(i18n.T("textures.missing", n=len(missing_before)))
            lines += [f"  - {name}" for name in missing_before[:10]]
        elif missing_after:
            lines.append(i18n.T("textures.still_missing", n=len(missing_after)))
            lines += [f"  - {name}" for name in missing_after[:10]]
        if not lines:
            lines.append(i18n.T("textures.paths_ok"))

        message = "\n".join(lines)
        state.add_display("result", message)
        self.report({"INFO"}, lines[0])
        state._redraw()
        return {"FINISHED"}


class GAA_OT_texture_report(bpy.types.Operator):
    """Показать отчёт по текстурам сцены"""

    bl_idname = "gaa.texture_report"
    bl_label = "Отчёт по текстурам"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            message = textures.report()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("textures.failed", exc=exc))
            return {"CANCELLED"}
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("textures.count", n=len(textures.file_images())))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_assign_to_selection(bpy.types.Operator):
    """Назначить материал активного объекта всем выделенным"""

    bl_idname = "gaa.assign_to_selection"
    bl_label = "Назначить выделению"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "active_object", None)
        material = getattr(obj, "active_material", None)
        if material is None:
            self.report({"WARNING"}, i18n.T("bulk.no_material"))
            return {"CANCELLED"}
        try:
            changed = objects.assign_to_selection(material)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("bulk.failed", exc=exc))
            return {"CANCELLED"}

        if not changed:
            self.report({"INFO"}, i18n.T("bulk.nothing_selected"))
            return {"FINISHED"}
        message = i18n.T("bulk.assigned", name=material.name, n=len(changed))
        state.add_display("result", message + "\n" + "\n".join(f"- {n}" for n in changed))
        self.report({"INFO"}, message)
        state._redraw()
        return {"FINISHED"}


class GAA_OT_find_material_users(bpy.types.Operator):
    """Показать, кто использует материал активного объекта"""

    bl_idname = "gaa.find_material_users"
    bl_label = "Кто использует"
    bl_options = {"REGISTER"}

    def execute(self, context):
        obj = getattr(context, "active_object", None)
        material = getattr(obj, "active_material", None)
        if material is None:
            self.report({"WARNING"}, i18n.T("bulk.no_material"))
            return {"CANCELLED"}
        try:
            users = objects.find_users(material)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("bulk.failed", exc=exc))
            return {"CANCELLED"}

        if not users:
            message = i18n.T("bulk.no_users", name=material.name)
        else:
            lines = [i18n.T("bulk.users", name=material.name, n=len(users))]
            for name, index, faces in users:
                lines.append(f"- {name}: слот {index}, {faces} граней")
            message = "\n".join(lines)
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("bulk.users", name=material.name, n=len(users)))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_import_textures(bpy.types.Operator):
    """Импортировать текстуры из папки и подготовить проект"""

    bl_idname = "gaa.import_textures"
    bl_label = "Импорт из папки"
    bl_options = {"REGISTER", "UNDO"}

    # subtype="DIR_PATH" makes Blender's file browser accept a *folder*.
    filepath: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        return context.window_manager.fileselect_add(self)

    def execute(self, context):
        if not self.filepath:
            return {"CANCELLED"}
        try:
            report_data = project.import_and_prepare(self.filepath)
        except Exception as exc:  # noqa: BLE001 - a bad folder must not kill the UI
            self.report({"ERROR"}, i18n.T("project.failed", exc=exc))
            state.add_display("error", i18n.T("project.failed", exc=exc))
            return {"CANCELLED"}

        lines = [i18n.T("project.title")] + [
            f"- {step}" for step in report_data["steps"]
        ]
        problems = report_data["problems"]
        if problems:
            lines.append(i18n.T("project.problems"))
            lines += [f"- {problem}" for problem in problems]
        else:
            lines.append(i18n.T("project.ready"))
        message = "\n".join(lines)
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("project.done", name=report_data["material"]))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_check_export(bpy.types.Operator):
    """Проверить, готов ли проект к передаче"""

    bl_idname = "gaa.check_export"
    bl_label = "Проверка экспорта"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            message = export.report()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("project.failed", exc=exc))
            return {"CANCELLED"}
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("project.checked"))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_tool_confirm(bpy.types.Operator):
    """Выполнить разрушающий инструмент, который запросил ассистент"""

    bl_idname = "gaa.tool_confirm"
    bl_label = "Выполнить"
    # UNDO: the tool changes the scene, and it must be one Ctrl+Z step.
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        chat = state.get_state()
        result = chat.run_pending_tool()
        if result is None:
            return {"CANCELLED"}
        state.add_display("result", result["report"])
        self.report({"INFO"}, i18n.T("msg.tool_ran"))
        # The model continues with the result of the confirmed tool.
        chat._ask_again()
        state._redraw()
        return {"FINISHED"}


class GAA_OT_tool_cancel(bpy.types.Operator):
    """Отклонить разрушающий инструмент, который запросил ассистент"""

    bl_idname = "gaa.tool_cancel"
    bl_label = "Отмена"
    bl_options = {"REGISTER"}

    def execute(self, context):
        chat = state.get_state()
        result = chat.decline_pending_tool()
        if result is None:
            return {"CANCELLED"}
        state.add_display("result", result["report"])
        self.report({"INFO"}, i18n.T("msg.tool_declined_short"))
        # The model continues and tells the user what was not done.
        chat._ask_again()
        state._redraw()
        return {"FINISHED"}


class GAA_OT_check_uv(bpy.types.Operator):
    """Проверить UV-развёртку: плотность текселей, перекрытия, выход за границы"""

    bl_idname = "gaa.check_uv"
    bl_label = "Проверить UV"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            # The world matrix must be fresh: a scale set a moment ago has to
            # be counted, otherwise the density comes out wrong.
            context.view_layer.update()
            objects = [
                obj for obj in context.scene.objects if obj.type == "MESH"
            ]
            reports = [uv.analyze(obj) for obj in objects]
            found = uv.audit(objects)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}

        lines = [i18n.T("uv.title")]
        for report in reports:
            lines.append(uv.format_report(report))
        if found:
            lines.append("")
            lines.append(findings.format_findings(found))
        else:
            lines.append(i18n.T("doctor.clean"))

        message = "\n".join(lines)
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("uv.checked"))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_unwrap_uv(bpy.types.Operator):
    """Развернуть выделенные объекты: швы по углу, развёртка, укладка"""

    bl_idname = "gaa.unwrap_uv"
    bl_label = "Развернуть"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            context.view_layer.update()
            settings = uv.settings_from_scene(context.scene)
            targets = uv.scope_objects(context)
            if not targets:
                self.report({"WARNING"}, i18n.T("uv.no_meshes"))
                return {"CANCELLED"}
            reports = uv.unwrap_objects(targets, settings, context)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}

        done = [r for r in reports if r.get("ok")]
        if not done:
            problems = "; ".join(
                f"{r['object']}: {r.get('error', 'failed')}" for r in reports
            )
            self.report({"ERROR"}, i18n.T("uv.failed", exc=problems))
            return {"CANCELLED"}

        lines = [i18n.T("uv.unwrapped", n=len(done))]
        for report in done:
            lines.append(
                f"- {report['object']}: {report['seams']} швов, "
                f"метод {report['method']}"
            )
        state.add_display("result", "\n".join(lines))
        self.report({"INFO"}, i18n.T("uv.unwrapped", n=len(done)))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_match_density(bpy.types.Operator):
    """Выровнять плотность текселей по всему объекту"""

    bl_idname = "gaa.match_density"
    bl_label = "Плотность"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            context.view_layer.update()
            settings = uv.settings_from_scene(context.scene)
            targets = uv.scope_objects(context)
            if not targets:
                self.report({"WARNING"}, i18n.T("uv.no_meshes"))
                return {"CANCELLED"}
            reports = uv.match_density_scene(targets, settings.density, context)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}

        done = [r for r in reports if r.get("ok")]
        if not done:
            self.report({"ERROR"}, i18n.T("uv.failed", exc="nothing changed"))
            return {"CANCELLED"}

        lines = [i18n.T("uv.density_target", n=int(settings.density))]
        for report in done:
            after = ", ".join(f"{d}" for d in report["after"]) or "-"
            line = f"- {report['object']}: {after} px/м"
            if not report.get("fits", True):
                line += f" (текстура нужна {report['needed_size']}px)"
            lines.append(line)
        state.add_display("result", "\n".join(lines))
        self.report({"INFO"}, i18n.T("uv.density_done", n=len(done)))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_build_atlas(bpy.types.Operator):
    """Собрать общий атлас из островов выделенных объектов"""

    bl_idname = "gaa.build_atlas"
    bl_label = "Собрать атлас"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            context.view_layer.update()
            settings = uv.settings_from_scene(context.scene)
            targets = uv.scope_objects(context)
            if not targets:
                self.report({"WARNING"}, i18n.T("uv.no_meshes"))
                return {"CANCELLED"}
            report = uv.build_atlas(targets, settings, context)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}

        if not report.get("ok"):
            self.report({"ERROR"}, i18n.T("uv.failed", exc=report.get("error")))
            return {"CANCELLED"}

        lines = [
            i18n.T(
                "uv.atlas_done",
                n=report["islands"],
                used=report["used"],
                size=report["atlas"],
            )
        ]
        if report["achieved"]:
            lines.append(
                i18n.T(
                    "uv.atlas_density",
                    achieved=round(report["achieved"], 1),
                    target=int(report["target"]),
                )
            )
        if report["needed_size"] > report["atlas"]:
            lines.append(
                i18n.T("uv.atlas_too_small", size=report["needed_size"])
            )
        message = "\n".join(lines)
        state.add_display("result", message)
        self.report({"INFO"}, lines[0])
        state._redraw()
        return {"FINISHED"}


class GAA_OT_export_uv_kit(bpy.types.Operator):
    """Выгрузить UV-комплект для рисования в другой программе"""

    bl_idname = "gaa.export_uv_kit"
    bl_label = "Выгрузить UV-комплект"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            context.view_layer.update()
            targets = uv.scope_objects(context)
            if not targets:
                self.report({"WARNING"}, i18n.T("uv.no_meshes"))
                return {"CANCELLED"}
            settings = uv.settings_from_scene(context.scene)
            report = exchange.export_kit(targets, None, None, settings)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}

        message = (
            i18n.T("uv.kit_done", folder=report["folder"])
            + "\n"
            + f"- {report['layout']}\n"
            + f"- {report['manifest']}"
        )
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("uv.kit_done", folder=report["folder"]))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_import_atlas(bpy.types.Operator):
    """Поставить нарисованный атлас в материал активного объекта"""

    bl_idname = "gaa.import_atlas"
    bl_label = "Импортировать атлас"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.png", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            return {"CANCELLED"}
        try:
            report = exchange.import_atlas(self.filepath, context.active_object)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}

        if not report.get("ok"):
            self.report({"ERROR"}, i18n.T("uv.failed", exc=report.get("error")))
            return {"CANCELLED"}
        message = i18n.T(
            "uv.atlas_imported",
            image=report["image"],
            material=report["material"],
        )
        state.add_display("result", message)
        self.report({"INFO"}, message)
        state._redraw()
        return {"FINISHED"}


class GAA_OT_bake_textures(bpy.types.Operator):
    """Запечь процедурный материал в текстуры"""

    bl_idname = "gaa.bake_textures"
    bl_label = "Запечь текстуры"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "active_object", None)
        if obj is None:
            self.report({"WARNING"}, i18n.T("bake.no_object"))
            return {"CANCELLED"}

        problems = baking.check_ready(obj)
        blocking = [p for p in problems if p.severity == findings.ERROR]
        if blocking:
            message = i18n.T("bake.not_ready") + "\n" + "\n".join(
                f"- {p.text()}" for p in blocking
            )
            state.add_display("error", message)
            self.report({"ERROR"}, i18n.T("bake.not_ready"))
            return {"CANCELLED"}

        settings = uv.settings_from_scene(context.scene)
        size = int(settings.atlas)
        try:
            report = baking.bake(
                obj,
                targets=("albedo", "normal"),
                size=size,
            )
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("bake.failed", exc=exc))
            return {"CANCELLED"}

        if not report.get("ok"):
            self.report({"ERROR"}, i18n.T("bake.failed", exc=report.get("error")))
            state.add_display("error", i18n.T("bake.failed", exc=report.get("error")))
            return {"CANCELLED"}

        message = i18n.T(
            "bake.done",
            maps=", ".join(report["maps"]),
            obj=report["object"],
            seconds=round(report["seconds"], 1),
        )
        state.add_display("result", message)
        self.report({"INFO"}, i18n.T("bake.done_short"))
        state._redraw()
        return {"FINISHED"}


class GAA_OT_height_to_normal(bpy.types.Operator):
    """Сделать карту нормалей из карты высот"""

    bl_idname = "gaa.height_to_normal"
    bl_label = "Высота → нормали"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.png", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath:
            return {"CANCELLED"}
        try:
            source = bpy.data.images.load(self.filepath, check_existing=True)
            image = heightmap.height_to_normal(source, 1.0, False)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("bake.failed", exc=exc))
            return {"CANCELLED"}

        if image is None:
            self.report({"ERROR"}, i18n.T("bake.failed", exc="the image is empty"))
            return {"CANCELLED"}

        assigned = heightmap.assign_to_material(image, context.active_object)
        message = i18n.T("bake.normal_done", name=image.name)
        if assigned:
            message += " " + i18n.T("bake.normal_assigned")
        state.add_display("result", message)
        self.report({"INFO"}, message)
        state._redraw()
        return {"FINISHED"}


class GAA_OT_node_fix_colorspace(bpy.types.Operator):
    """Поставить правильное цветовое пространство картинки"""

    bl_idname = "gaa.node_fix_colorspace"
    bl_label = "Fix colour space"
    bl_options = {"REGISTER"}

    def execute(self, context):
        node = getattr(context, "node", None)
        image = getattr(node, "image", None) if node is not None else None
        if image is None:
            return {"CANCELLED"}
        wanted = textures.expected_colorspace(image)
        if wanted is None:
            self.report(
                {"INFO"},
                i18n.T("uv.colorspace_unknown", name=image.name),
            )
            return {"FINISHED"}
        before = image.colorspace_settings.name
        if before == wanted:
            self.report({"INFO"}, i18n.T("uv.colorspace_ok", name=image.name))
            return {"FINISHED"}
        try:
            image.colorspace_settings.name = wanted
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            i18n.T("uv.colorspace_set", name=image.name, before=before, after=wanted),
        )
        return {"FINISHED"}


class GAA_OT_node_pack(bpy.types.Operator):
    """Упаковать картинку в .blend"""

    bl_idname = "gaa.node_pack"
    bl_label = "Pack into the .blend"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        node = getattr(context, "node", None)
        image = getattr(node, "image", None) if node is not None else None
        if image is None:
            return {"CANCELLED"}
        if image.packed_file is not None:
            self.report({"INFO"}, i18n.T("uv.already_packed", name=image.name))
            return {"FINISHED"}
        try:
            image.pack()
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}
        self.report({"INFO"}, i18n.T("uv.packed", name=image.name))
        return {"FINISHED"}


class GAA_OT_node_make_relative(bpy.types.Operator):
    """Сделать путь к картинке относительным"""

    bl_idname = "gaa.node_make_relative"
    bl_label = "Make the path relative"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        node = getattr(context, "node", None)
        image = getattr(node, "image", None) if node is not None else None
        if image is None or not image.filepath:
            return {"CANCELLED"}
        if image.filepath.startswith("//"):
            self.report({"INFO"}, i18n.T("uv.already_relative", name=image.name))
            return {"FINISHED"}
        base = uv.blend_dir()
        if not base:
            self.report({"WARNING"}, i18n.T("uv.not_saved"))
            return {"CANCELLED"}
        try:
            relative = bpy.path.relpath(image.filepath, start=bpy.data.filepath)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("uv.failed", exc=exc))
            return {"CANCELLED"}
        image.filepath = relative
        self.report({"INFO"}, i18n.T("uv.relative_set", name=image.name, path=relative))
        return {"FINISHED"}


class GAA_OT_node_set_base_color(bpy.types.Operator):
    """Подключить картинку в Base Color"""

    bl_idname = "gaa.node_set_base_color"
    bl_label = "Set as Base Color"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _node_set_input(context, "Base Color", "Base Color")


class GAA_OT_node_set_roughness(bpy.types.Operator):
    """Подключить картинку в Roughness"""

    bl_idname = "gaa.node_set_roughness"
    bl_label = "Set as Roughness"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        return _node_set_input(context, "Roughness", "Roughness")


class GAA_OT_node_set_normal(bpy.types.Operator):
    """Подключить картинку в Normal (через узел Normal Map)"""

    bl_idname = "gaa.node_set_normal"
    bl_label = "Set as Normal"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        node = getattr(context, "node", None)
        image = getattr(node, "image", None) if node is not None else None
        if image is None:
            return {"CANCELLED"}
        tree = node.id_data
        bsdf = api.find_principled(tree)
        if bsdf is None:
            self.report({"WARNING"}, i18n.T("uv.no_principled"))
            return {"CANCELLED"}
        normal_socket = api.get_input(bsdf, "Normal")
        if normal_socket is None:
            return {"CANCELLED"}

        # A normal map has to go through a Normal Map node - plugging it
        # straight into the Normal input is the classic mistake.
        normal_map = None
        for link in normal_socket.links:
            if link.from_node.type == "NORMAL_MAP":
                normal_map = link.from_node
                break
        if normal_map is None:
            normal_map = tree.nodes.new("ShaderNodeNormalMap")
            normal_map.name = "Normal Map"
            normal_map.location = (node.location.x + 250, node.location.y - 200)
            for link in list(normal_socket.links):
                tree.links.remove(link)
            tree.links.new(normal_map.outputs["Normal"], normal_socket)
        for link in list(normal_map.inputs["Color"].links):
            tree.links.remove(link)
        tree.links.new(node.outputs["Color"], normal_map.inputs["Color"])
        self.report({"INFO"}, i18n.T("uv.node_connected", socket="Normal"))
        return {"FINISHED"}


def _node_set_input(context, socket_name: str, label: str):
    """Connect the image under the mouse to a socket of the Principled BSDF."""
    node = getattr(context, "node", None)
    image = getattr(node, "image", None) if node is not None else None
    if image is None:
        return {"CANCELLED"}
    tree = node.id_data
    bsdf = api.find_principled(tree)
    if bsdf is None:
        return {"CANCELLED"}
    socket = api.get_input(bsdf, socket_name)
    if socket is None:
        return {"CANCELLED"}
    tree.links.new(node.outputs["Color"], socket)
    return {"FINISHED"}


class GAA_OT_procedural_build(bpy.types.Operator):
    """Собрать процедурный материал по описанию"""

    bl_idname = "gaa.procedural_build"
    bl_label = "Процедурный материал"
    bl_options = {"REGISTER", "UNDO"}

    description: StringProperty(name="Описание", default="")
    recipe: StringProperty(name="Рецепт", default="")

    def execute(self, context):
        try:
            text = self.description.strip()
            recipe = self.recipe.strip()
            if not recipe and text:
                matched = procedural.match_recipes(text, limit=1)
                if matched:
                    recipe = matched[0][0].name
            if not recipe:
                self.report(
                    {"WARNING"},
                    i18n.T("procedural.no_recipe", text=text or "?"),
                )
                return {"CANCELLED"}
            material = procedural.build_from_recipe(recipe, self.description[:40] or "")
            if material is None:
                self.report({"ERROR"}, i18n.T("procedural.unknown", recipe=recipe))
                return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, i18n.T("procedural.failed", exc=exc))
            return {"CANCELLED"}

        message = i18n.T("procedural.built", name=material.name)
        state.add_display("result", message)
        self.report({"INFO"}, message)
        state._redraw()
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
    GAA_OT_material_doctor,
    GAA_OT_material_fix,
    GAA_OT_pack_textures,
    GAA_OT_fix_paths,
    GAA_OT_texture_report,
    GAA_OT_assign_to_selection,
    GAA_OT_find_material_users,
    GAA_OT_import_textures,
    GAA_OT_check_export,
    GAA_OT_tool_confirm,
    GAA_OT_tool_cancel,
    GAA_OT_check_uv,
    GAA_OT_unwrap_uv,
    GAA_OT_match_density,
    GAA_OT_build_atlas,
    GAA_OT_export_uv_kit,
    GAA_OT_import_atlas,
    GAA_OT_bake_textures,
    GAA_OT_height_to_normal,
    GAA_OT_node_fix_colorspace,
    GAA_OT_node_pack,
    GAA_OT_node_make_relative,
    GAA_OT_node_set_base_color,
    GAA_OT_node_set_roughness,
    GAA_OT_node_set_normal,
    GAA_OT_procedural_build,
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
    "gaa.material_doctor": "op.material_doctor",
    "gaa.material_fix": "op.material_fix",
    "gaa.pack_textures": "op.pack_textures",
    "gaa.fix_paths": "op.fix_paths",
    "gaa.texture_report": "op.texture_report",
    "gaa.assign_to_selection": "op.assign_to_selection",
    "gaa.find_material_users": "op.find_users",
    "gaa.import_textures": "op.import_textures",
    "gaa.check_export": "op.check_export",
    "gaa.tool_confirm": "op.tool_confirm",
    "gaa.tool_cancel": "op.tool_cancel",
    "gaa.check_uv": "op.check_uv",
    "gaa.unwrap_uv": "op.unwrap_uv",
    "gaa.match_density": "op.match_density",
    "gaa.build_atlas": "op.build_atlas",
    "gaa.export_uv_kit": "op.export_uv_kit",
    "gaa.import_atlas": "op.import_atlas",
    "gaa.bake_textures": "op.bake_textures",
    "gaa.height_to_normal": "op.height_to_normal",
    "gaa.node_fix_colorspace": "op.node_fix_colorspace",
    "gaa.node_pack": "op.node_pack",
    "gaa.node_make_relative": "op.node_make_relative",
    "gaa.node_set_base_color": "op.node_set_base_color",
    "gaa.node_set_roughness": "op.node_set_roughness",
    "gaa.node_set_normal": "op.node_set_normal",
    "gaa.procedural_build": "op.procedural_build",
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
