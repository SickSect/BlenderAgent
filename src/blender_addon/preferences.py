"""Add-on preferences: language, backend switch (local Ollama / external API)."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from . import i18n


def _on_language_update(self, _context):
    """Operator labels/tooltips are RNA-baked: re-register to retranslate them."""
    # Local import: operators.py imports this module at load time.
    from . import operators

    operators.refresh_labels()


class GAA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    language: EnumProperty(
        name="Язык / Language",
        description="Язык интерфейса аддона / Add-on interface language",
        items=[
            ("RU", "Русский", "Интерфейс аддона на русском"),
            ("EN", "English", "Add-on interface in English"),
        ],
        default="RU",
        update=_on_language_update,
    )

    backend: EnumProperty(
        name="Бэкенд / Backend",
        items=[
            ("OLLAMA", "Ollama", "Локальная модель через Ollama / Local model via Ollama"),
            ("OPENAI", "OpenAI API", "OpenAI-совместимый API по ключу / OpenAI-compatible API by key"),
        ],
        default="OLLAMA",
    )

    ollama_host: StringProperty(
        name="Адрес Ollama / Ollama host",
        description=(
            "Корень адреса Ollama, без /api — аддон сам допишет /api/chat. "
            "Обычно http://localhost:11434 / The root of the Ollama address, "
            "without /api - the add-on appends /api/chat itself"
        ),
        default="http://localhost:11434",
    )
    ollama_model: StringProperty(
        name="Модель Ollama / Ollama model", default="qwen2.5-coder:7b"
    )

    api_base_url: StringProperty(
        name="Base URL",
        description=(
            "Базовый URL OpenAI-совместимого API - с /v1, но БЕЗ "
            "/chat/completions (его аддон дописывает). Примеры: "
            "https://api.openai.com/v1, https://openrouter.ai/api/v1, "
            "https://api.groq.com/openai/v1, http://localhost:1234/v1 "
            "(LM Studio). В поле «Модель» - точное имя модели из списка "
            "провайдера / The base URL of an OpenAI-compatible API - with /v1, "
            "but WITHOUT /chat/completions (the add-on appends it)"
        ),
        default="https://api.openai.com/v1",
    )
    api_key: StringProperty(
        name="API-ключ / API key", subtype="PASSWORD", default=""
    )
    api_model: StringProperty(
        name="Модель API / API model",
        description=(
            "Точное имя модели из списка провайдера: gpt-4o-mini, "
            "deepseek-chat, qwen/qwen2.5-coder-32b-instruct (OpenRouter) / "
            "The exact model name from the provider's list"
        ),
        default="gpt-4o-mini",
    )

    temperature: FloatProperty(
        name="Temperature", default=0.5, min=0.0, max=2.0
    )
    num_ctx: IntProperty(
        name="Контекст модели, токенов / Model context (tokens)",
        description=(
            "Размер контекстного окна, который запрашивается у бэкенда (num_ctx). "
            "Больше — больше места под историю диалога и инструкции-skills, "
            "но и больше памяти и времени на генерацию / "
            "Context window requested from the backend (num_ctx); larger leaves "
            "room for history and skill instructions, at the cost of memory"
        ),
        default=16384,
        min=2048,
        max=131072,
        step=1024,
    )
    include_snapshot: BoolProperty(
        name="Отправлять снимок сцены / Send the scene snapshot",
        description="Прикладывать к запросу состояние текущей сцены / Attach the current scene state to every request",
        default=True,
    )
    context_chars: IntProperty(
        name="Бюджет контекста, символов / Context budget (characters)",
        description="Сколько символов истории отправляется модели / How many characters of history are sent to the model",
        default=24000,
        min=2000,
        max=200000,
    )
    chat_width: IntProperty(
        name="Ширина окна чата / Chat window width (px)",
        description=(
            "Ширина всплывающего окна чата (Ctrl+Alt+A), включая поле ввода "
            "и историю; переносы текста подстраиваются под неё / "
            "Popup chat width (Ctrl+Alt+A); text wrapping follows it"
        ),
        default=560,
        min=300,
        max=1400,
        soft_max=900,
        step=10,
    )
    timeout: IntProperty(
        name="Таймаут, сек / Timeout (sec)", default=180, min=10, max=600
    )

    def draw(self, context):
        layout = self.layout

        layout.prop(self, "language", text=i18n.T("prefs.language"))

        row = layout.row(align=True)
        row.prop(self, "backend", expand=True)

        box = layout.box()
        if self.backend == "OLLAMA":
            box.prop(self, "ollama_host", text=i18n.T("prefs.ollama_host"))
            box.prop(self, "ollama_model", text=i18n.T("prefs.ollama_model"))
            box.label(text=i18n.T("prefs.ollama_hint"), icon="INFO")
        else:
            box.prop(self, "api_base_url")
            box.prop(self, "api_key", text=i18n.T("prefs.api_key"))
            box.prop(self, "api_model", text=i18n.T("prefs.api_model"))
            box.label(
                text=i18n.T("prefs.key_warning"),
                icon="ERROR",
            )

        col = layout.column(align=True)
        col.prop(self, "temperature")
        col.prop(self, "num_ctx", text=i18n.T("prefs.num_ctx"))
        col.prop(self, "context_chars", text=i18n.T("prefs.context_chars"))
        col.prop(self, "timeout", text=i18n.T("prefs.timeout"))
        layout.prop(self, "include_snapshot", text=i18n.T("prefs.snapshot"))

        box = layout.box()
        box.label(text=i18n.T("prefs.chat_box"), icon="WINDOW")
        box.prop(self, "chat_width")


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
        return {
            "kind": "ollama",
            "host": "http://localhost:11434",
            "num_ctx": 16384,
            "lang": "ru",
        }
    lang = (prefs.language or "RU").lower()
    if prefs.backend == "OPENAI":
        return {
            "kind": "openai",
            "host": prefs.api_base_url,
            "model": prefs.api_model,
            "api_key": prefs.api_key,
            "temperature": prefs.temperature,
            "num_ctx": int(prefs.num_ctx),
            "timeout": prefs.timeout,
            "lang": lang,
        }
    return {
        "kind": "ollama",
        "host": prefs.ollama_host,
        "model": prefs.ollama_model,
        "temperature": prefs.temperature,
        "num_ctx": int(prefs.num_ctx),
        "timeout": prefs.timeout,
        "lang": lang,
    }


_classes = (GAA_Preferences,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
