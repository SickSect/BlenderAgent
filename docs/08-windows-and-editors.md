# Глава 8. Окна Blender из скрипта: screen, area, region, space

Эта глава — про то, как из Python **управлять самим интерфейсом** Blender, а не
только сценой. Повод конкретный: в чат добавлены две кнопки —

- **Shader Editor** — открыть редактор нод для материала активного объекта;
- **Image Editor** — открыть редактор изображений на текстуре этого материала.

Чтобы понять, *как* такой код пишется (и почему он выглядит именно так), нужно
разобраться в устройстве окон Blender.

📦 `src/blender_addon/operators.py` — `GAA_OT_open_shader_editor`,
`GAA_OT_open_image_editor` и помощники `_find_screen` / `_find_area`.

---

## 8.1 Интерфейс Blender — это дерево объектов

До сих пор мы читали/меняли **данные сцены** (`bpy.data`). Но окна Blender — тоже
объекты, к которым есть доступ из Python. Иерархия такая:

```
Window                     одно окно ОС
└── Screen                 «рабочее пространство» (Layout, Shading, …)
    └── Area               прямоугольная панель (viewport, outliner, …)
        └── Region         часть области (шапка, сайдбар, окно)
            └── Space      конкретный редактор со своими настройками
```

Разберём по уровням.

### Screen — раскладка

`Screen` — это то, что переключается вкладками сверху (Layout / Modeling / Shading /
…). У него есть имя (`Screen("Layout")`) и список **областей**: `screen.areas`.

📦 `_find_screen`:

```python
def _find_screen(context):
    """The ``Screen`` behind the current window, or ``None`` in background mode."""
    screen = getattr(context, "screen", None)
    if screen is None:
        screen = getattr(getattr(context, "window", None), "screen", None)
    return screen
```

В обычном Blender `context.screen` уже есть. Запасной путь через `context.window`
нужен для нестандартных ситуаций (например, `--background`, где `context.area`
может быть пустым).

### Area — панель

`Area` — прямоугольник на экране. У него два ключевых свойства:

- `area.type` — **какой это редактор**: `'VIEW_3D'`, `'NODE_EDITOR'`,
  `'IMAGE_EDITOR'`, `'PROPERTIES'`, `'OUTLINER'`, …;
- `area.width` / `area.height` — размеры в пикселях;
- `screen.areas` — коллекция всех областей экрана.

⚙️ `Area.type` **записываемый**: присвоив ему другое значение, мы меняем редактор —
ровно то, что делает выпадающий список типа редактора в шапке.

### Region — часть области

Внутри области есть **regions**: `WINDOW` (основное содержимое), `HEADER` (шапка),
`UI` (боковая панель, которую мы открываем клавишей N), `TOOL_HEADER` и т.д.

Именно в регион типа `UI` рисуются наши панели чата (`GAA_PT_main`,
`GAA_PT_shader`, `GAA_PT_image` — глава 5). Поэтому важно: **если превратить в
другой редактор ту область, откуда нажали кнопку, сайдбар с чатом исчезнет** —
учитываем это в §8.3.

### Space — редактор внутри области

У области есть **активное пространство** — `area.spaces.active`. Это и есть
редактор с его настройками. Тип пространства зависит от `Area.type`:

| `Area.type` | Python-тип | Что настраиваем |
|---|---|---|
| `VIEW_3D` | `SpaceView3D` | камера, шейдинг, оверлеи |
| `NODE_EDITOR` | `SpaceNodeEditor` | `shader_type`, `node_tree`, `pin` |
| `IMAGE_EDITOR` | `SpaceImageEditor` | `image`, `image_user` |
| `PROPERTIES` | `SpaceProperties` | активная вкладка свойств |

⚠️ У области может храниться **несколько** пространств (стежок), а `spaces.active`
— то, что показано сейчас. В обычном коде мы работаем именно с `active`.

---

## 8.2 Прототип: «открыть Shader Editor»

Алгоритм простой:

```
1. понять, какой Screen сейчас перед пользователем;
2. найти Area, где уже открыт NODE_EDITOR, — если есть, переиспользовать;
3. иначе взять другую область и сменить ей type на "NODE_EDITOR";
4. настроить её Space (shader_type/pin или image).
```

📦 `_find_area`:

```python
def _find_area(context, target_type: str):
    screen = _find_screen(context)
    if screen is None:
        return None
    for area in screen.areas:
        if area.type == target_type:
            return area
    origin = getattr(context, "area", None)
    candidates = [area for area in screen.areas if area != origin]
    if not candidates:
        candidates = list(screen.areas)
    if not candidates:
        return None
    area = max(candidates, key=lambda a: a.width * a.height)
    area.type = target_type
    return area
```

Разберём решения по шагам.

**Почему сначала «переиспользовать»?** Если Shader Editor уже открыт, не нужно
ничего ломать — просто настроим его. Это самый предсказуемый для пользователя
вариант.

**Почему `!=`, а не `is not`?** Это важная ловушка Blender API.

> ⚠️ Каждое обращение к `area` из коллекции возвращает **новую Python-обёртку** вокруг
> одного и того же объекта Blender. Поэтому `area is other` почти всегда `False`,
> даже если это одна и та же область. Оператор `==` у таких типов сравнивает
> **указатели на реальный объект**, поэтому корректно именно `area != origin`.
> (Мы наткнулись на это при разработке: сначала стояло `is not`, и «исключение
> исходной области» не работало — менялась именно та область, в которой находится
> чат.)

**Почему исключаем `context.area`?** `context.area` — та область, из которой
вызван оператор (где нажали кнопку). Если её же и переключить, исчезнет сайдбар с
кнопкой — а если это был 3D-вьюпорт, пользователь «потеряет» сцену.

