# Глава 10. PBR-материал из папки: изображения, цвета, ноды и файловый браузер

Глава 9 закончилась блоком пресетов. Тогда кнопки лишь **просили** ассистента
сделать материал. Эта глава — про кнопку, которая делает материал **сама**, без
модели: «PBR из папки».

Ты указываешь папку с текстурами (`albedo`, `normal`, `roughness`, `metallic`,
`ORM`, `emission`, `height`) — аддон сам разбирает имена файлов, грузит
изображения, выставляет цветовые пространства и собирает нодовое дерево
Principled BSDF.

```
┌ Пресеты материалов ────────────┐
│ [ Матовый ]      [ Металл ]    │
│ [ Стекло ]       [ Эмиссия ]   │
│ [ PBR ]                        │
│ [ PBR из папки ]               │
└────────────────────────────────┘
```

📦 `materials.py` (вся логика), `operators.py` (`GAA_OT_pbr_from_folder`),
`ui.py` (кнопка), `i18n.py` (`op.pbr_folder.*`, `msg.pbr_*`, `pbr.map.*`).

---

## 10.1 Главный принцип главы: UI отдельно, логика отдельно

Весь фокус — в одной строчке:

> Кнопка (оператор) **не делает работу сама**. Она только выбирает папку и
> вызывает три обычные функции.

📦 `operators.py`:

```python
def execute(self, context):
    maps = materials.find_maps(self.filepath)        # 1. имена  -> роли
    if not maps:
        self.report({"WARNING"}, i18n.T("msg.pbr_empty"))
        return {"CANCELLED"}
    images = materials.load_images(maps)             # 2. роли   -> изображения
    material = materials.build_material(             # 3. изображения -> ноды
        materials.material_name_for(self.filepath), images
    )
    assigned = _assign_material(context, material)   # 4. материал -> объект
```

Три шага — три независимые функции в `materials.py`, и ни одна из них не знает
про файловый браузер, кнопки и переводы. Почему так:

- **Тестируемость.** В `--background` файловый браузер открыть нельзя (там нет
  окон), а вот `find_maps`/`load_images`/`build_material` — можно вызвать прямо
  в тестовом скрипте с обычной папкой. Именно так эта фича и проверялась.
- **Переиспользование.** Тот же `build_material` можно звать и из кода, который
  выполнил ассистент (глава 4), и из будущей кнопки «пересобрать материал».
- **Читаемость.** Оператор остаётся тонким: 4 строки, которые видно насквозь.

⚠️ **Правило:** если логика в `execute` разрастается «в ширину», это сигнал
вынести её в функцию/модуль без `bpy.ops` и без UI. UI-слой — тонкая скорлупа.

---

## 10.2 Шаг 1: имя файла → роль карты

Текстуры в папках называют как угодно: `T_Wood_BaseColor_2k.png`,
`Wood_roughness.png`, `M_Rock_disp.png`, `wood_n.png`. Нужна эвристика.

Сплитим имя на «токены» — куски из букв и цифр:

📦 `materials.py`:

```python
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

def _tokens(name: str) -> list:
    stem = os.path.splitext(name)[0].lower()       # убираем ".png", приводим к нижнему
    return [t for t in _TOKEN_SPLIT.split(stem) if t]
```

`T_Wood_BaseColor_2k` → `["t", "wood", "basecolor", "2k"]`. Теперь роль ищется по
**токенам**, а не по подстроке — поэтому `basecolor` находится как единое слово, а
`Form.png` → `["form"]` **не** ложно-срабатывает на `orm`.

Таблица правил (порядок важен — первое совпадение выигрывает):

```python
MAP_RULES = (
    ("normal", ("normal", "norm", "nrm", "nor")),
    ("base",   ("basecolor", "base", "albedo", "alb", "diffuse", "diff", "dif", "col", "color", "bc")),
    ("rough",  ("roughness", "rough", "rgh", "rghness")),
    ("metal",  ("metallic", "metalness", "metal", "mtl", "met")),
    ("orm",    ("orm", "rma", "mra")),
    ("emit",   ("emission", "emissive", "emiss", "emit")),
    ("height", ("height", "displacement", "disp", "hgt")),
)
```

