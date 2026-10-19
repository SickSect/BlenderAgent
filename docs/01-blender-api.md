# Глава 1. Основы Blender API

Эта глава — фундамент. Без неё остальные главы будут «магией». Здесь объясняются
четыре кита, на которых стоит любой аддон Blender, с примерами, которые можно
запустить прямо в Blender.

## Оглавление главы

1. [Что такое `bpy` и где живёт код](#11-что-такое-bpy-и-где-живёт-код)
2. [`bpy.data` — библиотека данных](#12-bpydata--библиотека-данных)
3. [`bpy.context` — «где я нахожусь»](#13-bpycontext--где-я-нахожусь)
4. [`temp_override` — подмена контекста](#14-temp_override--подмена-контекста)
5. [`bpy.ops` — операторы](#15-bpyops--операторы)
6. [Свои операторы, панели, свойства (RNA)](#16-свои-операторы-панели-и-свойства-rna)
7. [Таймеры и обработчики](#17-таймеры-и-обработчики-почему-у-нас-нет-потоков)
8. [Клавиши, жизненный цикл аддона, шпаргалка](#18-клавиши-жизненный-цикл-аддона-шпаргалка)

Обозначения: 🧪 — можно выполнить в Blender; ⚠️ — ловушка; 📦 — ссылка на код
аддона.

---

## 1.1 Что такое `bpy` и где живёт код

Blender — приложение на C++, но внутри него **встроен интерпретатор Python**
(в Blender 4.2–5.x это Python 3.13). Модуль `bpy` — «мост»: он показывает
C++-объекты Blender как Python-объекты.

Модель выполнения важна для понимания всего остального:

- Python-код крутится **в главном потоке**, в паузах между кадрами интерфейса.
- Пока выполняется твой скрипт, интерфейс «замирает».
- Долгие операции (сеть, тяжёлые вычисления) нельзя держать в этом потоке — иначе
  Blender зависнет. Именно поэтому у нас есть **отдельный процесс-воркер**
  (глава 3), а не сетевые запросы «в лоб».

🧪 Куда вставлять примеры:

1. Открой в Blender окно **Scripting** (Text Editor).
2. Вставь код, нажми **Run Script** (`Alt+P`).
3. Вывод смотри в **Window → Toggle System Console** (Windows) или в терминале,
   откуда запущен Blender.

---

## 1.2 `bpy.data` — библиотека данных

`bpy.data` — это **всё содержимое .blend-файла**, независимо от того, что видно
на экране. У каждого типа данных своя коллекция:

```python
bpy.data.objects          # все объекты файла
bpy.data.meshes           # вся геометрия (меши)
bpy.data.materials        # все материалы
bpy.data.images           # все изображения
bpy.data.texts            # текстовые блоки (Text Editor)
bpy.data.collections      # коллекции («папки» объектов)
bpy.data.node_groups      # переиспользуемые группы нод
```

🧪 Посмотреть, что уже есть в файле:

```python
import bpy

print("Объектов:", len(bpy.data.objects))
for obj in bpy.data.objects:
    print(" -", obj.name, "| тип:", obj.type)

cube = bpy.data.objects.get("Cube")   # .get() вернёт None, если объекта нет
if cube is not None:
    print("Куб стоит в", tuple(cube.location))
```

Важное правило: **данные не привязаны к экрану**. Объект живёт в
`bpy.data.objects`, даже если вне кадра или скрыт.

⚠️ Единица данных называется **datablock**. У каждого есть имя `name`, но имя —
не идентификатор: два разных datablock могут называться одинаково, а при
копировании Blender добавляет `.001`, `.002`…

### Объект ≠ геометрия

Ключевая идея Blender: **объект** (`Object`) — это контейнер в пространстве
(позиция, поворот, масштаб, родитель, модификаторы). **Его `data`** — то, что он
показывает (меш, кривая, камера, свет).

```python
cube = bpy.data.objects.get("Cube")
print(cube.location)             # где стоит объект
print(cube.data)                 # сам меш (bpy.types.Mesh)
print(len(cube.data.vertices))   # сколько вершин в геометрии
```

Поэтому два объекта могут ссылаться на **один и тот же меш** — так экономят
память. Именно это делает `bpy.ops.object.duplicate(linked=True)`.

### Файл vs сцена

- `bpy.data.objects` — **все** объекты файла.
- `bpy.context.scene.objects` — только объекты текущей сцены (то, что видно).

📦 `src/blender_addon/snapshot.py` перечисляет именно `scene.objects`: модели
нужно знать про видимую сцену, а не про «запасники» файла.

---

## 1.3 `bpy.context` — «где я нахожусь»

`bpy.context` — это **состояние интерфейса и пользователя**, а не данные: какое
окно активно, какой редактор открыт, что выделено, в каком режиме работаем.

```python
bpy.context.scene            # активная сцена
bpy.context.view_layer       # активный слой видимости
bpy.context.object           # активный объект (может быть None)
bpy.context.selected_objects # выделенные объекты
bpy.context.mode             # 'OBJECT', 'EDIT', 'SCULPT', ...
bpy.context.window           # окно
bpy.context.area             # область: VIEW_3D, OUTLINER, NODE_EDITOR, ...
bpy.context.region           # конкретная часть area: WINDOW, UI, HEADER
bpy.context.preferences      # настройки Blender
bpy.context.window_manager   # менеджер окон (тут живёт состояние нашего чата)
```

⚠️ **Главная ловушка всего Blender API**: содержимое `bpy.context` зависит от
того, *откуда* выполняется код. Скрипт из Text Editor, кнопка в боковой панели и
модальное popup-окно дают **разный** контекст. Именно из-за этого в аддоне
появился `_exec_context()` (глава 4).

🧪 Один и тот же код выведет разный `area.type` в зависимости от того, где стоит
курсор:

```python
import bpy
print("area:", bpy.context.area.type if bpy.context.area else None,
      "| region:", bpy.context.region.type if bpy.context.region else None)
```

### `view_layer` и депграф

`bpy.context.view_layer` связан с **dependency graph (депграф)** — внутренним
«графиком зависимостей»: что от чего зависит и что нужно пересчитать после правок.
Пользователь касается его косвенно:

```python
bpy.context.view_layer.update()   # «пересчитай состояние сейчас»
```

Мы вызываем это сразу после AI-кода; почему — разбор падения в депграфе в главе 4.

---

## 1.4 `temp_override` — подмена контекста

С Blender 3.2 есть менеджер контекста `temp_override`, который **временно
подставляет** нужные поля контекста:

```python
import bpy

window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")

with bpy.context.temp_override(window=window, area=area, region=region,
                               space_data=area.spaces.active):
    bpy.ops.object.duplicate()   # теперь ведёт себя как Shift+D во вьюпорте
```

Это ключевой приём, когда код выполняется «не из того» редактора. 📦 Реальная
реализация — `_exec_context()` в `src/blender_addon/operators.py`, разбор в главе 4.

---

## 1.5 `bpy.ops` — операторы

**Оператор** — действие, оформленное так, чтобы его могли вызвать интерфейс
(кнопка, меню, хоткей) и скрипт. У оператора три необязательные части:

| Часть | Роль |
|---|---|
| `poll(context)` | проверка «можно ли сейчас»; `False` → оператор недоступен |
| `execute(context)` | само действие (вызов из кнопки/скрипта) |
| `invoke(context, event)` | интерактивный запуск (мышь, модальный режим) |

Вызов встроенных операторов и возможные результаты:

```python
bpy.ops.object.duplicate()
result = bpy.ops.object.delete()   # возвращает set строк
```

| Результат | Значение |
|---|---|
| `{'FINISHED'}` | выполнено успешно |
| `{'CANCELLED'}` | отменено (или не прошёл `poll`) |
| `{'RUNNING_MODAL'}` | оператор стал модальным (ждёт мышь/клавиатуру) |

⚠️ Операторы **контекстно-зависимы**: `bpy.ops.object.duplicate()` работает с
выделенными объектами, `bpy.ops.uv.smart_project()` требует режим Edit и
3D-вьюпорт. «Не тот» контекст → оператор либо откажется, либо сделает не то.

🧪 Проверить доступность оператора в текущем контексте:

```python
import bpy
print(bpy.ops.object.duplicate.poll())   # True или False
```

### Свойства оператора

Параметры оператора объявляются как свойства RNA, и их можно передавать из Python:

```python
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 3))
```

📦 Так сделан `gaa.chat_window`: он хранит `self._wrap` (число символов в строке),
посчитанное при открытии окна.

### Два стиля создания объектов

**Стиль A — через операторы** (как будто нажали кнопку):

```python
import bpy
bpy.ops.mesh.primitive_cube_add(size=2.0)
bpy.ops.object.duplicate()
bpy.ops.object.shade_smooth()
```

Плюс: коротко. Минус: зависит от контекста и выделения.

**Стиль B — напрямую через `bpy.data`** (детерминированно):

```python
import bpy
mesh = bpy.data.meshes.new("MyMesh")
obj = bpy.data.objects.new("MyObject", mesh)
bpy.context.scene.collection.objects.link(obj)   # без link объект не в сцене!
```

Плюс: предсказуемо, не зависит от выделения. Минус: больше строк и вся ручная
работа со связями.

⚠️ Объект, созданный `bpy.data.objects.new()`, **не появится в сцене**, пока не
сделаешь `.link(...)`. Классическая ошибка новичка.

📦 AI-код — это обычный Python, поэтому модель может использовать оба стиля.
В `prompts.py` мы просим писать самодостаточные скрипты с импортами.

---

## 1.6 Свои операторы, панели и свойства (RNA)

Blender хранит данные в системе **RNA** — C-структуры с метаданными. Python-поля
«прорастают» в RNA при регистрации класса. Это объясняет две вещи:

- `bl_label` / `bl_description` / докстринг оператора **копируются в RNA при
  регистрации**, поэтому смена языка не применяется сама (см. главу 5);
- свойства вроде `bpy.types.WindowManager.gaa_input` видны и RNA (сохраняются в
  `.blend`, показываются в интерфейсе).

### Свой оператор

```python
import bpy

class MY_OT_hello(bpy.types.Operator):
    bl_idname = "my.hello"        # уникальный id: "модуль.действие"
    bl_label = "Say Hello"        # надпись на кнопке / в тултипе
    bl_options = {"REGISTER"}

    def execute(self, context):
        print("Привет!")
        return {"FINISHED"}

def register():
    bpy.utils.register_class(MY_OT_hello)

def unregister():
    bpy.utils.unregister_class(MY_OT_hello)
```

Вызвать: `bpy.ops.my.hello()` или кнопкой `layout.operator("my.hello")`.

### Соглашения об именах

- Класс: `MY_OT_hello` (`<ПРЕФИКС>_OT_<имя>`).
- `bl_idname`: `<модуль>.<операция>` — у нас `gaa.send`, `gaa.run_code` и т.д.
- Префикс `GAA`/`gaa` — исторический (был Game Asset Assistant). Мы **намеренно**
  оставили его при переименовании в Asset Assistant: менять `bl_idname` значит
  ломать сохранённые хоткеи и внешние скрипты. Это «внутренний неймспейс», а не
  название продукта.

### `bl_options` — права оператора

| Флаг | Смысл |
|---|---|
| `"REGISTER"` | сохранять значения свойств между вызовами |
| `"UNDO"` | перед выполнением создаётся шаг Undo → изменения отменяются `Ctrl+Z` |
| `"INTERNAL"` | не показывать в меню поиска `F3` |

📦 `GAA_OT_run_code` объявлен как `{"REGISTER", "UNDO"}` — весь AI-скрипт
становится одним шагом отмены (см. главу 4).

### `poll()` — «можно ли сейчас»

```python
@classmethod
def poll(cls, context):
    return bool(context.window_manager.gaa_input.strip())
```

Пока `poll` возвращает `False`, оператор недоступен (кнопка серая, программный
вызов даёт `RuntimeError`). 📦 Так `gaa.send` запрещает пустое сообщение, а
`gaa.run_code` — повторный запуск того же кода.

### PropertyGroup — структура данных

Чтобы хранить составной объект (у нас — сообщение чата), наследуют
`bpy.types.PropertyGroup`:

```python
from bpy.props import StringProperty, CollectionProperty, IntProperty

class MY_Item(bpy.types.PropertyGroup):
    role: StringProperty(default="user")
    body: StringProperty(default="")

# привязка к WindowManager:
bpy.types.WindowManager.my_items = CollectionProperty(type=MY_Item)
bpy.types.WindowManager.my_active = IntProperty(default=0)
```

Доступ: `wm.my_items`, `wm.my_items.add()`, `wm.my_items.clear()`.

📦 Именно так устроена история чата: `gaa_messages` (коллекция `GAAChatMessage`)
и `gaa_active` (индекс выбранной строки) — `src/blender_addon/ui.py`.

### Типы свойств (`bpy.props`)

| Свойство | Для чего | Пример |
|---|---|---|
| `BoolProperty` | да/нет | `default=True` |
| `IntProperty` | целое | `default=560, min=300, max=1400` |
| `FloatProperty` | дробное | `default=0.5, min=0.0, max=2.0` |
| `StringProperty` | строка | `default="http://localhost:11434"` |
| `EnumProperty` | выбор из списка | `items=[("RU","Русский","..."),("EN","English","...")]` |
| `CollectionProperty` | список объектов | `type=MY_Item` |
| `PointerProperty` | ссылка на datablock | `type=bpy.types.Object` |

⚠️ `name: TypeProperty(...)` — это **аннотация**, которую Blender читает при
регистрации класса, а не обычная переменная.

### `update=` — реакция на изменение

```python
def _on_language_update(self, _context):
    from . import operators
    operators.refresh_labels()

language: EnumProperty(items=[...], update=_on_language_update)
```

📦 `src/blender_addon/preferences.py`: при смене языка перерегистрируем операторы,
чтобы их подписи (запечённые в RNA) перевелись заново.

### AddonPreferences — настройки

```python
class GAA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__          # имя пакета аддона

    temperature: FloatProperty(default=0.5, min=0.0, max=2.0)

    def draw(self, context):
        self.layout.prop(self, "temperature")
```

📦 `src/blender_addon/preferences.py`. Достаются через
`bpy.context.preferences.addons[__package__].preferences`.

### Панели

Три `bl_*` поля задают, **где** появится панель:

```python
class GAA_PT_main(bpy.types.Panel):
    bl_label = "AI Assistant"
    bl_idname = "GAA_PT_main"
    bl_space_type = "VIEW_3D"     # редактор: VIEW_3D / NODE_EDITOR / IMAGE_EDITOR
    bl_region_type = "UI"         # боковая панель N
    bl_category = "AI"            # вкладка

    def draw(self, context):
        layout = self.layout
        layout.label(text="Привет", icon="INFO")
        row = layout.row(align=True)
        row.operator("gaa.send", icon="PLAY")
        row.operator("gaa.stop", icon="PAUSE")
```

Одна панель привязана ровно к одной паре `(space_type, region_type)`, поэтому
«чат в трёх редакторах» — это три маленьких класса с общим миксином
`_GAAChatPanelMixin` (📦 `src/blender_addon/ui.py`).

### UIList — список с прокруткой

```python
class GAA_UL_messages(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        layout.label(text=item.body.splitlines()[0] if item.body else "...")

# в панели:
layout.template_list("GAA_UL_messages", "", wm, "gaa_messages",
                     wm, "gaa_active", rows=7)
```

---

## 1.7 Таймеры и обработчики (почему у нас нет потоков)

Правило Blender: **нельзя запускать Python-потоки, живущие дольше скрипта** — они
«переживают» вызов и приводят к падениям в C-коде отрисовки. В документации API это
называется «Python Threads are Not Supported».

Вместо потока используют **таймер** — функцию, которую Blender сам регулярно
вызывает в главном потоке:

```python
import bpy

def tick():
    print("тик")
    return 0.5     # вызвать снова через 0.5 с
    # return None  # вернуть None, чтобы остановить таймер

bpy.app.timers.register(tick, first_interval=1.0)
```

📦 На этом построен весь чат: `src/blender_addon/state.py` раз в 0.1 с читает новые
события воркера и обновляет интерфейс (глава 3).

**Обработчики** (`bpy.app.handlers`) — события жизненного цикла Blender
(загрузка файла, сохранение, отрисовка кадра):

```python
def on_load(*_args):
    print("файл загружен")

bpy.app.handlers.load_post.append(on_load)
```

📦 `src/blender_addon/__init__.py` через `load_post` сбрасывает историю чата при
загрузке нового .blend: свойства `WindowManager` при загрузке обнуляются, и без
этого экран и внутренняя история «разошлись» бы.

---

## 1.8 Клавиши, жизненный цикл аддона, шпаргалка

### Клавиши (keymap)

```python
wm = bpy.context.window_manager
kc = wm.keyconfigs.addon
km = kc.keymaps.new(name="Window", space_type="EMPTY")
kmi = km.keymap_items.new("gaa.chat_window", "A", "PRESS", ctrl=True, alt=True)
```

Храни пару `(km, kmi)` и удаляй её в `unregister()`, иначе после выключения
аддона останутся «мёртвые» горячие клавиши.

⚠️ В headless-режиме (`blender --background`) раскладок может не быть:
`keyconfigs.addon` бывает `None` — нужна проверка.

📦 `src/blender_addon/__init__.py`, `_register_keymap` / `_unregister_keymap`.

### `register()` / `unregister()` — жизненный цикл

Blender вызывает две функции модуля явно:

```python
def register():
    bpy.utils.register_class(SomeOperator)
    bpy.utils.register_class(SomePanel)
    bpy.types.WindowManager.some_prop = StringProperty()
    bpy.app.handlers.load_post.append(on_load)

def unregister():
    bpy.app.handlers.load_post.remove(on_load)
    del bpy.types.WindowManager.some_prop
    bpy.utils.unregister_class(SomePanel)
    bpy.utils.unregister_class(SomeOperator)
```

Правила:

1. В `register` регистрация идёт «вверх», в `unregister` — в обратном порядке.
2. **Всё, что создал, убери**: классы, свойства, обработчики, клавиши, таймеры.
3. Blender при перезагрузке аддона вызывает `unregister()` затем `register()`.

📦 `src/blender_addon/__init__.py`: в `unregister()` сначала глушится таймер и
убивается воркер, и только потом снимаются классы.

### Мини-шпаргалка

```python
import bpy
from bpy.props import StringProperty, IntProperty

bpy.data.objects.get("Cube")             # найти объект
bpy.data.materials.new("Mat")            # создать материал
bpy.context.selected_objects             # выделенные
bpy.context.view_layer.update()          # пересчитать депграф
bpy.ops.object.duplicate()               # контекстно-зависимо!
bpy.utils.register_class(MyClass)        # регистрация класса
bpy.app.timers.register(tick, first_interval=0.1)   # отложенный вызов
```

Дальше — глава 2: как из этих кирпичей собран наш аддон.
