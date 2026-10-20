# Глава 13. API-помощники и реестр инструментов

> 📦 `src/blender_addon/api.py`, `tools.py`, `materials.py` (инструмент),
> `state.py` (цикл вызова), `operators.py` (каталог в промпте),
> `i18n.py`, `prompts.py`, `tests/tools_test.py`,
> `tests/run_tools_check.py`

Эта глава — про два модуля, без которых дальнейшая работа превращается в
копипаст. Разберём, какую боль они снимают и как ими пользоваться.

---

## 13.1. Боль: одна и та же информация в трёх местах

Знание о том, как устроен Blender API, лежало в проекте в трёх копиях:

| Копия | Где | Как выглядела |
|---|---|---|
| Алиасы сокетов | `materials._link()` | `in_name, *aliases` — молчаливый отказ при отсутствии сокета |
| Список имён | `snapshot._socket()` | `("Transmission Weight", "Transmission")` |
| Прозой в скиллах | `skills/material-*.md` | «Transmission Weight = 1.0, was Transmission before 4.0» |

Три копии одного знания **разъезжаются**: добавь новый сокет в одну — о других
забудут. И главное: **ни одна из них не защищает код, который пишет модель**.
Модель 7B регулярно пишет `bsdf.inputs["Transmission"]` — и пользователь видит
`KeyError` в трейсбеке.

## 13.2. `api.py` — единственный источник правды

📦 `src/blender_addon/api.py`. Модуль намеренно **не зависит от `bpy`**: все
хелперы работают с «утиными» объектами. Из-за этого его можно импортировать
и в аддон, и в сгенерированный код, и в обычный pytest — без Blender.

### Таблица имён

```python
SOCKET_ALIASES = {
    "Transmission Weight": ("Transmission Weight", "Transmission"),
    "Specular IOR Level":  ("Specular IOR Level", "Specular"),
    "Emission Color":      ("Emission Color", "Emission"),
    "Coat Weight":         ("Coat Weight", "Clearcoat"),
    "Sheen Weight":        ("Sheen Weight", "Sheen"),
    "Subsurface Weight":   ("Subsurface Weight", "Subsurface"),
    ...
}
```

Первое имя — текущее, остальные — старые. Отсюда же:

```python
LEGACY_TO_CANONICAL = {
    legacy: canonical
    for canonical, spellings in SOCKET_ALIASES.items()
    for legacy in spellings[1:]
}
```

…и обратный словарь: `canonical_socket("Transmission") == "Transmission Weight"`.

### Четыре хелпера

```python
def get_input(node, *names): ...      # первый существующий вход
def set_input(node, value, *names): ...# записать значение (True, если нашли)
def find_principled(tree): ...         # BSDF по типу, не по имени
def link(links, source, out, target, in_name, *aliases): ...
```

Паттерн «попробуй несколько имён» — тот же, что был в `materials._link()` и
`snapshot._socket()`, но теперь в одном месте. Переименование сокета в новой
версии Blender — это правка **одной строки**.

### Данные против цвета

```python
DATA_SOCKETS = frozenset({"Roughness", "Metallic", "Normal", "Displacement", ...})
```

Сокеты, которые несут **числа**, а не цвет. Картинка, питающая такой сокет,
обязана быть в `Non-Color` — об этом же говорит раздел `Problems` в снимке
сцены (глава 7). Список живёт здесь, чтобы обе проверки смотрели в одну
правду.

### Код, который пишет модель

При `register()` аддон публикует хелперы в `bpy.app.driver_namespace` — штатное
место для обмена данными между плагином и тем, что он создаёт:

```python
def install(namespace: dict) -> None:
    namespace["gaa_api"] = _PublicApi()
```

Тогда сгенерированный код может быть таким:

```python
import bpy

api = bpy.app.driver_namespace.get("gaa_api")
bsdf = next(n for n in bpy.data.materials["Wood"].node_tree.nodes
            if n.type == "BSDF_PRINCIPLED")
if api is not None:
    api.set_input(bsdf, 1.0, "Transmission Weight", "Transmission")
else:
    bsdf.inputs["Transmission Weight"].default_value = 1.0
```

Версия ошибки перестаёт быть ошибкой пользователя: код работает и на 3.6, и на
5.x.