### Однобуквенные маркеры — только в конце

Многие паки пишут коротко: `wood_d` (diffuse), `wood_n` (normal), `wood_r`,
`wood_m`, `wood_h`, `wood_e`. Соблазнительно добавить `"d"`, `"n"`, `"m"` в те же
правила. Но настоящий тест поймал баг:

```
M_Rock_disp.png  ->  metal      ← неверно!
```

Токен `m` стоял **в начале** (означает «материал/меш», распространённый префикс), а
мы приняли его за `metallic`. Исправление — однобуквенные маркеры ищем **только
последним токеном**:

```python
SUFFIX_RULES = {"d": "base", "n": "normal", "r": "rough", "m": "metal", "h": "height", "e": "emit"}

def detect_role(filename):
    tokens = _tokens(filename)
    if not tokens:
        return None
    present = set(tokens)
    for role, keywords in MAP_RULES:
        if present & set(keywords):     # длинные токены — в любом месте
            return role
    return SUFFIX_RULES.get(tokens[-1])  # короткие — только в хвосте имени
```

Проверь по таблице:

| Файл | Токены | Роль |
|---|---|---|
| `T_Wood_BaseColor.png` | t, wood, basecolor | `base` |
| `Form.png` | form | — (нет) |
| `wood_d.png` | wood, **d** | `base` (хвост) |
| `M_Rock_disp.png` | m, rock, **disp** | `height` (хвост `disp`, не `m`) |
| `T_Wood_ORM.png` | t, wood, **orm** | `orm` |

📌 **Урок:** любая эвристика по именам — это **договор с реальностью**, и её надо
проверять на «неудобных» примерах. Здесь один тест (`M_Rock_disp`) спас от тихо
неправильного материала.

`find_maps` просто обходит папку по алфавиту и берёт **первое** совпадение на роль —
это делает поведение предсказуемым при нескольких кандидатах.

---

## 10.3 Шаг 2: изображение — это datablock, а не «файл на диске»

Загрузка одной строкой:

📦 `materials.py`:

```python
image = bpy.data.images.load(path, check_existing=True)
```

- `bpy.data.images` — та же коллекция датаблоков, что разбиралась в главе 6 (там мы
  *создавали* изображение в памяти; здесь **загружаем из файла**).
- `check_existing=True` — если это изображение уже загружено, вернуть **существующее**,
  а не создавать второй датаблок на тот же файл. Без этого получишь
  `albedo.png` и `albedo.png.001` — и в панели, и в снимке сцены (глава 7).

### Цветовое пространство — самое важное здесь

```python
image.colorspace_settings.name = MAP_COLORSPACE.get(role, "sRGB")
```

Это ровно тот момент, про который была заметка в главе 6 (§6.6), но теперь он
критичен, потому что происходит **автоматически** и незаметно. Суть:

| Что за карта | Как читать | Почему |
|---|---|---|
| albedo, emission | `sRGB` | это **цвет**; в файле он хранится с гаммой, Blender должен «разогнуть» его в линейное пространство |
| normal, roughness, metallic, height, ORM | `Non-Color` | это **данные** (числа), а не цвет; их нельзя пропускать через гамму |

Если normal map прочитать как `sRGB`, значения «разогнутся» (`0.5` станет не
`0.5`) — освещение и рельеф поедут, и это не бросается в глаза, пока не сравнишь с
референсом. Поэтому:

```python
MAP_COLORSPACE = {
    "base": "sRGB", "emit": "sRGB",
    "normal": "Non-Color", "rough": "Non-Color", "metal": "Non-Color",
    "orm": "Non-Color", "height": "Non-Color",
}
```

Присвоение обёрнуто в `try/except`: некоторые источники изображений (например,
сгенерированные или «Render Result») не дают менять цветовое пространство — это не
ошибка, из-за которой стоит падать.

💡 Проверь руками в Text Editor: загрузи любую текстуру и напечатай
`img.colorspace_settings.name` до и после присваивания. А доступные значения
видно через `img.colorspace_settings.bl_rna.properties["name"].enum_items`.

