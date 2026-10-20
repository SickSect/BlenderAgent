# Глава 17. Сквозной сценарий: от папки с текстурами до готового проекта

> 📦 `src/blender_addon/project.py`, `export.py`, `operators.py`
> (`gaa.import_textures`, `gaa.check_export`), `ui.py`, `i18n.py`,
> `tests/run_project_check.py`

Пятнадцать глав были про отдельные инструменты. Эта — про то, как из них
получается **рабочий процесс**.

---

## 17.1. Зачем нужен сценарий, если есть инструменты

Каждый инструмент по отдельности решает одну задачу. Но художник не решает
задачи — он выполняет **процесс**:

```
папка с текстурами  →  корректный материал  →  назначить объектам
                    →  проект переносим  →  можно отдать
```

Пять шагов, пять инструментов, и человеку приходится помнить порядок и не
забыть ни один. Ошибка в порядке стоит дорого: собрал материал и **забыл
назначить** — а потом отдал проект, у которого половина объектов серые.

Сценарий — это те же пять шагов, связанные в один вызов с одним отчётом.

---

## 17.2. Код

```python
def import_and_prepare(folder, assign=True, pack=True):
    steps = []

    # 1. Scan.
    maps = materials.find_maps(folder)
    if not maps:
        raise ValueError(f"no texture maps found in {folder} ...")
    steps.append(f"Found in the folder: {roles}")

    # 2. Build the material.
    material, maps, assigned, reused = materials.build_pbr_from_folder(folder)
    steps.append(f"Built the material '{material.name}'")

    # 3. Assign it.
    if assign:
        changed = objects.assign_to_selection(material, targets)
        steps.append(f"Assigned it to {len(changed)} objects")

    # 4. Pack the textures.
    if pack:
        packed = textures.pack_all()
        steps.append(f"Packed {len(packed)} textures into the .blend")

    # 5. Check that the project can leave this machine.
    findings = export.check_export()
    steps.append(f"Export check: ...")

    return {"steps": steps, "material": material.name, "problems": [...]}
```

Три свойства, которые делают сценарий честным:

| Свойство | Как сделано |
|---|---|
| **каждый шаг отчитывается** | `steps` — список строк, который показывается и человеку, и модели |
| **шаг можно выключить** | `assign` и `pack` — флаги; например, не упаковывать, если файлы нужны на диске |
| **повтор не плодит мусор** | `build_pbr_from_folder` помнит папку (`gaa_source_folder`) и обновляет материал вместо нового |

⚠️ Порядок шагов **неслучаен**: упаковка идёт **после** назначения, а проверка
экспорта — **после** упаковки. Иначе проверка увидит «текстуры не упакованы»
там, где они только что были упакованы.

---

## 17.3. Проверка экспорта: другой вопрос, чем у доктора

| | Доктор | Проверка экспорта |
|---|---|---|
| Смотрит | **внутрь** материала | **наружу** проекта |
| Спрашивает | «материал правильный?» | «проект готов уйти с машины?» |
| Находит | colorspace, normal-карту, неподключённый выход | потерянные файлы, пути, UV, чужие шейдеры |

Общие находки (**потерянный файл**, **не упаковано**, **нет UV**) — **одни и
те же коды и одни и те же сообщения**. Один факт, одна формулировка, два
взгляда.

Своё у проверки экспорта — то, что важно только в момент передачи:

| Код | Что значит |
|---|---|
| `export_custom_shader` | в `Surface` сидит не Principled BSDF (Glass, Toon, Diffuse…) — экспортёр в glTF/FBX его не переносит |
| `export_displacement` | Displacement в выход материала — в форматах обмена его нет |
| `export_texture_large` | текстура больше лимита (по умолчанию 4096) — waste памяти на устройстве |

### Как проверяется «свой шейдер»

```python
surface = output.inputs.get("Surface")
if surface and surface.links:
    source = surface.links[0].from_node
    if source != bsdf:
        # not a Principled BSDF - the exporters will not carry it over
```

Сравнение — через `==` (bpy выдаёт новую обёртку, `is` не работает).

---

## 17.4. Кнопка «Импорт из папки»

```python
class GAA_OT_import_textures(bpy.types.Operator):
    bl_idname = "gaa.import_textures"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        return context.window_manager.fileselect_add(self)
```

`subtype="DIR_PATH"` — браузер выбирает **папку**. `UNDO` — весь сценарий
откатывается одним `Ctrl+Z`.

Отчёт уходит **в панель чата** (он длинный) и короткая строка — в статус.

---

## 17.5. Что даёт сценарий пользователю

Вместо пяти действий и пяти «не забыл» — одно действие и один отчёт:

```
Импорт и подготовка проекта
- Found in the folder: base, normal, rough
- Built the material 'Wood_2k'
- Assigned it to 12 objects
- Packed 3 textures into the .blend
- Export check: 0 errors, 1 warnings, 2 notes
```

И если что-то пошло не так — отчёт это **показывает**, а не прячет:

```
Problems left:
- image "Metal_Diffuse" in material "Metal_Broken" feeds "Base Color" but its
  colour space is "sRGB" instead of "Non-Color"
```

---

## 17.6. Как это проверяется

📦 `tests/run_project_check.py`:

```
== the end-to-end scenario
ok   every step reported
ok   the material was assigned to the selection
ok   the textures were packed (3)
ok   the export check is part of the scenario
== running it twice
ok   the second run updates the material instead of duplicating it
ok   no duplicate material was left behind
== the export check
ok   a custom shader is flagged
ok   a displacement is flagged
ok   a textured mesh without UV is flagged
ok   an oversized texture is flagged against the limit
RESULT: OK
```

⚠️ Тест поймал тонкость: **меш без UV без текстур — не проблема**. Флаг
ставится только если на меше есть материал с текстурами. Иначе в проекте с
сотней пустых объектов получилась бы каша из предупреждений.

---

## 17.7. Итог главы

- Сценарий = пять уже готовых инструментов, связанных в правильном порядке.
- Каждый шаг **отчитывается**, и каждый шаг можно **выключить**.
- Повторный запуск **обновляет** материал, а не плодит `.001`.
- Проверка экспорта спрашивает **другое**, чем доктор, но общие находки
  описывает **одними и теми же** сообщениями.
- Своё у проверки экспорта: чужие шейдеры, Displacement, слишком крупные
  текстуры.
- Один `Ctrl+Z` откатывает весь сценарий.

Дальше — **глава 18**: диалог подтверждения для разрушающих операций.
