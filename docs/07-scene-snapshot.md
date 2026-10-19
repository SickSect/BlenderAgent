# Глава 7. Снимок сцены: как Blender «рассказывает» о себе модели

Это глава про тот самый блок текста, который аддон подкладывает в каждый запрос.
Именно от него зависит, отвечает ли модель про **твою** сцену или «в вакууме».

## 7.1 Проблема: модель не видит Blender

Языковая модель — это текст на входе и текст на выходе. У неё **нет** доступа к
`bpy`, к данным сцены, к экрану. Единственный канал — то, что мы напишем в промпте.

Отсюда две крайности:

- **Ничего не передавать.** Тогда «сделай материал для куба» — модель не знает, есть
  ли куб, как он называется, какие материалы уже висят. Она сочинит код наугад.
- **Передать всё.** Технически можно сериализовать весь .blend, но это огромный
  объём: тысячи строк, дорого по токенам, медленно, и 95% данных не нужны для
  конкретного вопроса.

Поэтому аддон шлёт **компактную выжимку** — *снимок сцены* (scene snapshot).

📦 `src/blender_addon/snapshot.py`, функция `build_snapshot()`.

```
Куда попадает:
  operators.py:_system_prompt()
      prompts.system_prompt(lang)        # базовые правила
    + snapshot.build_snapshot()          # ← выжимка о сцене (если включено)
   = system-сообщение каждого запроса
```

Отключается настройкой **«Отправлять снимок сцены»** (`prefs.include_snapshot`,
📦 `preferences.py`). Полезно, если сцена огромная и снимок занимает слишком много
контекста (см. главу 3 про бюджет).

---

## 7.2 Что значит «читать данные Blender»

Вернёмся к главе 1. В Blender есть два корня:

- **`bpy.data`** — все **datablock**'и: объекты, меши, материалы, изображения,
  текстовые блоки. Это «база данных» файла.
- **`bpy.context`** — «то, что сейчас рядом»: активная сцена, выделенные объекты,
  активный объект, режим.

Снимок — это **чтение** (read-only). Он ничего не меняет, поэтому:

- безопасен: его нельзя «уронить» сцену;
- не зависит от контекста вьюпорта (в отличие от `bpy.ops` — см. главу 4).

📦 `snapshot.build_snapshot` собирает строки примерно так:

```python
scene = bpy.context.scene
objects = list(scene.objects)          # объекты именно этой сцены
materials = list(bpy.data.materials)   # все материалы файла
images = list(bpy.data.images)         # все изображения файла
```

Разница важна: `scene.objects` — это то, что видно в сцене; `bpy.data.objects` —
вообще все объекты файла, включая не попавшие ни в одну сцену. **Blender хранит
данные (datablocks) отдельно от их использования в сцене** — это одна из ключевых
идей API.

## 7.3 Скелет снимка

Порядок разделов (📦 `build_snapshot`):

```
Blender 5.2.2 LTS
Scene: Scene | fps=24 | units=METRIC | render=BLENDER_EEVEE
Collections: Collection
Objects: 3
- Cube [MESH] loc=(0.00, 0.00, 0.00) rot_deg=(0.0, 0.0, 0.0) scale=(1.00, 1.00, 1.00) verts=8 edges=12 faces=6 | uv=UVMap | materials=Material,Recolor
- Light [LIGHT] ...
- Camera [CAMERA] ...
Selected: Cube
Active: Cube | mode=OBJECT | - Cube [MESH] ...
Materials: 4
- Recolor nodes=BsdfPrincipled,OutputMaterial,TexImage | base=(0.70, 0.08, 0.06, 1.00) metal=0 rough=0.6 | tex=Albedo[sRGB]
Images: 2
- Albedo 64x64 src=generated
- Rough 32x32 src=file packed Non-Color
```

Разберём по частям.

### Строка версии и сцены

```
Blender 5.2.2 LTS
Scene: Scene | fps=24 | units=METRIC | render=BLENDER_EEVEE
```

Нужна, потому что API Blender **меняется между версиями** (мы уже сталкивались с
именами сокетов и `use_nodes`). Зная версию, модель может не предлагать код «из
будущего»/«из прошлого».

### Объекты

📦 `_describe(obj)` — одна строка на объект:

```
- Cube [MESH] loc=(0.00, 0.00, 0.00) rot_deg=(0.0, 0.0, 0.0) scale=(1.00, 1.00, 1.00) verts=8 edges=12 faces=6 | uv=UVMap | materials=Material,Recolor
```

- `loc` — `obj.location` (вектор XYZ);
- `rot_deg` — поворот, **переведённый в градусы** (`math.degrees`): Blender хранит
  углы в радианах, а человек и модель лучше понимают градусы;
- `scale` — `obj.scale`;
- для меша: `verts/edges/faces` из `obj.data.vertices/edges/polygons`;
- `uv=...` — имена UV-слоёв (`data.uv_layers`), если есть;
- `materials=...` — имена слотов материалов;
- дальше — модификаторы, признак скрытости и т.п.