---

### Как называется материал

```python
def material_name_for(directory):
    name = os.path.basename(os.path.normpath(directory))
    return name or "PBR Material"
```

`normpath` убирает хвостовой слэш, `basename` берёт последнюю папку. Если такая
папка — `.../Wood_2k`, материал получит имя `Wood_2k`. Если имя вдруг уже занято,
Blender сам сделает `Wood_2k.001` — это его штатное поведение, ломать его не надо.

---

## 10.4 Шаг 3: нодовый граф — «роль → сокет»

Вспомни механику из главы 6: у материала есть `node_tree`, в ней `nodes` и `links`.
Ноды создаются как **классы-узлы** (`ShaderNode*`), а связь прокладывается так:

```python
tree.links.new(source.outputs["Color"], target.inputs["Roughness"])
```

📦 `materials.py`, `build_material` создаёт материал, находит (или создаёт)
Principled BSDF и Material Output, а затем для каждой карты рисует
`ShaderNodeTexImage`:

```python
x, y = -900, 300
for role, image in images.items():
    texture = _new_node(nodes, "ShaderNodeTexImage", (x, y))
    texture.image = image
    _connect(nodes, links, bsdf, output, role, texture, (x + 300, y))
    y -= 320                       # каждый следующий узел — ниже
```

`location` — чистая косметика: это координаты узла в редакторе, чтобы дерево
выглядело колонкой, а не свалкой. На рендер не влияет.

### Таблица подключений

| Роль | Узел-помощник | Куда идёт |
|---|---|---|
| `base` | — | `TEX_IMAGE.Color` → `Principled."Base Color"` |
| `rough` | — | `TEX_IMAGE.Color` → `Principled."Roughness"` |
| `metal` | — | `TEX_IMAGE.Color` → `Principled."Metallic"` |
| `emit` | — | `TEX_IMAGE.Color` → `Principled."Emission Color"` |
| `normal` | `ShaderNodeNormalMap` | карта → `Normal Map.Color`, `Normal Map.Normal` → `Principled."Normal"` |
| `orm` | `ShaderNodeSeparateColor` | `Green` → `Roughness`, `Blue` → `Metallic` |
| `height` | `ShaderNodeDisplacement` | `Height` → `Displacement.Displacement` → `OUTPUT_MATERIAL."Displacement"` |

Разберём «нетривиальные» три.

**Normal Map.** Файл normal — не «цвет картинки», а **вектор нормали в
тангент-пространстве**, закодированный в RGB. Просто сунуть его в `Base Color`
бессмысленно — нужен узел `ShaderNodeNormalMap`, который декодирует вектор и
свяжет его с нормалью шейдера. Поэтому normal всегда идёт через него.

**Separate Color.** Некоторые паки упаковывают три карты в один файл (ORM / RMA /
MRA): `R` = Ambient Occlusion, `G` = Roughness, `B` = Metallic. Чтобы «разложить»
каналы обратно, ставят `ShaderNodeSeparateColor` и берут `Green`/`Blue`.

**Displacement.** Слот `Displacement` есть не у Principled BSDF, а у
**Material Output** (`ShaderNodeOutputMaterial`). Поэтому height-карта идёт в
`ShaderNodeDisplacement`, а его выход — в `output.inputs["Displacement"]`.

### Почему все связи идут через `_link`

```python
def _link(links, source, out_name, target, in_name, *aliases) -> bool:
    out = source.outputs.get(out_name)
    if out is None:
        return False
    inp = target.inputs.get(in_name)
    if inp is None:
        for alias in aliases:            # напр. ("Emission Color", "Emission")
            inp = target.inputs.get(alias)
            if inp is not None:
                break
    if inp is None:
        return False
    links.new(out, inp)
    return True
```

Две вещи:

1. **`get` вместо `[...]`.** Имена сокетов меняются между версиями Blender. Приём
   из главы 6 («искать по имени, а не по индексу») здесь доведён до `get` +
   **алиасы**: сокет эмиссии в одних версиях `"Emission Color"`, в других
   `"Emission"`. Пытаемся по очереди.
