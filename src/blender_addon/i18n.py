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
        "op.material_doctor.label": "Доктор материалов",
        "op.material_doctor.desc": "Проверить материалы сцены и найти ошибки",
        "op.material_fix.label": "Починить",
        "op.material_fix.desc": "Исправить то, что доктор может починить сам",
        "op.pack_textures.label": "Упаковать",
        "op.pack_textures.desc": "Упаковать все файловые текстуры внутрь .blend",
        "op.fix_paths.label": "Пути",
        "op.fix_paths.desc": "Сделать пути относительными и перелинковать потерянные файлы",
        "op.texture_report.label": "Отчёт",
        "op.texture_report.desc": "Показать отчёт по текстурам сцены",
        "op.assign_to_selection.label": "Назначить выделению",
        "op.assign_to_selection.desc": "Назначить материал активного объекта всем выделенным",
        "op.find_users.label": "Кто использует",
        "op.find_users.desc": "Показать объекты, которые используют материал активного объекта",
        "op.import_textures.label": "Импорт из папки",
        "op.import_textures.desc": "Собрать материал из папки с текстурами, назначить его и подготовить проект",
        "op.check_export.label": "Проверка экспорта",
        "op.check_export.desc": "Проверить, готов ли проект к передаче: потерянные файлы, пути, UV, шейдеры",
        "op.check_uv.label": "Проверить UV",
        "op.check_uv.desc": "Проверить развёртку: плотность текселей, перекрытия, выход за границы",
        "op.unwrap_uv.label": "Развернуть",
        "op.unwrap_uv.desc": "Поставить швы по углу, развернуть и уложить острова",
        "op.match_density.label": "Плотность",
        "op.match_density.desc": "Выровнять плотность текселей по островам объекта",
        "op.build_atlas.label": "Атлас",
        "op.build_atlas.desc": "Собрать общий атлас из островов выделенных объектов",
        "op.export_uv_kit.label": "UV-комплект",
        "op.export_uv_kit.desc": "Выгрузить раскладку UV и манифест для рисования в другой программе",
        "op.import_atlas.label": "Импорт атласа",
        "op.import_atlas.desc": "Поставить нарисованный атлас в материал активного объекта",
        "op.bake_textures.label": "Запечь",
        "op.bake_textures.desc": "Запечь процедурный материал в текстуры (albedo и нормали)",
        "op.height_to_normal.label": "Высота → нормали",
        "op.height_to_normal.desc": "Сделать карту нормалей из карты высот",
        "op.tool_confirm.label": "Выполнить",
        "op.tool_confirm.desc": "Выполнить разрушающий инструмент, который запросил ассистент",
        "op.tool_cancel.label": "Отмена",
        "op.tool_cancel.desc": "Отклонить разрушающий инструмент, который запросил ассистент",
        "msg.code_done": "Код выполнен.",
        "msg.output": "Вывод:",
        "msg.code_error": "Ошибка выполнения кода:",
        "msg.code_saved": "Код сохранён в текст «{name}»",
        "msg.code_collapsed": "▶ Код выполнен ({n} строк)",
        "msg.code_hidden": "Код уже свёрнут",
        "pending.collapsed": "Код свёрнут в переписке",
        "msg.history_saved": "Переписка сохранена в текст «{name}»",
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
        "prompt.skills_header": (
            "## Инструкции по Blender (skills)\n"
            "Содержимое инструкций тебе пока не передано — виден только список. "
            "Если для задачи нужна подробная инструкция (точные имена сокетов, "
            "порядок нод, отличия версий Blender), ответь РОВНО одним блоком:\n\n"
            "```skill\n"
            "<имя из списка>\n"
            "```\n\n"
            "и больше ничем: текст инструкции придёт следующим сообщением, и ты "
            "продолжишь работу. Не запрашивай то, что уже есть в контексте — такие "
            "инструкции помечены строкой `# skill: <имя>`.\n"
            "Доступные инструкции:"
        ),
        "prompt.tools_header": (
            "## Инструменты (готовые действия)\n"
            "Ты можешь вызвать готовый инструмент аддона: он выполнит действие "
            "в сцене надёжно и мгновенно, без риска ошибиться в bpy-коде. "
            "Ниже — имя инструмента и что он делает. Чтобы вызвать, ответь "
            "РОВНО одним блоком:\n\n"
            "```tool\n"
            "<имя инструмента>\n"
            "```\n\n"
            "или с параметрами (JSON):\n\n"
            "```tool\n"
            'material.build_from_folder {"folder": "D:/textures/Wood_2k"}\n'
            "```\n\n"
            "Инструмент выполнится, и его результат придёт следующим сообщением — "
            "тогда продолжишь работу и расскажешь пользователю, что получилось.\n"
            "ВАЖНО: инструмент делает работу за тебя. Если ты вызвал инструмент — "
            "НЕ пиши bpy-код для того же действия и не пересказывай его: дождись "
            "результата и ответь коротко. Инструменты, помеченные "
            "«(destructive)», автоматически не выполняются: в ответе попроси "
            "пользователя нажать нужную кнопку в панели AI.\n"
            "Доступные инструменты:"
        ),
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
        # -- bulk operations (objects.py) ----------------------------------
        "bulk.box": "Массовые операции",
        "bulk.no_material": "У активного объекта нет материала",
        "bulk.nothing_selected": "Ничего не выбрано — назначать нечего.",
        "bulk.assigned": "Материал «{name}» назначен объектам: {n}",
        "bulk.no_users": "Материал «{name}» не используется ни одним объектом",
        "bulk.users": "Материал «{name}» используется в {n} слотах:",
        "bulk.failed": "Не удалось выполнить операцию: {exc}",
        "uv.title": "Проверка UV",
        "uv.checked": "Проверка UV выполнена",
        "uv.failed": "Не удалось проверить UV: {exc}",
        "uv.angle": "Швы по углу",
        "uv.own_seams": "Только по своим швам",
        "uv.no_meshes": "Нет мешей для развёртки",
        "uv.unwrapped": "Развёрнуто объектов: {n}",
        "uv.density_target": "Целевая плотность: {n} px/м",
        "uv.density_done": "Плотность выровнена на {n} объектах",
        "uv.colorspace_unknown": "У «{name}» непонятно, какое пространство нужно",
        "uv.colorspace_ok": "У «{name}» уже правильное цветовое пространство",
        "uv.colorspace_set": "У «{name}» цветовое пространство: {before} → {after}",
        "uv.already_packed": "«{name}» уже упакована",
        "uv.packed": "«{name}» упакована в .blend",
        "uv.already_relative": "У «{name}» путь уже относительный",
        "uv.not_saved": "Файл не сохранён — относительный путь не от чего строить",
        "uv.relative_set": "У «{name}» путь стал относительным: {path}",
        "uv.no_principled": "В материале нет Principled BSDF",
        "uv.node_connected": "Картинка подключена в {socket}",
        # -- node editor context menu (nodemenu.py) -------------------------
        "menu.asset_assistant": "Asset Assistant",
        "menu.fix_colorspace": "Починить цветовое пространство",
        "menu.pack": "Упаковать в .blend",
        "menu.make_relative": "Сделать путь относительным",
        "menu.set_base_color": "Подключить в Base Color",
        "menu.set_roughness": "Подключить в Roughness",
        "menu.set_normal": "Подключить в Normal",
        # -- procedural materials (procedural.py) ---------------------------
        "procedural.title": "Процедурные материалы",
        "procedural.built": "Материал «{name}» собран",
        "procedural.no_recipe": "Не понял, какой материал нужен: «{text}»",
        "procedural.unknown": "Нет такого рецепта: «{recipe}»",
        "procedural.failed": "Не удалось собрать материал: {exc}",
        "uv.atlas_done": "Атлас: островов {n}, занято {used}% от текстуры {size}px",
        "uv.atlas_density": "Плотность текселей: {achieved} px/м (цель {target})",
        "uv.atlas_too_small": "Текстура должна быть {size}px, чтобы сохранить плотность",
        "uv.kit_done": "UV-комплект выгружен в {folder}",
        "uv.atlas_imported": "Атлас «{image}» стал основным цветом материала «{material}»",
        # -- textures (textures.py) ----------------------------------------
        "textures.box": "Текстуры",
        "textures.none": "В файле нет файловых текстур.",
        "textures.count": "Текстуры ({n}):",
        "textures.packed": "Упаковано текстур: {n}",
        "textures.nothing_to_pack": "Упаковывать нечего — все текстуры уже внутри .blend.",
        "textures.relative": "Пути сделаны относительными: {n}",
        "textures.relinked": "Перелинковано потерянных файлов: {n}",
        "textures.missing": "Не найдено файлов текстур: {n}",
        "textures.still_missing": "Осталось потерянных файлов: {n}",
        "textures.paths_ok": "С путями всё в порядке.",
        "textures.failed": "Не удалось разобраться с текстурами: {exc}",
        # -- baking (baking.py) --------------------------------------------
        "bake.title": "Запекание",
        "bake.no_object": "Нет активного объекта",
        "bake.not_ready": "Объект не готов к запеканию:",
        "bake.done": "Запечено для «{obj}»: {maps} ({seconds} с)",
        "bake.installed": (
            "Материал «{material}» на объекте «{obj}». Карты сохранены в папку "
            "«{folder}» — её можно использовать для других объектов (кнопка "
            "«PBR из папки»)."
        ),
        "bake.source_kept": (
            "Процедурный материал «{material}» остался в файле — его можно "
            "вернуть на объект вручную."
        ),
        "bake.done_short": "Текстуры запечены",
        "bake.failed": "Не удалось запечь: {exc}",
        "bake.bake_no_uv": "У объекта «{obj}» нет UV-развёртки",
        "bake.bake_overlap": "У объекта «{obj}» {n} пар перекрывающихся островов",
        "bake.bake_no_height": "У материала «{obj}» нет источника высоты — нормали выйдут плоскими",
        "bake.bake_not_cycles": "Движок «{engine}» не умеет запекать (нужен Cycles)",
        "bake.bake_no_material": "У объекта «{obj}» нет материала",
        "bake.bake_gap_too_small": (
            "Зазор между островами — {gap}px, а паддинг запекания — {margin}px: "
            "острова будут затекать друг в друга"
        ),
        "bake.normal_done": "Карта нормалей «{name}» построена",
        "bake.normal_assigned": "и подключена во вход Normal материала",
        # -- material doctor (doctor.py) ----------------------------------
        "doctor.title": "Доктор материалов",
        "doctor.clean": "Проблем не найдено — материалы в порядке.",
        "doctor.counts": "Ошибок: {errors} · предупреждений: {warnings} · заметок: {notes}",
        "doctor.fixed": "Исправлено: {count}",
        "doctor.nothing_to_fix": "Исправлять нечего.",
        "doctor.failed": "Не удалось проверить материалы: {exc}",
        "find.colorspace": (
            "Карта данных «{image}» в материале «{mat}» питает «{socket}», но её "
            "цветовое пространство — «{space}», а нужно Non-Color"
        ),
        "find.normal_without_node": (
            "Карта нормалей «{image}» в материале «{mat}» подключена прямо в Normal, "
            "без узла Normal Map"
        ),
        "find.normal_to_base": (
            "Карта «{image}» в материале «{mat}» похожа на карту нормалей, но "
            "подключена в Base Color"
        ),
        "find.no_uv": (
            "У объекта «{obj}» нет UV-развёртки, а материал «{mat}» использует "
            "текстуры"
        ),
        "find.missing_file": "Файл текстуры «{image}» не найден: {path}",
        "find.not_packed": "Текстура «{image}» не упакована в .blend",
        "find.absolute_path": "У текстуры «{image}» абсолютный путь: {path}",
        "find.surface_unconnected": (
            "В материале «{mat}» выход Surface ничего не подключено — материал "
            "рендерится чёрным"
        ),
        "find.displacement_no_subdiv": (
            "Материал «{mat}» использует Displacement, но у объекта «{obj}» нет "
            "модификатора Subdivision"
        ),
        "find.orphan_material": "Материал «{mat}» не используется ни одним объектом",
        "find.duplicate_material": "Материал «{mat}» повторяет «{other}»",
        "find.resolution_mismatch": (
            "В материале «{mat}» карты разного разрешения: {sizes}"
        ),
        # -- UV (uv.py) ---------------------------------------------------
        "uv.uv_no_uv": "У объекта «{obj}» нет UV-развёртки",
        "uv.uv_overlap": (
            "У объекта «{obj}» {n} пар перекрывающихся островов ({pairs})"
        ),
        "uv.uv_out_of_bounds": (
            "У объекта «{obj}» {n} UV-координат выходят за пределы [0,1]"
        ),
        "uv.uv_density_mismatch": (
            "У объекта «{obj}» плотность острова {island} — {density} px/м, "
            "а у остальных {reference} px/м"
        ),
        "uv.uv_low_density": (
            "У объекта «{obj}» плотность острова {island} всего {density} px/м — "
            "текстура будет мыльной"
        ),
        "uv.uv_high_density": (
            "У объекта «{obj}» плотность острова {island} — {density} px/м: "
            "текстура избыточна"
        ),
        "uv.uv_stretch": (
            "У объекта «{obj}» треугольники искажены в {distortion} раза"
        ),
        "uv.uv_density_spread": (
            "У объекта «{obj}» плотность островов разная: от {low} до {high} px/м"
        ),
        "uv.uv_no_seams": (
            "У объекта «{obj}» нет ни одного шва — развёртка получится одним "
            "куском, с таким текстурировать нельзя"
        ),
        "uv.uv_no_texture": (
            "У объекта «{obj}» нет текстуры — плотность посчитать не по чему"
        ),
        # -- export check (export.py) --------------------------------------
        "find.export_custom_shader": (
            "В материале «{mat}» используется {node} — экспорт в glTF/FBX его "
            "не переносит"
        ),
        "find.export_displacement": (
            "Материал «{mat}» использует Displacement — в glTF/FBX его нет"
        ),
        "find.export_texture_large": (
            "Текстура «{image}» размером {size} больше, чем обычно допускают "
            "в играх ({max})"
        ),
        # -- project pipeline (project.py) ---------------------------------
        "project.title": "Импорт и подготовка проекта",
        "project.done": "Проект подготовлен, материал «{name}»",
        "project.checked": "Проверка экспорта выполнена",
        "project.problems": "Остались проблемы:",
        "project.ready": "Проблем не осталось — проект готов передать.",
        "project.failed": "Не удалось подготовить проект: {exc}",
        # -- state (state.py) --------------------------------------------
        "err.no_worker": "Не найден asset_core/worker.py рядом с аддоном",
        "err.no_python": "Не найден Python 3.9+ (в комплекте Blender или в PATH)",
        "err.worker_start": "Не удалось запустить воркер: {exc}",
        "err.worker_died": "Работник неожиданно завершился (код {code}). Лог: {log}",
        "err.unknown": "Неизвестная ошибка воркера",
        "msg.empty_reply": "(пустой ответ)",
        "msg.skill_loading": "Читаю инструкцию: {name}…",
        "msg.skill_ready": (
            "[Инструкция {name}] загружена в системный промпт. Используй её и "
            "продолжи ответ пользователю."
        ),
        "msg.tool_result": "[Инструмент {name}] отработал. Результат:\n{report}",
        "msg.tool_repeat": (
            "Инструмент «{name}» уже выполнялся в этом запросе — повторный "
            "вызов остановлен, чтобы не создать дубликаты."
        ),
        "msg.tool_running": "Выполняю инструмент: {name}…",
        "msg.tool_done": "Инструмент выполнен. Результат:\n{report}",
        "msg.tool_confirm": "Инструмент «{name}» меняет данные — нужно подтверждение.",
        "msg.tool_confirm_title": "Подтвердите инструмент «{name}»",
        "msg.tool_confirm_preview": "Будет удалено: {preview}",
        "msg.tool_declined": (
            "The user declined the tool '{name}'. Do not run it; tell the user "
            "that it was not done and what to do instead."
        ),
        "msg.tool_declined_short": "Инструмент отменён",
        "msg.tool_ran": "Инструмент выполнен",
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
        "prefs.num_ctx": "Контекст модели (токенов)",
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
        "op.material_doctor.label": "Material doctor",
        "op.material_doctor.desc": "Check the materials of the scene and find mistakes",
        "op.material_fix.label": "Fix",
        "op.material_fix.desc": "Apply the repairs the material doctor can make on its own",
        "op.pack_textures.label": "Pack",
        "op.pack_textures.desc": "Pack every file texture into the .blend",
        "op.fix_paths.label": "Paths",
        "op.fix_paths.desc": "Make the texture paths relative and relink lost files",
        "op.texture_report.label": "Report",
        "op.texture_report.desc": "Show a report on the textures of the scene",
        "op.assign_to_selection.label": "Assign to selection",
        "op.assign_to_selection.desc": "Put the active object's material on every selected object",
        "op.find_users.label": "Who uses it",
        "op.find_users.desc": "Show the objects that use the active object's material",
        "op.import_textures.label": "Import from folder",
        "op.import_textures.desc": "Build a material from a texture folder, assign it and prepare the project",
        "op.check_export.label": "Export check",
        "op.check_export.desc": "Check whether the project is ready to hand off: lost files, paths, UVs, shaders",
        "op.check_uv.label": "Check UVs",
        "op.check_uv.desc": "Check the unwrap: texel density, overlapping islands, coordinates out of bounds",
        "op.unwrap_uv.label": "Unwrap",
        "op.unwrap_uv.desc": "Mark the seams by angle, unwrap and pack the islands",
        "op.match_density.label": "Density",
        "op.match_density.desc": "Bring every island of the object to the same texel density",
        "op.build_atlas.label": "Atlas",
        "op.build_atlas.desc": "Lay the islands of the selected objects into one shared atlas",
        "op.export_uv_kit.label": "UV kit",
        "op.export_uv_kit.desc": "Export the UV layout and a manifest to paint in another program",
        "op.import_atlas.label": "Import atlas",
        "op.import_atlas.desc": "Put a painted atlas into the material of the active object",
        "op.bake_textures.label": "Bake",
        "op.bake_textures.desc": "Bake the procedural material into textures (albedo and normals)",
        "op.height_to_normal.label": "Height to normals",
        "op.height_to_normal.desc": "Build a normal map from a height map",
        "op.tool_confirm.label": "Run it",
        "op.tool_confirm.desc": "Run the destructive tool the assistant asked for",
        "op.tool_cancel.label": "Cancel",
        "op.tool_cancel.desc": "Decline the destructive tool the assistant asked for",
        "msg.code_done": "Code executed.",
        "msg.output": "Output:",
        "msg.code_error": "Code execution error:",
        "msg.code_saved": "Code saved to text «{name}»",
        "msg.code_collapsed": "▶ Code executed ({n} lines)",
        "msg.code_hidden": "The code is already collapsed",
        "pending.collapsed": "The code is collapsed in the chat",
        "msg.history_saved": "The conversation was saved to the text «{name}»",
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
        "prompt.skills_header": (
            "## Blender instructions (skills)\n"
            "The instruction texts are not in your context yet — only this list is. "
            "When a task needs a detailed recipe (exact socket names, node order, "
            "Blender version differences), answer with EXACTLY one block:\n\n"
            "```skill\n"
            "<name from the list>\n"
            "```\n\n"
            "and nothing else: the text arrives in the next message and you continue. "
            "Never request what is already in context — those are marked with "
            "`# skill: <name>`.\n"
            "Available instructions:"
        ),
        "prompt.tools_header": (
            "## Tools (ready-made actions)\n"
            "You can call a ready-made tool of the add-on: it performs the action "
            "in the scene reliably and instantly, with no risk of getting the "
            "bpy code wrong. Below is the tool name and what it does. To call "
            "one, answer with EXACTLY one block:\n\n"
            "```tool\n"
            "<tool name>\n"
            "```\n\n"
            "or with parameters (JSON):\n\n"
            "```tool\n"
            'material.build_from_folder {"folder": "D:/textures/Wood_2k"}\n'
            "```\n\n"
            "The tool runs and its result arrives in the next message — then you "
            "continue and tell the user what happened.\n"
            "IMPORTANT: the tool does the work for you. If you called a tool, do "
            "NOT write bpy code for the same action and do not repeat it: wait "
            "for the result and answer briefly. Tools marked «(destructive)» are "
            "not run automatically: ask the user to press the matching button "
            "in the AI panel.\n"
            "Available tools:"
        ),
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
        # -- bulk operations -----------------------------------------------
        "bulk.box": "Mass operations",
        "bulk.no_material": "The active object has no material",
        "bulk.nothing_selected": "Nothing is selected - there is nothing to assign.",
        "bulk.assigned": "Assigned material \"{name}\" to {n} objects:",
        "bulk.no_users": "Material \"{name}\" is not used by any object",
        "bulk.users": "Material \"{name}\" is used in {n} slot(s):",
        "bulk.failed": "Could not carry out the operation: {exc}",
        "uv.title": "UV check",
        "uv.checked": "The UV check is done",
        "uv.failed": "Could not check the UVs: {exc}",
        "uv.angle": "Seam angle",
        "uv.own_seams": "Only along my own seams",
        "uv.no_meshes": "No meshes to unwrap",
        "uv.unwrapped": "Unwrapped {n} object(s)",
        "uv.density_target": "Target density: {n} px/m",
        "uv.density_done": "Density matched on {n} object(s)",
        "uv.colorspace_unknown": "It is unclear which colour space \"{name}\" needs",
        "uv.colorspace_ok": "\"{name}\" already has the right colour space",
        "uv.colorspace_set": "\"{name}\": colour space {before} -> {after}",
        "uv.already_packed": "\"{name}\" is already packed",
        "uv.packed": "\"{name}\" is packed into the .blend",
        "uv.already_relative": "The path of \"{name}\" is already relative",
        "uv.not_saved": "The file was never saved - there is no base for a relative path",
        "uv.relative_set": "The path of \"{name}\" is now relative: {path}",
        "uv.no_principled": "The material has no Principled BSDF",
        "uv.node_connected": "The image is connected to {socket}",
        # -- node editor context menu ---------------------------------------
        "menu.asset_assistant": "Asset Assistant",
        "menu.fix_colorspace": "Fix the colour space",
        "menu.pack": "Pack into the .blend",
        "menu.make_relative": "Make the path relative",
        "menu.set_base_color": "Connect to Base Color",
        "menu.set_roughness": "Connect to Roughness",
        "menu.set_normal": "Connect to Normal",
        # -- procedural materials ------------------------------------------
        "procedural.title": "Procedural materials",
        "procedural.built": "The material \"{name}\" is built",
        "procedural.no_recipe": "Could not tell which material is meant: \"{text}\"",
        "procedural.unknown": "There is no recipe called \"{recipe}\"",
        "procedural.failed": "Could not build the material: {exc}",
        "uv.atlas_done": "Atlas: {n} island(s), {used}% of a {size}px texture",
        "uv.atlas_density": "Texel density: {achieved} px/m (target {target})",
        "uv.atlas_too_small": "The texture has to be {size}px to keep that density",
        "uv.kit_done": "The UV kit was written to {folder}",
        "uv.atlas_imported": "The atlas \"{image}\" is now the Base Color of \"{material}\"",
        # -- textures -----------------------------------------------------
        "textures.box": "Textures",
        "textures.none": "The file has no file textures.",
        "textures.count": "Textures ({n}):",
        "textures.packed": "Packed textures: {n}",
        "textures.nothing_to_pack": "Nothing to pack - all textures are already inside the .blend.",
        "textures.relative": "Made {n} texture paths relative",
        "textures.relinked": "Relinked {n} lost texture files",
        "textures.missing": "Texture files not found: {n}",
        "textures.still_missing": "Still missing: {n}",
        "textures.paths_ok": "The paths are fine.",
        "textures.failed": "Could not deal with the textures: {exc}",
        # -- baking -------------------------------------------------------
        "bake.title": "Baking",
        "bake.no_object": "There is no active object",
        "bake.not_ready": "The object is not ready to bake:",
        "bake.done": "Baked for \"{obj}\": {maps} ({seconds}s)",
        "bake.installed": (
            "The material \"{material}\" is on \"{obj}\". The maps are saved in "
            "the folder \"{folder}\" - use it for other objects with the "
            "'PBR from folder' button."
        ),
        "bake.source_kept": (
            "The procedural material \"{material}\" is still in the file - you "
            "can put it back on the object by hand."
        ),
        "bake.done_short": "The textures are baked",
        "bake.failed": "Could not bake: {exc}",
        "bake.bake_no_uv": "Object \"{obj}\" has no UV unwrap",
        "bake.bake_overlap": "Object \"{obj}\" has {n} overlapping island pair(s)",
        "bake.bake_no_height": "Material \"{obj}\" has no height source — the normals will come out flat",
        "bake.bake_not_cycles": "The \"{engine}\" engine cannot bake (Cycles is needed)",
        "bake.bake_no_material": "Object \"{obj}\" has no material",
        "bake.bake_gap_too_small": (
            "The gap between the islands is {gap}px but the bake margin is "
            "{margin}px - the islands will bleed into each other"
        ),
        "bake.normal_done": "Built the normal map \"{name}\"",
        "bake.normal_assigned": "and connected it to the Normal input of the material",
        # -- material doctor ----------------------------------------------
        "doctor.title": "Material doctor",
        "doctor.clean": "No problems found — the materials are fine.",
        "doctor.counts": "Errors: {errors} · warnings: {warnings} · notes: {notes}",
        "doctor.fixed": "Fixed: {count}",
        "doctor.nothing_to_fix": "Nothing to fix.",
        "doctor.failed": "Could not check the materials: {exc}",
        "find.colorspace": (
            "Data map \"{image}\" in material \"{mat}\" feeds \"{socket}\", but its "
            "colour space is \"{space}\" instead of Non-Color"
        ),
        "find.normal_without_node": (
            "Normal map \"{image}\" in material \"{mat}\" is wired straight into "
            "Normal, without a Normal Map node"
        ),
        "find.normal_to_base": (
            "Map \"{image}\" in material \"{mat}\" looks like a normal map but is "
            "wired into Base Color"
        ),
        "find.no_uv": (
            "Object \"{obj}\" has no UV layout, but material \"{mat}\" uses "
            "textures"
        ),
        "find.missing_file": "Texture file \"{image}\" not found: {path}",
        "find.not_packed": "Texture \"{image}\" is not packed into the .blend",
        "find.absolute_path": "Texture \"{image}\" has an absolute path: {path}",
        "find.surface_unconnected": (
            "In material \"{mat}\" the Surface output is not connected — the "
            "material renders black"
        ),
        "find.displacement_no_subdiv": (
            "Material \"{mat}\" uses Displacement, but object \"{obj}\" has no "
            "Subdivision modifier"
        ),
        "find.orphan_material": "Material \"{mat}\" is not used by any object",
        "find.duplicate_material": "Material \"{mat}\" duplicates \"{other}\"",
        "find.resolution_mismatch": (
            "Material \"{mat}\" mixes maps of different sizes: {sizes}"
        ),
        # -- UV ----------------------------------------------------------
        "uv.uv_no_uv": "Object \"{obj}\" has no UV unwrap",
        "uv.uv_overlap": (
            "Object \"{obj}\" has {n} overlapping island pair(s) ({pairs})"
        ),
        "uv.uv_out_of_bounds": (
            "Object \"{obj}\" has {n} UV coordinates outside [0,1]"
        ),
        "uv.uv_density_mismatch": (
            "Object \"{obj}\": island {island} is {density} px/m while the rest "
            "of the scene is {reference} px/m"
        ),
        "uv.uv_low_density": (
            "Object \"{obj}\": island {island} is only {density} px/m — the "
            "texture will look blurry"
        ),
        "uv.uv_high_density": (
            "Object \"{obj}\": island {island} is {density} px/m — the texture "
            "is bigger than it needs to be"
        ),
        "uv.uv_stretch": (
            "Object \"{obj}\": the triangles are stretched {distortion} times"
        ),
        "uv.uv_density_spread": (
            "Object \"{obj}\": the islands have different densities, from "
            "{low} to {high} px/m"
        ),
        "uv.uv_no_seams": (
            "Object \"{obj}\" has not a single seam - the unwrap will be one "
            "piece, and such a model cannot be textured"
        ),
        "uv.uv_no_texture": (
            "Object \"{obj}\" has no texture, so there is nothing to measure the "
            "density against"
        ),
        # -- export check --------------------------------------------------
        "find.export_custom_shader": (
            "Material \"{mat}\" uses {node}, which the glTF/FBX exporters do "
            "not carry over"
        ),
        "find.export_displacement": (
            "Material \"{mat}\" uses Displacement, which glTF/FBX do not have"
        ),
        "find.export_texture_large": (
            "Texture \"{image}\" is {size}, which is larger than games usually "
            "accept ({max})"
        ),
        # -- project pipeline ----------------------------------------------
        "project.title": "Import and project preparation",
        "project.done": "Project prepared, material \"{name}\"",
        "project.checked": "The export check is done",
        "project.problems": "Problems left:",
        "project.ready": "No problems left: the project is ready to hand off.",
        "project.failed": "Could not prepare the project: {exc}",
        # -- state --------------------------------------------------------
        "err.no_worker": "asset_core/worker.py not found next to the add-on",
        "err.no_python": "Python 3.9+ not found (bundled with Blender or on PATH)",
        "err.worker_start": "Failed to start the worker: {exc}",
        "err.worker_died": "The worker exited unexpectedly (code {code}). Log: {log}",
        "err.unknown": "Unknown worker error",
        "msg.empty_reply": "(empty reply)",
        "msg.skill_loading": "Reading skill: {name}…",
        "msg.skill_ready": (
            "[Skill {name}] is now in the system prompt. Use it and continue "
            "the answer."
        ),
        "msg.tool_result": "[Tool {name}] has run. Result:\n{report}",
        "msg.tool_repeat": (
            "The tool \"{name}\" already ran in this request - the repeat was "
            "stopped so nothing is created twice."
        ),
        "msg.tool_running": "Running tool: {name}…",
        "msg.tool_done": "The tool has run. Result:\n{report}",
        "msg.tool_confirm": "The tool \"{name}\" changes data - it needs your confirmation.",
        "msg.tool_confirm_title": "Confirm the tool \"{name}\"",
        "msg.tool_confirm_preview": "Will be removed: {preview}",
        "msg.tool_declined": (
            "The user declined the tool '{name}'. Do not run it; tell the user "
            "that it was not done and what to do instead."
        ),
        "msg.tool_declined_short": "The tool was declined",
        "msg.tool_ran": "The tool has run",
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
        "prefs.num_ctx": "Model context (tokens)",
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
    """Localized string for ``key`` in the current language (with .format()).

    Pass ``lang="en"`` to force a language: the model-facing text (the scene
    snapshot) is always English, while the same finding shown to the user in
    the chat follows the interface language.
    """
    lang = fmt.pop("lang", None) or get_lang()
    if lang not in STRINGS:
        lang = DEFAULT_LANG
    text = STRINGS[lang].get(key)
    if text is None:
        text = STRINGS[DEFAULT_LANG].get(key, key)
    return text.format(**fmt) if fmt else text