`obj.type` — тип объекта (`MESH`, `LIGHT`, `CAMERA`, `EMPTY`, ...).

## 7.4 Материалы: раздел `Materials`

Раньше в снимке были только **имена** материалов (в строке объекта). Этого мало:
чтобы «сделать материал похожим на существующий» или «не дублировать его», модель
должна видеть **устройство** материала.

📦 `_describe_material(mat)`:

```
- Recolor nodes=BsdfPrincipled,OutputMaterial,TexImage | base=(0.70, 0.08, 0.06, 1.00) metal=0 rough=0.6 | tex=Albedo[sRGB]
```

Как это получается.

### 1. Включены ли ноды

```python
def _describe_material(mat) -> str:
    line = f"- {mat.name}"
    # ``use_nodes`` is deprecated (removed in Blender 6.0); default to True.
    if not getattr(mat, "use_nodes", True) or getattr(mat, "node_tree", None) is None:
        return f"{line} simple base={_fmt_value(mat.diffuse_color)}"
```

**Материал в Blender** — это datablock. Исторически у него было два режима:

- **простой** (`use_nodes = False`): цвет `diffuse_color`, никаких узлов;
- **нодовый** (`use_nodes = True`): внешний вид описывает дерево узлов `node_tree`.

⚠️ Начиная с Blender 5.x `use_nodes` помечен устаревшим (удалят в 6.0) — материал
считается нодовым всегда. Поэтому мы читаем его через `getattr(mat, "use_nodes",
True)`: если атрибута не станет, код просто продолжит работать. Ветка `simple`
остаётся для старых версий.

### 2. Список узлов

```python
nodes = list(mat.node_tree.nodes)
types = [node.bl_idname.replace("ShaderNode", "") for node in nodes]
summary = "nodes=" + ",".join(types[:MAX_NODES])
```

- `mat.node_tree` — дерево узлов;
- `node.bl_idname` — «техническое» имя типа узла, например `ShaderNodeTexImage`.
  Мы отрезаем префикс `ShaderNode` → `TexImage` (компактнее);
- `[:MAX_NODES]` — ограничитель: не больше 8 имён, иначе снимок распухнет.

### 3. Значения Principled BSDF

Узел `ShaderNodeBsdfPrincipled` — универсальный шейдер поверхности (глава 6). У него
есть **входы-сокеты**; читаем три главных:

```python
bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
if bsdf is not None:
    bits = []
    for key, names in (("base", ("Base Color",)),
                       ("metal", ("Metallic",)),
                       ("rough", ("Roughness",))):
        socket = _socket(bsdf, *names)
        if socket is not None:
            bits.append(f"{key}={_fmt_value(socket.default_value)}")
```

**Сокет** — это вход/выход узла; значение по умолчанию — `socket.default_value`.
Мы уже знаем (глава 6), что **имена сокетов меняются между версиями**, поэтому:

```python
def _socket(node, *names):
    for name in names:
        socket = node.inputs.get(name)   # добавление: свойство-коллекция умеет .get()
        if socket is not None:
            return socket
    return None
```

Сейчас мы спрашиваем по одному имени на слот, но функция готова к списку — например,
`socket = _socket(bsdf, "Emission Color", "Emission")` для старых версий.

### 4. Форматирование значений

```python
def _fmt_value(value) -> str:
    try:
        if hasattr(value, "__len__"):                 # вектор / RGBA
            return "(" + ", ".join(f"{float(v):.2f}" for v in value) + ")"
        return f"{float(value):.3g}"                  # обычное число
    except (TypeError, ValueError):
        return str(value)
```

- Цвета (`Base Color`) — это 4 числа (RGBA), у них есть длина → печатаем кортежем с
  2 знаками: `(0.70, 0.08, 0.06, 1.00)`.
- Скаляры (`Metallic`, `Roughness`) — `:.3g` даёт коротко: `0`, `0.6`, `1`.
- `try/except` — на всякий случай: если попадётся неожиданный тип, печатаем как есть,
  а не падаем (снимок не должен ломать запрос).

### 5. Используемые текстуры

```python
textures = []
for node in nodes:
    if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
        image = node.image
        textures.append(f"{image.name}[{image.colorspace_settings.name}]")
if textures:
    extras.append("tex=" + ",".join(textures[:MAX_IMAGES]))
```

Узел `ShaderNodeTexImage` хранит ссылку `node.image` на datablock-изображение. Мы
пишем имя и **цветовое пространство** в квадратных скобках (`[sRGB]` / `[Non-Color]`)
— это подсказка модели, что за карта (цвет или данные; см. главу 6).

## 7.5 Изображения: раздел `Images`

📦 `_describe_image(image)`:

```
- Rough 32x32 src=file packed Non-Color
- Albedo 64x64 src=generated
```

