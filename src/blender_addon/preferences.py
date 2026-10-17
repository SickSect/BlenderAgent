"""Add-on preferences: backend switch (local Ollama / external API by key)."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)


class GAA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    backend: EnumProperty(
        name="Бэкенд",
        items=[
            ("OLLAMA", "Ollama (локальная)", "Локальная модель через Ollama"),
            ("OPENAI", "Внешний API", "OpenAI-совместимый API по ключу"),
        ],
        default="OLLAMA",
    )

    ollama_host: StringProperty(
        name="Адрес Ollama", default="http://localhost:11434"
    )
    ollama_model: StringProperty(
        name="Модель Ollama", default="qwen2.5-coder:7b"
    )

    api_base_url: StringProperty(
        name="Base URL", default="https://api.openai.com/v1"
    )
    api_key: StringProperty(name="API-ключ", subtype="PASSWORD", default="")
    api_model: StringProperty(name="Модель API", default="gpt-4o-mini")

    temperature: FloatProperty(
        name="Temperature", default=0.5, min=0.0, max=2.0
    )
    include_snapshot: BoolProperty(
        name="Отправлять снимок сцены",
        description="Прикладывать к запросу состояние текущей сцены",
        default=True,
    )
    context_chars: IntProperty(
        name="Бюджет контекста (символов)",
        description="Сколько символов истории отправляется модели",
        default=24000,
        min=2000,
        max=200000,
    )
    timeout: IntProperty(
        name="Таймаут (сек)", default=180, min=10, max=600
    )

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.prop(self, "backend", expand=True)

        box = layout.box()
        if self.backend == "OLLAMA":
            box.prop(self, "ollama_host")
            box.prop(self, "ollama_model")
            box.label(text="Модель: ollama pull qwen2.5-coder:7b", icon="INFO")
        else:
            box.prop(self, "api_base_url")
            box.prop(self, "api_key")
            box.prop(self, "api_model")
            box.label(
                text="Ключ хранится в настройках Blender открытым текстом",
                icon="ERROR",
            )

        col = layout.column(align=True)
        col.prop(self, "temperature")
        col.prop(self, "context_chars")
        col.prop(self, "timeout")
        layout.prop(self, "include_snapshot")


def get_prefs(context=None):
    """Preferences of this add-on, or None when unavailable."""
    context = context or bpy.context
    preferences = getattr(context, "preferences", None)
    if preferences is None:
        return None
    addon = preferences.addons.get(__package__)
    if addon is None:
        return None
    return addon.preferences


def config_dict(prefs) -> dict:
    """Plain-dict backend config handed to the worker with every request."""
    if prefs is None:
        return {"kind": "ollama", "host": "http://localhost:11434"}
    if prefs.backend == "OPENAI":
        return {
            "kind": "openai",
            "host": prefs.api_base_url,
            "model": prefs.api_model,
            "api_key": prefs.api_key,
            "temperature": prefs.temperature,
            "timeout": prefs.timeout,
        }
    return {
        "kind": "ollama",
        "host": prefs.ollama_host,
        "model": prefs.ollama_model,
        "temperature": prefs.temperature,
        "timeout": prefs.timeout,
    }


_classes = (GAA_Preferences,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