⚠️ `driver_namespace` — глобальное состояние Blender. Аддон **кладёт** туда
своё и **убирает** при `unregister()` (`driver_namespace.pop("gaa_api", None)`),
чтобы после отключения плагина в сцене не осталось висящего объекта.

---

## 13.3. `tools.py` — реестр инструментов

📦 `src/blender_addon/tools.py`. Снова **без `bpy`**: реестр, каталог и парсер
запросов — обычный Python, их покрывает обычный pytest.

### Что такое инструмент

Инструмент — **именованное действие**, которое адон умеет выполнять. Одна
реализация — три потребителя:

```
              ┌───────────────────────────────┐
              │        tools.py (реестр)       │
              │  id · title · description ·    │
              │  handler · destructive · params│
              └───────────────┬───────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
     кнопка в UI      вызов моделью        тест в Blender
  (operators.py)    (текстовый протокол)  (run_tools_check.py)
```

Без реестра добавление фичи стоило бы **четыре** правки: функция, оператор,
строка в UI, путь для модели. С реестром — **одну**: функцию с декоратором.

### Регистрация

```python
@tools.tool(
    "material.build_from_folder",
    "PBR from folder",
    "Build a PBR material from a folder of texture maps (albedo, normal, "
    "roughness, metallic, ORM, emission, height) and assign it to the active "
    "object. Call this instead of writing bpy code.",
    params=("folder",),
)
def build_from_folder_tool(folder=None, **kwargs) -> str:
    ...
    return report
```

`handler` вызывается как `handler(**params)` и возвращает **отчёт** — короткий
фактологический текст. Он попадает и в строку статуса, и в контекст модели.

### Каталог

```python
def catalog() -> str:
    """``- id: description`` lines for the system prompt."""
```

Ровно тот же механизм, что у скиллов: одна строка на инструмент, всегда в
промпте. **Модель должна знать, что может вызвать, прежде чем попросит.**

```
- material.build_from_folder: Build a PBR material from a folder of texture
  maps ... (destructive)
```

### Флаг `destructive`

Решение, принятое в роадмапе: **подтверждать только разрушающие**. Разрушающий
инструмент объявляет это сам:

```python
@tools.tool("materials.cleanup_orphans", "Cleanup", "...", destructive=True)
```

Тогда ни цикл, ни UI не решают это вручную — решение принимается один раз, в
реестре.

### Предпросмотр: что именно исчезнет

Одного названия инструмента мало: «удалить неиспользуемое» звучит страшнее, чем
«удалить вот эти три материала и две картинки». Поэтому у инструмента может
быть **предпросмотр** — функция, которая считает, что будет уничтожено:

```python
@tools.tool("materials.cleanup_orphans", "Cleanup", "...", destructive=True,
             preview=_preview_orphans)
def _preview_orphans(params=None) -> str:
    ...
    return "3 material(s): OldWood, OldMetal, OldPlastic"
```

Предпросмотр показывается **в блоке подтверждения** в чате — до того, как
что-то удалено. Он же попадает в подсказку модели, когда она просит
подтверждения.

---

## 13.4. Первый инструмент: тот же код, что и у кнопки

Кнопка «PBR из папки» появилась раньше и делала своё дело. Чтобы показать
архитектуру, мы **не стали** писать вторую реализацию — мы вынесли логику в
функцию и зарегистрировали её как инструмент:

```python
# materials.py
def build_pbr_from_folder(directory, assign_to=None):
    """Возвращает (material, maps, assigned_name, reused)."""
```

```python
# operators.py — кнопка
material, maps, assigned, _reused = materials.build_pbr_from_folder(
    self.filepath, assign_to=obj
)

# materials.py — инструмент (тот же вызов)
material, maps, assigned, reused = build_pbr_from_folder(folder, assign_to=obj)
```

Кнопка и вызов моделью — **одна и та же строка**. Расхождение невозможно по
 построению.

### Повторяемость

Модель может вызвать инструмент дважды. Чтобы не плодить `Wood_2k.001`,
материал помнит, из какой папки собран:

```python
SOURCE_FOLDER_KEY = "gaa_source_folder"
material[SOURCE_FOLDER_KEY] = directory
```