- `image.size` — кортеж `(ширина, высота)`;
- `image.source` — откуда картинка: `GENERATED` (создана в Blender),
  `FILE` (загружена с диска), `SEQUENCE`, `MOVIE`, `VIEWER`. Печатаем в нижнем
  регистре (`src=file`);
- флаги:
  - `packed` — есть `image.packed_file`, то есть картинка **вшита в .blend**. Это
    важно: внешний файл может потеряться при переносе проекта;
  - `Non-Color` (или другое нестандартное) — цветовое пространство не `sRGB`. Для
    карт данных (`roughness`, `metallic`, `normal`) именно `Non-Color` и нужно;
  - `missing` — картинка из файла, но данные не загружены (`has_data == False`),
    вероятно, файл не найден.

Что мы **не** показываем: содержимое пикселей (это мегабайты) и всегда-присутствующие
служебные изображения `Render Result` / `Viewer Node` — они исключаются:

```python
_IMAGE_BLACKLIST = {"Render Result", "Viewer Node"}
```

## 7.6 Лимиты: почему везде `MAX_*`

```python
MAX_OBJECTS = 40
MAX_MATERIALS = 40
MAX_IMAGES = 20
MAX_NODES = 8
```

Снимок уходит в **каждый** запрос, поэтому он должен быть ограничен. Если элементов
больше — печатаем первые N и строку-хвост через `i18n`:

```python
if len(materials) > MAX_MATERIALS:
    lines.append(i18n.T("snapshot.more_materials", n=len(materials) - MAX_MATERIALS))
```

Это связано с **бюджетом контекста** из главы 3: у модели ограниченное «окно», и
каждый лишний символ снимка съедает место у истории диалога.

## 7.7 Как снимок попадает в запрос

📦 `operators._system_prompt`:

```python
def _system_prompt(context) -> str:
    prefs = preferences.get_prefs(context)
    lang = prefs.language.lower() if prefs is not None else i18n.DEFAULT_LANG
    text = prompts.system_prompt(lang)
    include = prefs.include_snapshot if prefs is not None else True
    if include:
        try:
            text += "\n\n" + i18n.T("prompt.snapshot_header") + "\n" + snapshot.build_snapshot()
        except Exception as exc:  # a broken snapshot must not block the chat
            text += f"\n\n(Snapshot unavailable: {exc})"
    return text
```

Обрати внимание на `try/except`: **снимок не должен ломать чат**. Если он почему-то
не собрался (необычный объект, неожиданный тип), запрос всё равно уйдёт — просто без
части контекста.

А сам `prompts.py` теперь **явно просит** модель пользоваться новыми разделами
(не дублировать материалы, ставить `Non-Color`, предпочитать data API) — см. главу 6.

## 7.8 Blender-понятия из этой главы (шпаргалка)

| Термин | Что это |
|---|---|
| **datablock** | именованный блок данных (`bpy.data.materials`, `images`, ...) |
| **сцена vs данные** | `scene.objects` — что в сцене; `bpy.data.*` — все datablocks файла |
| **RNA** | система свойств Blender, к которым мы обращаемся из Python |
| **node tree** | дерево узлов материала (`mat.node_tree`) |
| **узел / сокет** | «коробочка» и её вход/выход (`node.inputs.get(name)`) |
| **colorspace** | интерпретация пикселей: `sRGB` (цвет) vs `Non-Color` (данные) |
| **packed** | изображение вшито в .blend (`image.packed_file`) |
| **`use_nodes`** | режим нод (устаревает, удалят в Blender 6.0) |

## 7.9 Что попробовать руками

1. Включи «Отправлять снимок сцены» и попроси: *«перечисли материалы в сцене и их
   base color»*. Сверь ответ с панелью **Material Properties**.
2. Добавь узел текстуры (`Shader Editor` → Image Texture) и спроси снова — в снимке
   появится `tex=...`.
3. Загрузи картинку и не ставь `Non-Color` — попроси модель сделать карту
   шероховатости; посмотри, поправит ли она colorspace.
4. Выключи снимок в настройках и задай вопрос про сцену — модель «ослепнет».

## 7.10 Итог главы

- Снимок — это **текстовая выжимка** о сцене, которую видят только модель (через
  промпт), а не сам Blender.
- Он **read-only** и не зависит от контекста вьюпорта.
- Разделы: версия, сцена, объекты, выделение/актив, **материалы**, **изображения**.
- Материалы раскрывают узлы, значения Principled и текстуры; изображения — размер,
  источник, colorspace и `packed`.
- Имена сокетов и `use_nodes` **меняются между версиями** — отсюда устойчивые
  помощники `_socket` и `getattr(..., default)`.
- Всё ограничено `MAX_*` и локализованными «... и ещё N», чтобы не съедать контекст.

Дальше по плану — пересборка и живой тест в Blender (шаг A), затем кнопки-удобства
для материалов (шаг B).
