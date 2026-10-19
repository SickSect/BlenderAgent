# Глава 6. Материалы и текстуры

Это «кулинарная книга» для следующего этапа аддона. Здесь — минимум теории и много
готовых рецептов, которые можно вставлять в Text Editor и запускать. Всё, что описано,
модель сможет генерировать в виде bpy-кода, а ты — запускать кнопкой «Выполнить код».

Прежде чем что-то менять в сцене — **сохрани файл** (`Ctrl+S`). Правило из главы 4
действует всегда.

## 6.1 Три сущности: меш, объект, материал

Вспомним из главы 1:

- **Mesh** — геометрия (вершины, грани, UV). Живёт в `bpy.data.meshes`.
- **Object** — «контейнер в пространстве», ссылается на меш через `obj.data`.
- **Material** — описание внешнего вида. Живёт в `bpy.data.materials`.

Связь «объект ↔ материал» идёт через **слоты материалов** (`material_slots`).
Слотов у объекта может быть несколько; каждое **лицо** меша указывает на свой слот
через `polygon.material_index`.

```
Object
 ├─ data → Mesh
 │           ├─ polygons[].material_index ──┐
 │           └─ uv_layers                   │
 └─ material_slots[i] ─────────────────────►│
         └─ material → Material              │  (индекс i)
```

Материал один, а слотов/объектов, которые на него ссылаются, может быть много — как
меш, разделяемый между объектами. Будь осторожен при правках «общего» материала.

---

## 6.2 Создать и назначить материал

### Через `bpy.data` (предсказуемо)

🧪

```python
import bpy

obj = bpy.context.active_object
if obj is None or obj.type != "MESH":
    raise RuntimeError("Сначала выдели меш-объект")

mat = bpy.data.materials.new(name="Recolor")   # 1. создать материал
mat.use_nodes = True                            # 2. включить ноды (обычно нужно)

obj.data.materials.clear()                      # (опционально) убрать старые слоты
obj.data.materials.append(mat)                  # 3. назначить объекту
```

Разбор:

- `bpy.data.materials.new(name=...)` — создаёт datablock. Имя будет уникализировано
  (`Recolor.001`), если такое уже есть.
- `use_nodes = True` — включает нодовый редактор. Без этого материал рендерится по
  «простым» настройкам (`diffuse_color`), и работать с нодами нельзя.
  ⚠️ Начиная с Blender 5.x `use_nodes` помечен устаревшим (удалят в 6.0) — материал и
  так нодовый. В старом коде его можно встретить; в новом лучше не полагаться на него,
  а сразу работать с `mat.node_tree`.
- `obj.data.materials.append(mat)` — добавляет **новый слот** в конец списка. Если
  хочешь заменить слот 0, пиши `obj.data.materials[0] = mat`.

### Через оператор (как кнопкой)

```python
bpy.ops.object.material_slot_add()   # добавить слот активному объекту
```

⚠️ Оператор зависит от контекста (активный объект, режим). Если вызываешь из скрипта —
это обычно ок; если из popup — см. главу 4 про `temp_override`.

### Найти материал, не создавая дубликат

```python
mat = bpy.data.materials.get("Recolor") or bpy.data.materials.new("Recolor")
```

`get()` вернёт `None`, если материала нет, — идиома «найти или создать» удобна в
скриптах, которые могут запускаться повторно.

---

## 6.3 Нодовый граф: узлы, сокеты, связи

Материал с `use_nodes = True` содержит **дерево нод** (`mat.node_tree`). Мысленная
модель:

- **Узел (node)** — «коробочка» с входами и выходами.
- **Сокет (socket)** — конкретный вход/выход узла (`node.inputs[...]`, `node.outputs[...]`).
- **Связь (link)** — провод, соединяющий выход одного узла с входом другого.
- **Выходной узел** (`ShaderNodeOutputMaterial`) — итог, который рендерится.

Путь материала: `Image/Noise → ... → Principled BSDF → Material Output`.

```python
nt = mat.node_tree
for node in nt.nodes:
    print(node.name, node.bl_idname)   # например "Principled BSDF", "ShaderNodeBsdfPrincipled"
for link in nt.links:
    print(node_link_str(link))
```

У новых материалов Blender сразу создаёт два узла: `Principled BSDF` и
`Material Output`, уже соединённые. Чаще всего мы просто **донастраиваем** Principled.

### Как надёжно найти Principled BSDF

По имени — плохо (может отличаться). Надёжнее — по типу:

```python
def principled_node(mat):
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    # если нет — создадим и подключим к выходу
    bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    out = next(n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf
```

---

## 6.4 Principled BSDF: главные входы

Principled BSDF — универсальный «поверхностный» шейдер: один узел описывает почти
любой непрозрачный материал.

