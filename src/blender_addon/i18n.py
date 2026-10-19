"""UI strings: RU/EN tables behind a tiny ``T()`` helper.

Design (groundwork for more languages later):

* Every user-visible string lives in :data:`STRINGS` under a stable key —
  code never hard-codes display text.
* :func:`T` resolves the current language from the add-on preferences on
  every call, so switching the language updates the UI on the next redraw
  without re-registering panels.
* A missing key falls back to the default language, then to the key itself —
  a typo degrades to a visible oddity, never to a crash.
* Adding a language = adding one dict to :data:`STRINGS` and one enum item in
  preferences; no code changes elsewhere.

Known limitation: RNA-baked strings (property tooltips/descriptions,
enum item names) are registered once, so a few preference tooltips stay in
their original language until a full re-registration — see README.
"""

from __future__ import annotations

DEFAULT_LANG = "ru"

STRINGS = {
    "ru": {
        # -- chat UI (ui.py) --------------------------------------------
        "chat.title": "AI Assistant — чат",
        "status.busy": "Генерация…",
        "status.error": "Ошибка",
        "status.ready_model": "Готов · {model}",
        "status.ready": "Готов к запуску",
        "history.empty_1": "Спросите что-нибудь про сцену,",
        "history.empty_2": "модель, материалы или bpy-код",
        "pending.title": "Код от AI готов к запуску:",
        "pending.more": "…ещё {n} строк(и)",
        "pending.ran": "Выполнен · Ctrl+Z — отмена",
        "input.placeholder": "Сообщение…",
        "btn.clear": "Очистить",
        "btn.settings": "Настройки",
        "role.user": "Вы",
        "role.assistant": "AI",
        "role.error": "Ошибка",
        "role.result": "Код",
        # -- operators (operators.py) ------------------------------------
        "op.send.label": "Отправить",
        "op.send.desc": "Отправить текущее сообщение ассистенту",
        "op.stop.label": "Стоп",
        "op.stop.desc": "Остановить генерацию и убить воркер",
        "op.clear.label": "Очистить",
        "op.clear.desc": "Очистить историю чата",
        "op.settings.label": "Настройки",
        "op.settings.desc": "Открыть настройки аддона",
        "op.chat_window.label": "Окно чата",
        "op.chat_window.desc": "Открыть чат с AI-ассистентом в отдельном окне",
        "op.run_code.label": "Выполнить код",
        "op.run_code.desc": "Выполнить последний bpy-код из ответа ассистента в текущей сцене",
        "op.code_to_text.label": "В Text Editor",
        "op.code_to_text.desc": "Сохранить последний код ассистента в Text Editor",
        "op.open_shader.label": "Shader Editor",
        "op.open_shader.desc": "Открыть Shader Editor для материала активного объекта",
        "op.open_image.label": "Image Editor",
        "op.open_image.desc": "Открыть Image Editor для текстуры активного материала",
        "op.use_preset.label": "Пресет материала",
        "op.use_preset.desc": "Подставить готовый запрос в поле ввода чата",
        "op.pbr_folder.label": "PBR из папки",
        "op.pbr_folder.desc": "Собрать PBR-материал из текстурных карт в выбранной папке",
        "msg.code_done": "Код выполнен.",
        "msg.output": "Вывод:",
        "msg.code_error": "Ошибка выполнения кода:",
        "msg.code_saved": "Код сохранён в текст «{name}»",
        "msg.window_failed": "Не удалось открыть окно чата: {exc}",
        "msg.settings_failed": "Не удалось открыть настройки: {exc}",
        "msg.send_failed": "Не удалось отправить запрос",
        "msg.editor_no_area": "Не нашёл область экрана для редактора",
        "msg.editor_failed": "Не удалось настроить редактор: {exc}",
        "msg.no_material": "У активного объекта нет материала",
        "msg.no_image": "В файле нет изображений",
        "msg.shader_opened": "Shader Editor: материал «{name}»",
        "msg.image_opened": "Image Editor: «{name}»",
        "msg.pbr_done": "Материал «{name}»: {maps}",
        "msg.pbr_assigned": "назначен объекту «{obj}»",
        "msg.pbr_empty": "В папке не нашлось карт (albedo/base, rough, metal, normal, orm, emit, height)",
        "msg.pbr_failed": "Не удалось собрать материал: {exc}",
        "prompt.snapshot_header": "## Снимок текущей сцены",
        # -- material presets (ui.py + operators.py) ----------------------
        "presets.title": "Пресеты материалов",
        "preset.matte": "Матовый",
        "preset.metal": "Металл",
        "preset.glass": "Стекло",
        "preset.emission": "Эмиссия",
        "preset.pbr": "PBR",
        "preset.matte.text": (
            "Сделай выделенному объекту матовый материал: Principled BSDF, "
            "Base Color тёмно-красный, Roughness 0.9, Metallic 0. Назначь его объекту."
        ),
        "preset.metal.text": (
            "Сделай выделенному объекту металлический материал: Metallic 1.0, "
            "Roughness 0.25, светлый Base Color. Назначь его объекту."
        ),
        "preset.glass.text": (
            "Сделай выделенному объекту стеклянный материал: Principled BSDF, "
            "Transmission Weight 1.0, Roughness 0.0, IOR 1.45, Base Color белый. "
            "Назначь его объекту."
        ),
        "preset.emission.text": (
            "Сделай выделенному объекту эмиссионный материал: Emission Color "
            "тёплый белый, Emission Strength 5.0."
        ),
        "preset.pbr.text": (
            "Создай выделенному объекту PBR-материал из текстур в папке <путь>: "
            "albedo подключи как Base Color (sRGB), normal/roughness/metallic — "
            "как данные (Non-Color). Используй data API и не плоди дубликаты материалов."
        ),
        # -- PBR from folder (materials.py + operators.py) ----------------
        "pbr.map.base": "основной цвет",
        "pbr.map.normal": "нормаль",
        "pbr.map.rough": "шероховатость",
        "pbr.map.metal": "металличность",
        "pbr.map.orm": "ORM",
        "pbr.map.emit": "эмиссия",
        "pbr.map.height": "высота",
        # -- state (state.py) --------------------------------------------
        "err.no_worker": "Не найден asset_core/worker.py рядом с аддоном",
        "err.no_python": "Не найден Python 3.9+ (в комплекте Blender или в PATH)",
        "err.worker_start": "Не удалось запустить воркер: {exc}",
        "err.worker_died": "Работник неожиданно завершился (код {code}). Лог: {log}",
        "err.unknown": "Неизвестная ошибка воркера",
        "msg.empty_reply": "(пустой ответ)",
        "msg.stopped": "[остановлено]",
        "msg.interrupted": "[ответ прерван]",
        # -- snapshot (snapshot.py) --------------------------------------
        "snapshot.more_objects": "... и ещё {n} объектов",
        "snapshot.more_materials": "... и ещё {n} материалов",
        "snapshot.more_images": "... и ещё {n} изображений",
        # -- preferences (preferences.py) --------------------------------
        "prefs.language": "Язык интерфейса",
        "prefs.backend": "Бэкенд",
        "prefs.ollama_host": "Адрес Ollama",
        "prefs.ollama_model": "Модель Ollama",
        "prefs.ollama_hint": "Модель: ollama pull qwen2.5-coder:7b",
        "prefs.api_key": "API-ключ",
        "prefs.api_model": "Модель API",
        "prefs.key_warning": "Ключ хранится в настройках Blender открытым текстом",
        "prefs.context_chars": "Бюджет контекста (символов)",
        "prefs.timeout": "Таймаут (сек)",
        "prefs.snapshot": "Отправлять снимок сцены",
        "prefs.chat_box": "Окно чата",
    },
    "en": {
        # -- chat UI ------------------------------------------------------
        "chat.title": "AI Assistant — chat",
        "status.busy": "Generating…",
        "status.error": "Error",
        "status.ready_model": "Ready · {model}",
        "status.ready": "Ready",
        "history.empty_1": "Ask anything about the scene,",
        "history.empty_2": "the model, materials or bpy code",
        "pending.title": "AI code ready to run:",
        "pending.more": "…{n} more line(s)",
        "pending.ran": "Ran · Ctrl+Z to undo",
        "input.placeholder": "Message…",
        "btn.clear": "Clear",
        "btn.settings": "Settings",
        "role.user": "You",
        "role.assistant": "AI",
        "role.error": "Error",
        "role.result": "Code",
        # -- operators ----------------------------------------------------
        "op.send.label": "Send",
        "op.send.desc": "Send the current message to the assistant",
        "op.stop.label": "Stop",
        "op.stop.desc": "Stop generation and kill the worker",
        "op.clear.label": "Clear",
        "op.clear.desc": "Clear the chat history",
        "op.settings.label": "Settings",
        "op.settings.desc": "Open the add-on settings",
        "op.chat_window.label": "Chat window",
        "op.chat_window.desc": "Open the AI assistant chat in a separate window",
        "op.run_code.label": "Run code",
        "op.run_code.desc": "Run the last bpy code from an assistant reply in the current scene",
        "op.code_to_text.label": "To Text Editor",
        "op.code_to_text.desc": "Save the last assistant code to a Text Editor block",
        "op.open_shader.label": "Shader Editor",
        "op.open_shader.desc": "Open the Shader Editor on the active object's material",
        "op.open_image.label": "Image Editor",
        "op.open_image.desc": "Open the Image Editor on the active material's texture",
        "op.use_preset.label": "Material preset",
        "op.use_preset.desc": "Insert a ready-made request into the chat input",
        "op.pbr_folder.label": "PBR from folder",
        "op.pbr_folder.desc": "Build a PBR material from the texture maps in a chosen folder",
        "msg.code_done": "Code executed.",
        "msg.output": "Output:",
        "msg.code_error": "Code execution error:",
        "msg.code_saved": "Code saved to text «{name}»",
        "msg.window_failed": "Failed to open the chat window: {exc}",
        "msg.settings_failed": "Failed to open the settings: {exc}",
        "msg.send_failed": "Failed to send the request",
        "msg.editor_no_area": "No screen area available for the editor",
        "msg.editor_failed": "Failed to set up the editor: {exc}",
        "msg.no_material": "The active object has no material",
        "msg.no_image": "The file has no images",
        "msg.shader_opened": "Shader Editor: material «{name}»",
        "msg.image_opened": "Image Editor: «{name}»",
        "msg.pbr_done": "Material «{name}»: {maps}",
        "msg.pbr_assigned": "assigned to «{obj}»",
        "msg.pbr_empty": "No maps found in the folder (albedo/base, rough, metal, normal, orm, emit, height)",
        "msg.pbr_failed": "Could not build the material: {exc}",
        "prompt.snapshot_header": "## Current scene snapshot",
        # -- material presets ---------------------------------------------
        "presets.title": "Material presets",
        "preset.matte": "Matte",
        "preset.metal": "Metal",
        "preset.glass": "Glass",
        "preset.emission": "Emission",
        "preset.pbr": "PBR",
        "preset.matte.text": (
            "Give the selected object a matte material: Principled BSDF, "
            "dark red Base Color, Roughness 0.9, Metallic 0. Assign it to the object."
        ),
        "preset.metal.text": (
            "Give the selected object a metallic material: Metallic 1.0, "
            "Roughness 0.25, light Base Color. Assign it to the object."
        ),
        "preset.glass.text": (
            "Give the selected object a glass material: Principled BSDF, "
            "Transmission Weight 1.0, Roughness 0.0, IOR 1.45, white Base Color. "
            "Assign it to the object."
        ),
        "preset.emission.text": (
            "Give the selected object an emissive material: warm white "
            "Emission Color, Emission Strength 5.0."
        ),
        "preset.pbr.text": (
            "Build a PBR material for the selected object from textures in <path>: "
            "albedo as Base Color (sRGB), normal/roughness/metallic as data "
            "(Non-Color). Use the data API and do not create duplicate materials."
        ),
        # -- PBR from folder ----------------------------------------------
        "pbr.map.base": "base color",
        "pbr.map.normal": "normal",
        "pbr.map.rough": "roughness",
        "pbr.map.metal": "metallic",
        "pbr.map.orm": "ORM",
        "pbr.map.emit": "emission",
        "pbr.map.height": "height",
        # -- state --------------------------------------------------------
        "err.no_worker": "asset_core/worker.py not found next to the add-on",
        "err.no_python": "Python 3.9+ not found (bundled with Blender or on PATH)",
        "err.worker_start": "Failed to start the worker: {exc}",
        "err.worker_died": "The worker exited unexpectedly (code {code}). Log: {log}",
        "err.unknown": "Unknown worker error",
        "msg.empty_reply": "(empty reply)",
        "msg.stopped": "[stopped]",
        "msg.interrupted": "[reply interrupted]",
        # -- snapshot -----------------------------------------------------
        "snapshot.more_objects": "... and {n} more objects",
        "snapshot.more_materials": "... and {n} more materials",
        "snapshot.more_images": "... and {n} more images",
        # -- preferences --------------------------------------------------
        "prefs.language": "Interface language",
        "prefs.backend": "Backend",
        "prefs.ollama_host": "Ollama host",
        "prefs.ollama_model": "Ollama model",
        "prefs.ollama_hint": "Model: ollama pull qwen2.5-coder:7b",
        "prefs.api_key": "API key",
        "prefs.api_model": "API model",
        "prefs.key_warning": "The key is stored in Blender preferences as plain text",
        "prefs.context_chars": "Context budget (characters)",
        "prefs.timeout": "Timeout (sec)",
        "prefs.snapshot": "Send the scene snapshot",
        "prefs.chat_box": "Chat window",
    },
}


def get_lang() -> str:
    """Current language code ('ru'/'en'), read from the add-on preferences.

    Falls back to :data:`DEFAULT_LANG` everywhere the preferences are not
    available (headless runs, tests, pre-registration draws).
    """
    try:
        import bpy

        addon = bpy.context.preferences.addons.get(__package__)
        if addon is not None and addon.preferences is not None:
            lang = str(getattr(addon.preferences, "language", "")).lower()
            if lang in STRINGS:
                return lang
    except Exception:
        pass
    return DEFAULT_LANG


def T(key: str, **fmt) -> str:
    """Localized string for ``key`` in the current language (with .format())."""
    lang = get_lang()
    text = STRINGS[lang].get(key)
    if text is None:
        text = STRINGS[DEFAULT_LANG].get(key, key)
    return text.format(**fmt) if fmt else text