Второй вызов с той же папкой **обновляет** существующий материал, а не создаёт
новый.

---

## 13.5. Протокол вызова

Локальные модели часто не умеют native tool-calling. Поэтому — тот же
текстовый приём, что со скиллами:

````
```tool
material.build_from_folder {"folder": "D:/textures/Wood_2k"}
```
````

Парсер терпим к мелочам:

| Что модель написала | Что поймёт аддон |
|---|---|
| ` ```tool\nmaterial.audit\n``` ` | `("material.audit", {})` |
| ` ```tool\nmaterial.audit {"material": "Wood"}\n``` ` | `("material.audit", {"material": "Wood"})` |
| параметры на следующей строке | то же самое |
| `TOOL: material.audit` (одна строка) | то же самое |
| `{"name": "material.audit", "arguments": {...}}` (просто JSON) | то же самое |
| ` ```python ` | **не** запрос инструмента |
| неизвестное имя | проигнорировано |

Разбор живёт в `tools.parse_request()`, чистка — в `tools.strip_requests()`:
блок запроса не попадает ни в историю, ни в кнопку «Выполнить код».

### ⚠️ Почему в списке есть «просто JSON»

Это не экзотика — это **то, что делают реальные модели**. Проверено на
`qwen2.5-coder:7b`:

```
Модели дали схему инструмента → она ответила:
  {"name": "material_audit", "arguments": {}}
```

То есть модель **поняла** инструмент и вызвала его — но не нативным
`tool_calls`, а обычным текстом-JSON. Нативный вызов у неё не поддерживается
(проверено: в ответе `message.tool_calls` пуст).

Отсюда два решения:

1. **Нативный tool-calling не нужен.** Для моделей без поддержки это мёртвый
   код и лишняя зависимость от конкретных моделей.
2. **Парсер принимает и JSON.** Модель, которой скормили схему инструмента,
   выдаст вызов в каком-то виде — и аддон его поймает.

⚠️ JSON находится **сканером с балансировкой скобок**, а не регуляркой:
в `arguments` могут быть **вложенные** объекты (`{"material": "Wood"}`), и
`[^{}]*` по ним не проходит.

---

## 13.6. Цикл: инструмент → отчёт → контекст

Механика **та же**, что у второго прохода со скиллами, — переиспользован
рабочий код:

```
модель ответила
   └─ state._handle("done")
        ├─ нашли ```tool ...``` ?
        │     ├─ да → выполнить на главном потоке
        │     │     ├─ отчёт → подклеить к системному промпту
        │     │     └─ переспросить ту же реплику
        │     └─ нет → обычное завершение хода
        └─ (иначе) спросить скилл, как раньше
```

Отчёт попадает **в системный промпт**, а не в историю чата:

```python
for call in self.tool_results:
    parts.append(
        f"# tool result: {call['id']}\n"
        "This tool has ALREADY RUN. Its output is below. "
        "Do not call it again - use this result to answer the user.\n\n"
        f"{call['report']}"
    )
```

Почему не в историю: у шаблона чата нет понятия «сообщение-результат
инструмента», а разные модели читают одну и ту же переписку по-разному.
Системный промпт — единственное место, где все модели понимают вложение.

### Защита от повторного вызова

Слабые модели не всегда замечают, что инструмент уже выполнен, и просят его
снова. Тогда:

- **второй раз инструмент не выполняется** — вернулся бы отчёт, а данные
  удвоились бы;
- ход **завершается**: в ответ попадает то, что модель успела сказать, плюс
  отчёт первого запуска.

```python
previous = self._tool_report(name, params)
if previous is not None:
    said = tools.strip_requests(self.stream_buf).strip()
    self.stream_buf = f"{said}\n\n{previous}".strip() if said else \
        i18n.T("msg.tool_done", report=previous)
    self._finish_reply()
    return True
```

Бюджет `tools.MAX_TOOL_CALLS = 4` — страховка сверху: каждый вызов стоит
один дополнительный запрос к модели.

---

## 13.7. Харнесс: контракт инструмента

📦 `tests/run_tools_check.py`. Инструмент — это **обещание**: он работает в
текущей сцене, ему не нужно окно, он отвечает отчётом, и повторный запуск
ничего не ломает. Харнесс проверяет это на синтетической сцене в
`--background`:

```
== version-tolerant helpers
ok   the Principled BSDF is found by type
ok   Transmission Weight resolves
...
== the registry
   1 tools: material.build_from_folder