2. **Возврат `False`, а не исключение.** Если сокета нет — молча пропускаем узел.
   Неполный материал лучше падения: пользователь откроет ноды и увидит, что не
   подключилось.

---

## 10.5 Шаг 4: назначить материал объекту

📦 `operators.py`:

```python
def _assign_material(context, material) -> str:
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return ""                       # пустышка/света: материала у данных нет
    if len(slots):
        index = getattr(obj, "active_material_index", 0)
        if not 0 <= index < len(slots):
            index = 0
        slots[index] = material         # заменить активный слот
    else:
        slots.append(material)          # добавить первый слот
    return obj.name
```

Здесь важны два факта о Blender:

- материалы хранятся **на данных объекта** (`obj.data.materials`), а не на самом
  объекте. У меша это `mesh.materials`;
- если слоты уже есть — заменяем **активный** (`obj.active_material_index`), а не
  добавляем шестой. Так «перекрасить/перетекстурировать» объект не плодит слоты.

У объектов без `data.materials` (Empty, лампа, камера) материала быть не может —
функция возвращает пустую строку, и оператор просто **не** пишет «назначен…» в
отчёте.

Обрати внимание на проверку `if not 0 <= index < len(slots)`. Мы уже ловили в
главе 8, как легко «проверить не то»; здесь это ещё и защита от того, что индекс
мог «устареть».

---

## 10.6 Файловый браузер: как из оператора выбрать папку

До сих пор все операторы выполнялись сразу по клику. Здесь нужен **диалог**.

📦 `operators.py`:

```python
class GAA_OT_pbr_from_folder(bpy.types.Operator):
    bl_idname = "gaa.pbr_from_folder"
    bl_label = "PBR из папки"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        return context.window_manager.fileselect_add(self)
```

Ключевых моментов три.

**`subtype="DIR_PATH"`.** Blender-у `StringProperty` с этим подтипом показывает
браузер в режиме «выбери папку», а выбранный путь кладёт обратно в `self.filepath`.

**`invoke`, а не `execute`.** У оператора может быть два входа:

- `execute(context)` — «сделать сразу» (так работает, например, кнопка
  «Отправить»);
- `invoke(context, event)` — «спросить пользователя», обычно запуская модальный
  режим. `fileselect_add` открывает браузер и **возвращает управление**: код ниже
  него не выполняется, пока пользователь не подтвердит выбор. Тогда Blender сам
  вызовет `execute` — уже с заполненным `self.filepath`.

⚠️ Именно поэтому нельзя «прочитать `self.filepath` сразу после `fileselect_add`»:
его ещё нет. Порядок всегда: `invoke` → (пользователь) → `execute`.

**Почему это не тестируется в `--background`.** Браузера там нет — окна тоже.
Поэтому вся работа живёт в `materials.py`, а оператор тестируется вызовом с уже
заданным путём, без браузера:

```python
bpy.ops.gaa.pbr_from_folder(filepath=r"C:\textures\Wood_2k")
```

Так делает и наш headless-тест, и так же может сделать ассистент из своего кода.

---

## 10.7 Отчёт и `bl_options = {"REGISTER", "UNDO"}`

Оператор возвращает Blender-у **отчёт** — строку статуса. Для материала мы
собрали её из i18n и списка найденных ролей:

```python
maps_text = ", ".join(i18n.T("pbr.map." + role) for role in materials.ordered_roles(maps))
message = i18n.T("msg.pbr_done", name=material.name, maps=maps_text)
if assigned:
    message += " · " + i18n.T("msg.pbr_assigned", obj=assigned)
self.report({"INFO"}, message)
```

В русском интерфейсе это выглядит так (видно в строке статуса и в консоли):

```
Info: Материал «Grass_2k»: основной цвет, нормаль, шероховатость, металличность · назначен объекту «Sphere»
```

Благодаря `ordered_roles` порядок не зависит от того, в каком порядке `os.listdir`
вернула файлы — «основной цвет» всегда первый.