| Вход (Blender 4.x) | Значение | Тип значения |
|---|---|---|
| `Base Color` | основной цвет | RGBA, компоненты 0..1 |
| `Metallic` | металл? (0..1) | float |
| `Roughness` | шероховатость 0 (зеркало) … 1 (мат) | float |
| `IOR` | преломление (по умолчанию 1.45) | float |
| `Alpha` | прозрачность (0 = прозрачно) | float |
| `Normal` | нормали (для карт нормалей) | вектор |
| `Emission Color`, `Emission Strength` | свечение | цвет + число |

⚠️ **Имена сокетов менялись между версиями Blender.** В 3.x было `Specular`,
`Clearcoat`, `Transmission`, `Emission`; в 4.x это `Specular IOR Level`,
`Coat Weight`, `Transmission Weight`, `Emission Color` + `Emission Strength`.
Цвета — это **RGBA** кортеж `(r, g, b, a)` в диапазоне `0.0..1.0` (не 0–255!).

### Устойчивый помощник

Чтобы код не падал при смене версии, пишем функцию, которая пробует несколько имён:

```python
def set_input(node, value, *names):
    """Первый существующий вход из names → value. True, если что-то нашли."""
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return True
    return False
```

Использование — перечисляем и старые, и новые имена:

```python
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
set_input(bsdf, (0.8, 0.15, 0.1, 1.0), "Base Color")
set_input(bsdf, 0.0, "Metallic")
set_input(bsdf, 0.35, "Roughness")
set_input(bsdf, (1.0, 0.8, 0.3, 1.0), "Emission Color", "Emission")   # 3.x/4.x
set_input(bsdf, 3.0, "Emission Strength")
```

---

## 6.5 Рецепт: однотонный материал

🧪 Полный самодостаточный скрипт (как раз то, что должна генерировать модель):

```python
import bpy

def set_input(node, value, *names):
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return True
    return False

obj = bpy.context.active_object
if obj is None or obj.type != "MESH":
    raise RuntimeError("Выдели меш-объект")

mat = bpy.data.materials.get("Matte Red") or bpy.data.materials.new("Matte Red")
mat.use_nodes = True
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
set_input(bsdf, (0.7, 0.08, 0.06, 1.0), "Base Color")
set_input(bsdf, 0.0, "Metallic")
set_input(bsdf, 0.6, "Roughness")

if mat.name not in [m.name for m in obj.data.materials if m]:
    obj.data.materials.clear()
    obj.data.materials.append(mat)
print("Материал", mat.name, "назначен на", obj.name)
```

---

## 6.6 Рецепт: текстура-картинка (image texture)

Картинки живут в `bpy.data.images`. Их можно загрузить с диска или создать в памяти.

🧪 Загрузить файл и натянуть как Base Color:

```python
import bpy

obj = bpy.context.active_object
mat = obj.data.materials[0] if obj.data.materials else bpy.data.materials.new("Tex")
if not obj.data.materials:
    obj.data.materials.append(mat)
mat.use_nodes = True
nt = mat.node_tree

img = bpy.data.images.load(r"C:\path\to\albedo.png")   # 1. загрузить картинку
tex = nt.nodes.new("ShaderNodeTexImage")               # 2. создать узел
tex.image = img                                        # 3. положить картинку
tex.location = (-400, 0)

bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])   # 4. соединить
```

Разбор:

- `bpy.data.images.load(path)` — читает файл и создаёт datablock-изображение. Имя
  будет по имени файла.
- `nodes.new("ShaderNodeTexImage")` — узел текстуры. Позиция `location` — только для
  удобства в редакторе нод.
- `links.new(выход, вход)` — это и есть «провод».

⚠️ **UV-координаты.** Узел текстуры по умолчанию берёт активный UV-слой. Если у меша
нет развёртки (`uv_layers` пуст), текстура «сядет» одинаково на все грани. Развёртку
делают оператором (см. §6.8).

### Правильный цвета пространства (colorspace)

Не все карты несут **цвет** — roughness/metallic/normal — это данные, а не картинка.
Для таких изображений нужно отключить цветовое преобразование sRGB:

```python
rough = bpy.data.images.load(r"C:\path\to\roughness.png")
rough.colorspace_settings.name = "Non-Color"     # ← важно для данных!
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = rough
nt.links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])
```

Забыть про `Non-Color` — классическая ошибка: карта шероховатости «съезжает» по
яркости.

### Упаковка изображения в файл .blend

Если картинка лежит где-то во временной папке, при переносе проекта она потеряется.
`pack()` вшивает её в .blend:

```python
img.pack()
```

Обратная операция — `img.unpack(method='USE_ORIGINAL')`.