== every tool runs headless
ok   material.build_from_folder runs without raising
ok   material.build_from_folder survives a second run
== the model-call loop
ok   the turn stays busy while the tool runs
ok   the report is attached to the system prompt
ok   the request block is stripped
RESULT: OK
```

Инструмент, падающий в headless, **не доезжает до пользователя**. Для
аддона, который уже однажды ронял Blender (см. README, история про
`DepsgraphNodeBuilder`), это не формальность.

`tests/tools_test.py` — то же самое для чистой логики реестра, обычным
pytest, без Blender.

---

## 13.8. Как добавить инструмент

1. Напиши функцию в доменном модуле (`materials.py`, `textures.py`,
   `nodes.py`, …). Она должна быть **headless-friendly**: только data API,
   без `bpy.ops`, без чтения с диска без нужды.
2. Оберни её декоратором `@tools.tool(...)`. Опиши в `description`, что она
   делает, — это попадёт в промпт модели.
3. Если инструмент разрушающий — поставь `destructive=True` и, если можно,
   добавь `preview=...`, чтобы в подтверждении было видно, что исчезнет.
4. Добавь кнопку в UI, если она нужна человеку: **тонкий оператор**, который
   зовёт ту же функцию.
5. Прогони `tests/run_tools_check.py` и `tests/tools_test.py`.

Ни строчки архитектуры менять не приходится — это и смысл реестра.

---

## 13.9. История вызовов

Каждый вызов инструмента **записывается** — включая отклонённые. Это даёт
ответ на вопрос «а что ты вообще сделал?»:

```
12 tool call(s) so far:
- material.audit (ok): Audited the scene: 2 errors, 2 warnings, 4 notes.
- uv.build_atlas (ok): Atlas: 12 island(s) from 2 object(s), 43% of a 2048px texture
- materials.cleanup_orphans (not done): The user declined the tool 'Clean up orphans'.
```

Инструмент `tools.history` отдаёт этот список — и человеку (через чат), и
модели (она может сама спросить «что уже сделано»).

Записи живут в состоянии чата и **обнуляются с началом нового хода**: история
относится к текущей работе, а не ко всей сессии.

---

## 13.9. Что даёт Stage 2 — честно

Сама по себе эта глава **не добавляет ни одной пользовательской кнопки**. Она
даёт:

| Что | Зачем |
|---|---|
| одну таблицу имён сокетов вместо трёх копий | переименование в новой версии Blender = правка одной строки |
| защиту сгенерированного кода от версионных ошибок | `Transmission` вместо `Transmission Weight` перестаёт быть ошибкой пользователя |
| реестр с тремя потребителями | добавление фичи = одна функция вместо четырёх правок |
| каталог инструментов в промпте | модель наконец знает, что умеет аддон |
| протокол вызова с возвратом результата | ассистент не только советует, но и **делает** |
| headless-харнесс | инструмент, ломающий Blender, не доезжает до пользователя |

Первый инструмент (`material.build_from_folder`) показал, что схема работает
на **уже существующей** фиче: кнопка и вызов моделью — один код.

⚠️ **Честное ограничение.** Локальная модель 7B на CPU отвечает медленно
(1–5 минут), и мелкие модели не всегда следуют кастомному протоколу. Поэтому
инструмент — это в первую очередь **кнопка**, которая работает мгновенно и
всегда; вызов моделью — бонус, а не основа.

---

## 13.10. Итог главы

- `api.py` — единственный источник знания об именах сокетов; не зависит от
  `bpy`, публикуется в `driver_namespace` для сгенерированного кода.
- `tools.py` — реестр: одна реализация, три потребителя (кнопка, модель,
  тест); флаг `destructive` задаёт политику подтверждений.
- Протокол вызова — текстовый, по образцу протокола скиллов; отчёт
  возвращается в системный промпт, а не в историю.
- Повторный вызов того же инструмента **не выполняется** — данные не
  удваиваются, ход завершается.
- Каждый инструмент обязан отработать в `--background`: этим занимается
  `tests/run_tools_check.py`.