**Почему берём самую большую область?** Больше места — удобнее смотреть ноды или
текстуру. Это простое эвристическое правило; в редком случае с одной областью
(раскладка «на весь экран») откатываемся к ней же — иначе открывать нечего.

---

## 8.3 Настройка редактора нод (Shader Editor)

📦 `GAA_OT_open_shader_editor`:

```python
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
    space.shader_type = "OBJECT"
    space.pin = False
except Exception as exc:
    self.report({"WARNING"}, i18n.T("msg.editor_failed", exc=exc))
    return {"CANCELLED"}
```

Ключевые свойства `SpaceNodeEditor`:

- **`shader_type`** — что редактируем: `"OBJECT"` (материал объекта), `"WORLD"`,
  `"LINE_STYLE"`. Нам нужен `"OBJECT"`.
- **`pin`** — «прикрепить» редактор к конкретному дереву. `pin = False` означает
  «следуй за активным объектом»: редактор сам показывает материал активного объекта.
  Так как мы и сообщаем именно про материал активного объекта — это ровно то, что
  нужно (и не надо вручную подсовывать `node_tree`).

📦 `_active_object_material`:

```python
def _active_object_material(context):
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    if obj is None:
        return None, None
    return obj, getattr(obj, "active_material", None)
```

- `context.active_object` — активный объект **view layer** (глава 1).
- `obj.active_material` — материал в **активном слоте** (тот, что выбран в списке
  слотов, а не обязательно первый).

📍 Если у объекта материала нет — не падаем, а пишем пользователю
`msg.no_material` («У активного объекта нет материала»). Редактор при этом уже
открыт: возможно, человек хочет назначить материал вручную.

---

## 8.4 Настройка редактора изображений (Image Editor)

📦 `GAA_OT_open_image_editor` (суть):

```python
area = _find_area(context, "IMAGE_EDITOR")
...
_obj, mat = _active_object_material(context)
image = _first_material_image(mat) if mat is not None else None
if image is None:
    image = bpy.data.images.get("Render Result")
if image is None and len(bpy.data.images):
    image = bpy.data.images[0]
if image is None:
    self.report({"INFO"}, i18n.T("msg.no_image"))
    return {"FINISHED"}

area.spaces.active.image = image
```

Здесь:

- **`space.image`** — какая картинка показана в Image Editor;
- 📦 `_first_material_image` проходит по нодам материала и берёт первую
  `ShaderNodeTexImage` с картинкой:

  ```python
  for node in tree.nodes:
      if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
          return node.image
  ```

- если у материала нет текстур — показываем хоть что-то полезное: сначала
  `"Render Result"` (результат последнего рендера), иначе первое изображение файла.

Так кнопка **всегда** что-то показывает, если картинки вообще есть.

---

## 8.5 Где это в интерфейсе

📦 `ui.py`, общая функция `draw_chat` (её рисуют все хосты):

```python
row = layout.row(align=True)
row.operator("gaa.open_shader_editor", icon="NODETREE")
row.operator("gaa.open_image_editor", icon="IMAGE_DATA")
```

Так как `draw_chat` один на все панели, кнопки появляются и в 3D-вьюпорте, и в
Shader/Image-редакторах, и во всплывающем окне. Подписи берутся из `i18n.py`
(`op.open_shader.*`, `op.open_image.*`), как у любых операторов (глава 5).

---

## 8.6 Чего мы сознательно не делаем

- **Не делим область** (`screen.area_split`). Это можно сделать из скрипта, но
  требует ручных `context`-override и легко оставляет интерфейс в странном
  состоянии; к тому же такой код трудно проверить headless. Открытие «в самой
  большой свободной области» даёт 90% пользы без риска.
- **Не меняем область, из которой нажали кнопку.** Как в §8.2: там живёт чат.
- **Не пытаемся угадать «правильную» область по имени рабочего пространства.**
  Имена (Layout/Shading/…) локализуются и меняются — завязываться на них нельзя.

---

## 8.7 Что попробовать руками

1. Выдели объект с материалом, в чате нажми **Shader Editor** — откроется нода
   этого материала (убедись, что редактор «следует» за выделением: переключи
   объект — дерево сменится).
2. Нажми **Image Editor** — откроется первая текстура материала.
3. Убери все материалы у объекта и нажми снова: придёт сообщение «У активного
   объекта нет материала», Blender не упадёт.
4. Сравни с штатным способом: выпадающий список типа редактора в шапке области.

---

## 8.8 Шпаргалка терминов

| Термин | Что это |
|---|---|
| **Screen** | раскладка (рабочее пространство), содержит области |
| **Area** | панель интерфейса; `Area.type` задаёт редактор |
| **Region** | часть области (шапка/сайдбар/окно) |
| **Space** | редактор внутри области (`area.spaces.active`) |
| **bpy wrapper** | новая Python-обёртка на каждый доступ → сравнивай через `==`, не `is` |
| **`shader_type`** | режим Shader Editor: OBJECT / WORLD / … |
| **`pin`** | Shader Editor следит за активным объектом (`False`) или закреплён (`True`) |

## 8.9 Итог главы

- Оконная система Blender — это дерево `Screen → Area → Region → Space`, доступное
  из Python.
- Чтобы **показать** редактор, нужно найти/сменить `Area.type`, а затем настроить
  `area.spaces.active`.
- Сравнивать bpy-объекты надо через `==` — обёртки каждый раз новые.
- Кнопки **Shader Editor** и **Image Editor** открывают нужный редактор на
  материале/текстуре активного объекта и никогда не ломают ту область, где живёт чат.

Дальше по шагу B — быстрые «пресеты» запросов (кнопки, которые подставляют готовый
текст в поле ввода).