**`UNDO`** здесь есть, и это правильно: в отличие от пресета из главы 9
(`{"REGISTER"}`), этот оператор **меняет сцену** (создаёт материал и назначает
его). Значит, всё действие должно стать одним шагом `Ctrl+Z`. Правило то же, что
в §9.1: `UNDO` — только для операторов, меняющих данные.

---

## 10.8 Что попробовать руками

1. Собери папку с картами, например `Wood_2k/` с файлами
   `Wood_BaseColor.png`, `Wood_Normal.png`, `Wood_Roughness.png`,
   `Wood_Metallic.png`.
2. Выдели объект → панель `AI` → **PBR из папки** → выбери папку. Материал
   появится на объекте, а в строке статуса — список найденных карт.
3. Открой **Shader Editor** (кнопка из главы 8 или сам) и посмотри дерево: три
   Image Texture, Normal Map, всё связано.
4. Выдели любой Image Texture и взгляни в **Node → Image** (N): у albedo в
   Image Editor цветовое пространство `sRGB`, у остальных — `Non-Color`.
5. Нажми `Ctrl+Z` — материал исчезнет целиком (один шаг отмены).
6. Сломай нарочно: положи в папку только `readme.txt` → статус скажет «В папке не
   нашлось карт», оператор вернёт `CANCELLED` и ничего не создаст.

---

## 10.9 Ограничения и что можно добавить дальше

Это честная первая версия; вот куда очевидно расти:

1. **AO-канал из ORM.** Сейчас `Red` (occlusion) разбирается, но не подключён.
  Логичное продолжение — умножать его на base color через `ShaderNodeMixRGB` (в
  4.x — `ShaderNodeMix`) в режиме `Multiply`.
2. **Свой набор правил.** Сейчас словарь токенов зашит. Со временем можно вынести
   его в настройки аддона, чтобы паки с другими соглашениями не требовали правки
   кода.
3. **Несколько кандидатов на роль.** Сейчас из двух `roughness` выигрывает первый
   по алфавиту. Можно показывать выбор.
4. **Бейкинг.** Обратная задача: собрать текстуры с процедурного материала/модели
   в файлы (Cycles bake). Отдельная большая тема.

---

## 10.10 Шпаргалка терминов

| Термин | Что это |
|---|---|
| **токен** | кусок имени файла между разделителями; по нему определяется роль карты |
| **`bpy.data.images.load(path, check_existing=True)`** | загрузить картинку, не создавая дубликат datablock |
| **`colorspace_settings.name`** | `sRGB` для цвета, `Non-Color` для данных (normal/rough/metal/… ) |
| **`ShaderNodeNormalMap`** | декодирует вектор нормали из картинки в нормаль шейдера |
| **`ShaderNodeSeparateColor`** | разлагает упакованную карту (ORM) на каналы R/G/B |
| **`ShaderNodeDisplacement`** | подключается к слоту `Displacement` **Material Output**, не к Principled |
| **`obj.data.materials` / `active_material_index`** | слоты материалов на данных объекта; активный слот — тот, что заменяем |
| **`fileselect_add`** | открыть файловый браузер из `invoke`; `execute` придёт позже |
| **`subtype="DIR_PATH"`** | свойство-путь, браузер в режиме выбора папки |

---

## 10.11 Итог главы

- Кнопка = тонкий оператор; вся логика — в чистых функциях `materials.py`,
  которые можно гонять без окон и браузера.
- Роли карт выводятся из имён **токенами**; однобуквенные маркеры — только в
  хвосте имени (урок `M_Rock_disp`).
- `bpy.data.images` — изображения как datablocks; `check_existing=True` против дублей.
- **Цветовое пространство:** `sRGB` = цвет, `Non-Color` = данные; ошибка тут тихо
  ломает освещение.
- `ShaderNodeNormalMap` / `SeparateColor` / `Displacement` — декодировать normal,
  разложить ORM, отдать height в слот `Displacement`.
- `filepath: StringProperty(subtype="DIR_PATH")` + `fileselect_add` — выбор папки.
- `UNDO` — ставить, если оператор меняет данные сцены.

Дальше фичу материалов можно продолжать в сторону быстрых действий с нодами,
PBR-наборов из папки и, в перспективе, бейкинга.