---

## 6.7 Рецепт: процедурная текстура (без файлов)

Иногда текстура не нужна как файл — её можно сгенерировать нодами. Узел шума (`ShaderNodeTexNoise`) даёт случайный паттерн, `ColorRamp`
(`ShaderNodeValToRGB`) превращает его в резкий чёрно-белый, а результат идёт в
шероховатость. Соберём целиком:

```python
import bpy

obj = bpy.context.active_object
if obj is None or obj.type != "MESH":
    raise RuntimeError("Выдели меш-объект")

mat = obj.data.materials[0] if obj.data.materials else bpy.data.materials.new("Procedural")
if not obj.data.materials:
    obj.data.materials.append(mat)
mat.use_nodes = True
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")

# Noise (шум) → ColorRamp («резкость») → Roughness
noise = nt.nodes.new("ShaderNodeTexNoise")
noise.location = (-600, 0)
noise.inputs["Scale"].default_value = 12.0

ramp = nt.nodes.new("ShaderNodeValToRGB")   # это и есть ColorRamp
ramp.location = (-350, 0)
ramp.color_ramp.elements[0].position = 0.35
ramp.color_ramp.elements[1].position = 0.65

nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], bsdf.inputs["Roughness"])
```

Что здесь происходит по шагам:

1. `nodes.new("ShaderNodeTexNoise")` создаёт генератор шума; `Scale` задаёт «частоту»
   (больше значение → мельче узор).
2. `nodes.new("ShaderNodeValToRGB")` — это и есть **ColorRamp**. Она берёт число `Fac`
   и по градиенту между позициями `elements[0]` и `elements[1]` делает плавный переход;
   сдвигая эти позиции ближе, получаем более резкую границу.
3. `links.new(выход, вход)` соединяет: `Noise.Fac → Ramp.Fac`, затем
   `Ramp.Color → Roughness`.

> ⚠️ Принцип нодового графа один: **выход одного узла → вход другого**. Магии нет,
> только соединения. `Fac` — это float (число), он хорошо идёт в числовые входы вроде
> Roughness; `Color` — RGBA, он идёт в цветовые входы.

---

## 6.8 Рецепт: UV-развёртка (осторожно, контекст!)

«Смарт-развёртка» — оператор, и он требует **режим Edit** и вьюпорт. Из popup-окна он
не отработает корректно — ровно тот случай, для которого в главе 4 сделан
`temp_override`.

🧪 Прямой запуск (из Text Editor работает, потому что контекст вьюпорта доступен):

```python
import bpy

obj = bpy.context.active_object
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
bpy.ops.object.mode_set(mode="OBJECT")
```

Если этот код придёт от модели и запустится кнопкой из popup — его нужно обернуть в
контекст, как делает аддон:

```python
area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(window=bpy.context.window, area=area, region=region,
                               space_data=area.spaces.active):
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project()
    bpy.ops.object.mode_set(mode="OBJECT")
```

Это прямое продолжение главы 4: вот **зачем** был нужен `_exec_context()`.

---

## 6.9 Несколько материалов на одном объекте

Слоты + `material_index` позволяют покрасить разные грани по-разному.

🧪

```python
import bpy

obj = bpy.context.active_object
mesh = obj.data

red = bpy.data.materials.new("Red");  red.use_nodes = True
blue = bpy.data.materials.new("Blue"); blue.use_nodes = True

mesh.materials.append(red)    # слот 0
mesh.materials.append(blue)   # слот 1

# Все грани с нормалью вверх — синие:
for poly in mesh.polygons:
    poly.material_index = 1 if poly.normal.z > 0.5 else 0
```

Проверить слоты: `obj.material_slots[i].name`, `obj.material_slots[i].material`.

---

## 6.10 Работа с изображениями: создать, закрасить, сохранить

```python
import bpy

# 1. Создать пустое изображение
img = bpy.data.images.new("Generated", width=64, height=64, alpha=True)

# 2. Заполнить пиксели. Формат: ПЛОСКИЙ список RGBA-float построчно,
#    длина = width * height * 4, значения 0.0..1.0
pixels = [0.9, 0.3, 0.2, 1.0] * (64 * 64)
img.pixels = pixels

# 3. Сохранить на диск как PNG
img.filepath_raw = r"C:\path\to\generated.png"
img.file_format = "PNG"
img.save()

# 4. (опционально) вшить в .blend
img.pack()
```

⚠️ `img.pixels` — очень длинный список; для 1024×1024 это ~4 млн чисел, и присваивание
заметно тормозит. Для тяжёлой генерации используют `img.pixels.foreach_set(...)` с
заранее подготовленным буфером (`numpy`). Но `numpy` — уже не «чистый bpy», поэтому в
промптах это отдельная договорённость.

