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

from . import i18n, preferences, presets, state

# EVENT_A ("A" in a circle) — closest thing to an "AI" badge; BOT does not
# exist as an icon in Blender 4.x/5.x.
_ROLE_ICONS = {"user": "USER", "assistant": "EVENT_A", "error": "ERROR", "result": "INFO"}
_ROLE_KEYS = {"user": "role.user", "assistant": "role.assistant", "error": "role.error", "result": "role.result"}
_WRAP_WIDTH = 54          # chars — narrow sidebars
_POPUP_FONT_WIDTH = 7     # approx px per char at 100% UI scale
_POPUP_PADDING = 90       # px reserved for box borders/paddings
_MAX_DETAIL_LINES = 60
_CODE_PREVIEW_LINES = 14
# Material prompt presets (labels/texts come from i18n: ``preset.<id>`` and
# ``preset.<id>.text``). Kept here so ``operators.py`` stays UI-agnostic.
_PRESET_IDS = ("matte", "metal", "glass", "emission", "pbr")


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
        # The whole conversation - what the model was told and what it said -
        # in one text file, for looking into a result that came out wrong.
        row = layout.row(align=True)
        row.scale_y = 0.85
        row.operator("gaa.history_to_text", icon="TEXT")
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
        if getattr(chat, "code_collapsed", False):
            # The block in the chat is already a one-line marker: showing the
            # preview again would just take the screen.
            row = box.row(align=True)
            row.label(text=i18n.T("pending.collapsed"), icon="CHECKMARK")
            row.operator("gaa.code_to_text", text="", icon="TEXT")
        else:
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
                # The script has already been executed for this reply; running
                # it again would duplicate whatever it created (see operators.py).
                row.label(text=i18n.T("pending.ran"), icon="CHECKMARK")
            else:
                row.operator("gaa.run_code", icon="PLAY")
            row.operator("gaa.code_to_text", text="", icon="TEXT")
            # Hide the block in the chat without running it.
            row.operator("gaa.hide_code", text="", icon="X")

    # -- confirmation of a destructive tool --------------------------------
    pending = getattr(chat, "pending_confirmation", None)
    if pending:
        box = layout.box()
        box.label(
            text=i18n.T("msg.tool_confirm_title", name=pending.get("title", "")),
            icon="ERROR",
        )
        description = pending.get("description", "")
        if description:
            for line in textwrap.wrap(description, width=wrap_width)[:4]:
                box.label(text=line)
        preview = pending.get("preview", "")
        if preview:
            box.label(text=i18n.T("msg.tool_confirm_preview", preview=preview))
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("gaa.tool_confirm", icon="CHECKMARK")
        row.operator("gaa.tool_cancel", icon="X")

    # -- input -----------------------------------------------------------
    layout.prop(wm, "gaa_input", text="", placeholder=i18n.T("input.placeholder"))

    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator("gaa.send", icon="PLAY")
    row.operator("gaa.stop", text="", icon="PAUSE")

    # -- material prompt presets -----------------------------------------
    box = layout.box()
    box.label(text=i18n.T("presets.title"), icon="PRESET")
    grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for preset_id in _PRESET_IDS:
        props = grid.operator("gaa.use_preset", text=i18n.T("preset." + preset_id))
        props.preset = preset_id
    # Build a material straight from a folder of texture maps — the preset
    # above only *asks* the assistant to do it; this button does it itself.
    box.operator("gaa.pbr_from_folder", icon="FILE_FOLDER")

    # Apply textures from folder to existing materials on selected objects.
    row = box.row(align=True)
    row.operator("gaa.apply_textures_from_folder", icon="IMPORT")

    # The material doctor: find what is wrong with the materials of the
    # scene. The second button applies the repairs it can make on its own.
    row = box.row(align=True)
    row.operator("gaa.material_doctor", icon="CHECKMARK")
    row.operator("gaa.material_fix", icon="FILE_REFRESH")

    # Textures: make the project travel with its textures, and keep the paths
    # portable. These three are the everyday hygiene of a .blend file.
    box = layout.box()
    box.label(text=i18n.T("textures.box"), icon="TEXTURE")
    row = box.row(align=True)
    row.operator("gaa.pack_textures", icon="PACKAGE")
    row.operator("gaa.fix_paths", icon="FILE_REFRESH")
    row.operator("gaa.texture_report", icon="INFO")

    # Mass operations: the material of the active object onto the whole
    # selection, and who else uses that material.
    box = layout.box()
    box.label(text=i18n.T("bulk.box"), icon="GROUP")
    row = box.row(align=True)
    row.operator("gaa.assign_to_selection", icon="GROUP")
    row.operator("gaa.find_material_users", icon="QUESTION")

    # The whole workflow in one click: a texture folder in - a ready project
    # out (material, assignment, packing, export check).
    box = layout.box()
    box.label(text=i18n.T("project.title"), icon="IMPORT")
    row = box.row(align=True)
    row.operator("gaa.import_textures", icon="FILE_FOLDER")
    row.operator("gaa.check_export", icon="EXPORT")
    # The last mile: the file the engine opens.
    export_row = box.row(align=True)
    glb = export_row.operator("gaa.export_asset", text="GLB", icon="EXPORT")
    glb.format = "glb"
    fbx = export_row.operator("gaa.export_asset", text="FBX", icon="EXPORT")
    fbx.format = "fbx"

    # UV: the texel density is the one problem that is invisible in the
    # editor and very visible in a game.
    box = layout.box()
    box.label(text=i18n.T("uv.title"), icon="UV")
    scene = getattr(context, "scene", None)
    if scene is not None:
        layout.prop(scene, "gaa_uv_angle", text=i18n.T("uv.angle"))
        layout.prop(
            scene,
            "gaa_uv_use_existing_seams",
            text=i18n.T("uv.own_seams"),
        )
    row = box.row(align=True)
    row.operator("gaa.unwrap_uv", icon="GROUP_UVS")
    row.operator("gaa.match_density", icon="FULLSCREEN_ENTER")
    row.operator("gaa.check_uv", icon="CHECKMARK")
    row = box.row(align=True)
    row.operator("gaa.build_atlas", icon="TEXTURE")
    row = box.row(align=True)
    row.operator("gaa.export_uv_kit", icon="EXPORT")
    row.operator("gaa.import_atlas", icon="IMPORT")

    # Baking: turn a procedural material into textures the game can read.
    box = layout.box()
    box.label(text=i18n.T("bake.title"), icon="RENDER_STILL")
    row = box.row(align=True)
    row.operator("gaa.bake_textures", icon="RENDER_STILL")
    row.operator("gaa.height_to_normal", icon="NORMALS_VERTEX")
    # The bake renders every pixel: the size is the whole cost. The settings
    # live on the scene, not on the operator - a property drawn on an operator
    # instance is thrown away on the next redraw.
    scene = getattr(context, "scene", None)
    if scene is not None:
        settings = box.row(align=True)
        settings.scale_y = 0.85
        settings.prop(scene, "gaa_bake_size", text="")
        settings.prop(scene, "gaa_bake_samples", text="")
        settings.prop(scene, "gaa_bake_maps", text="")
        settings.prop(scene, "gaa_bake_naming", text="")
    # The two finishing touches of the pipeline.
    finish = box.row(align=True)
    finish.operator("gaa.pack_orm", icon="TEXTURE")
    finish.operator("gaa.render_preview", icon="RENDER_STILL")

    # Procedural materials: built from a description, graph assembled by code.
    box = layout.box()
    box.label(text=i18n.T("procedural.title"), icon="NODE_MATERIAL")
    box.operator("gaa.procedural_build", icon="ADD")
    # The author's own materials: the layered graphs a small model cannot
    # invent. A dropdown of the presets.
    if presets.all_presets():
        box.operator_menu_enum(
            "gaa.preset_build",
            "preset",
            text=i18n.T("procedural.presets"),
        )

    row = layout.row(align=True)
    row.operator("gaa.clear", icon="TRASH")
    row.operator("gaa.settings", icon="PREFERENCES")
    # The quick guide: it lives in the add-on, so it always matches the
    # installed code.
    row.operator("gaa.open_help", icon="HELP")

    # Materials shortcut: jump straight to the nodes/texture of the active
    # object's material (see operators.py — screen area / space juggling).
    row = layout.row(align=True)
    row.operator("gaa.open_shader_editor", icon="NODETREE")
    row.operator("gaa.open_image_editor", icon="IMAGE_DATA")
    # The scripts: the code of the assistant lands in a text block, and the
    # Text Editor is in another workspace - this opens it right here.
    row.operator("gaa.open_text_editor", icon="SCRIPT")

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