Полезные свойства: `img.size` (кортеж W×H), `img.filepath`, `img.has_data`,
`img.colorspace_settings.name`.

---

## 6.11 Как всё это ляжет в аддон

Ничего нового изобретать не нужно — механизм уже готов:

1. Пользователь пишет запрос в чат (например, «сделай матовый красный материал для
   выделенного объекта и добавь текстуру `albedo.png`»).
2. Модель возвращает ```python-блок с кодом (как в §6.5/6.6).
3. Пользователь нажимает «Выполнить код» → код исполняется через `_exec_script`
   (глава 4): правильный контекст, `view_layer.update()`, один шаг Undo.
4. Сцена меняется; `snapshot.py` при следующем запросе уже покажет новые материалы и
   UV-слои. Теперь он раскрывает материалы подробно (узлы, Base Color / Metallic /
   Roughness, используемые текстуры) и перечисляет изображения — см. главу 7. Так
   модель «видит» свою работу и не плодит дубликаты.

Три бонуса, которые уже есть в интерфейсе:

- **Панель в редакторе шейдеров** (`GAA_PT_shader`) — чат рядом с нодами, удобно для
  материалов.
- **Панель в редакторе изображений** (`GAA_PT_image`) — чат рядом с текстурами.
- **Кнопка «PBR из папки»** — собирает материал из набора карт *без* модели:
  разбирает имена, грузит изображения и расставляет цветовые пространства (глава 10).

📦 `ui.py` уже содержит оба хоста; отдельный код для материалов не требуется — работает
общий механизм «код → запуск». А «PBR из папки» — пример обратного: когда действие
настолько чёткое, что его проще выполнить кодом аддона, чем просить модель
(см. [`10-pbr-from-folder.md`](10-pbr-from-folder.md)).

---

## 6.12 Шпаргалка рецептов

```python
import bpy

# --- материал ---
mat = bpy.data.materials.new("M")
mat.use_nodes = True
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

# задать вход (устойчиво к версии)
def set_input(node, value, *names):
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return True
    return False

set_input(bsdf, (0.8, 0.2, 0.2, 1.0), "Base Color")
set_input(bsdf, 0.2, "Metallic")
set_input(bsdf, 0.5, "Roughness")

# --- назначить объекту ---
obj = bpy.context.active_object
obj.data.materials.append(mat)          # новый слот
# obj.data.materials[0] = mat           # заменить слот 0

# --- картинка ---
img = bpy.data.images.load("C:/tex.png")
img.colorspace_settings.name = "Non-Color"   # для roughness/metallic/normal, не для цвета
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

# --- новая связь между любыми нодами ---
# mat.node_tree.links.new(node_a.outputs["Color"], node_b.inputs["Roughness"])

# --- несколько слотов ---
# obj.data.materials.append(mat2)
# for poly in obj.data.polygons:
#     poly.material_index = 1

# --- картинка из памяти ---
# img = bpy.data.images.new("Gen", 64, 64, alpha=True)
# img.pixels = [0.9, 0.3, 0.2, 1.0] * (64 * 64)
# img.pack()

# --- UV (нужен контекст вьюпорта, см. главу 4) ---
# bpy.ops.object.mode_set(mode="EDIT")
# bpy.ops.mesh.select_all(action="SELECT")
# bpy.ops.uv.smart_project()
# bpy.ops.object.mode_set(mode="OBJECT")
```

---

## 6.13 Что попробовать руками

1. Создай куб, добавь ему материал через §6.5 и поменяй цвет — покрути `Base Color`.
2. Сделай две текстурные карты (§6.6): albedo как `Color`, roughness как `Non-Color`
   → `Roughness`. Сравни с/без `Non-Color`.
3. Разверни объект (§6.8) и убедись, что текстура села правильно.
4. Сделай материал с эмиссией (§6.5) и посмотри, как он светится.

---

## 6.14 Итог главы

- Материал — отдельный datablock; связь с объектом идёт через **слоты**, а грань
  выбирает слот через `material_index`.
- Нодовый граф = узлы + сокеты + связи; путь `... → Principled BSDF → Output`.
- Имена входов Principled меняются между версиями — используй устойчивый `set_input`.
- Картинки — `bpy.data.images`; для не-цветовых карт ставь `Non-Color`.
- Операторы вроде `uv.smart_project()` требуют контекста — снова всплывает
  `temp_override` из главы 4.
- Всё это уже встраивается в аддон через механизм «модель пишет код → ты запускаешь».

На этом учебник заканчивается. Если что-то осталось непонятным — вернись к главе 1
(основы) или главе 2 (карта местности), а за деталями конкретного модуля смотри
соответствующую главу 3–6.
