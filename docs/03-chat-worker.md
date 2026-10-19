# Глава 3. Чат и воркер: как аддон общается с моделью

Это, наверное, самая «неочевидная» часть проекта. На первый взгляд кажется, что
можно просто сделать HTTP-запрос из Blender. Но нельзя — и сейчас мы разберём
почему, и как это обошли.

## 3.1 Главная проблема: нельзя блокировать и нельзя потоки

Два ограничения Blender, которые вместе создают задачу:

1. **Главный поток неделим.** Пока выполняется Python, интерфейс не перерисовывается.
   Запрос к модели идёт секунды → Blender «зависнет» и будет выглядеть сломанным.
2. **Фоновые потоки запрещены.** В документации Blender API есть раздел
   «Python Threads are Not Supported»: поток, который живёт дольше скрипта,
   приводит к падениям в C-коде отрисовки. Это не «не рекомендуется», это баг.

Вывод: сетевой запрос должен идти **в другом процессе**, а обмен — через нечто,
что можно опрашивать короткими шагами из главного потока. Этим «нечто» стали
**два текстовых файла**.

```
Blender add-on  --commands.jsonl-->  sidecar worker  --HTTP-->  Ollama / OpenAI API
   (bpy.app.timers)  <--events.jsonl---  (отдельный процесс)
```

Почему именно файлы, а не сокет/pipe? Потому что файлы:

- тривиально отлаживаются (можно открыть и посмотреть `%TEMP%\gaa_*\events.jsonl`);
- не требуют сложной синхронизации — только «читаю с того места, где закончил»;
- переживают кратковременные паузы и перезапуски читателя;
- работают одинаково на Windows, Linux и macOS без платформенного кода.

---

## 3.2 Протокол: по одному JSON на строку

Формат называется **JSONL** (JSON Lines) или **NDJSON** (newline-delimited JSON).
Одна строка = один объект. Никаких отступов и переводов строк внутри объекта.

```
commands (аддон → воркер):
  {"type": "chat", "config": {...}, "messages": [...]}
  {"type": "ping"}
  {"type": "shutdown"}

events (воркер → аддон):
  {"type": "chunk", "text": "кусочек ответа"}
  {"type": "done"}
  {"type": "error", "message": "..."}
  {"type": "pong"}
  {"type": "bye"}
```

📦 Определение протокола — в шапке `src/asset_core/worker.py`, реализация чтения
и записи — там же.

⚠️ Почему не один общий файл на оба направления? Потому что один файл — это ещё и
точка синхронизации (кто на каком месте). Два раздельных файла = два независимых
курсора, проще и надёжнее.

---

## 3.3 Курсор чтения: как читать «только новое»

Основа всего — маленький класс-читатель. Он помнит **байтовое смещение** и умеет
дочитывать только хвост файла. Упрощённо (реальный код — `worker.py:JsonlReader`
и `state.py:ChatState._read_events`):

```python
def read_new(path, offset, pending):
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    offset += len(data)
    pending += data
    lines = pending.split(b"\n")
    pending = lines.pop()          # ← последняя часть может быть недописана!
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line.decode("utf-8", "replace")))
        except json.JSONDecodeError:
            continue               # битая строка не должна всё убивать
    return out, offset, pending
```

Разберём три неочевидные детали:

**1. `pending = lines.pop()`.** Писатель может быть «в середине» строки: он записал
`{"type":"chu` и ещё не дописал `nk",...}`. Если распарсить это сейчас — ошибка.
Поэтому последний (не завершённый `\n`) фрагмент **откладывается** до следующего
чтения, когда строка достроится. Это и есть весь фокус корректного «tail»-чтения.

**2. `open(..., "rb")` и `decode("utf-8", "replace")`.** Читаем байты, потому что
`offset` — это смещение **в байтах**, а не в символах. Байте не всегда совпадает с
символом (русский текст в UTF-8 занимает 2 байта). Декодируем с `"replace"`, чтобы
даже обрезанный многобайтный символ не выбросил исключение.

**3. `except json.JSONDecodeError: continue`.** Если процесс-писатель убили
посреди строки, хвост останется мусором. Мы не роняем чат из-за этого, а просто
пропускаем битую строку.

📦 В `state.py` та же логика, но с полями `self._events_offset` и
`self._events_pending` — состояние живёт в объекте `ChatState`.

---

## 3.4 `ChatState` — состояние сессии

📦 `src/blender_addon/state.py`. Внизу файла есть:

```python
_state = ChatState()          # единственный экземпляр на процесс

def get_state() -> ChatState:
    return _state
```

Это **синглтон**: один объект на весь Blender. Он существует с момента импорта
модуля и до закрытия программы. Три вида его полей:

```python
class ChatState:
    # разговор для модели
    history: List[dict]        # [{"role": "user"/"assistant", "content": "..."}]
    busy: bool                 # идёт генерация?
    stream_buf: str            # накапливаем стриминг здесь
    last_error: str | None

    # мост к воркеру
    proc: subprocess.Popen | None
    tmpdir: Path
    events_path / commands_path / log_path
    _events_offset, _events_pending     # курсор чтения

    # результат для кнопки "Выполнить код"
    pending_code: str
    code_ran: bool
```

⚠️ `ChatState` — **синглтон-объект уровня процесса**, а не данные Blender. Он
живёт, пока запущен Blender, независимо от того, какой .blend открыт.

---

## 3.5 Запуск воркера (`ensure_worker`)

Ключевое слово — **лениво**: воркер стартует не при включении аддона, а при первом
нажатии «Отправить». Значит, если вы просто листаете сцену, лишний процесс не
висит.

```python
def ensure_worker(self) -> bool:
    if self.alive():
        return True                       # уже есть живой — переиспользуем

    script = _worker_script()             # где лежит asset_core/worker.py?
    if script is None:
        self.last_error = i18n.T("err.no_worker")
        return False

    python = find_python()                # какой Python запускать
    if python is None:
        self.last_error = i18n.T("err.no_python")
        return False

    self.tmpdir = Path(tempfile.mkdtemp(prefix="gaa_"))
    self.events_path = self.tmpdir / "events.jsonl"
    self.commands_path = self.tmpdir / "commands.jsonl"
    self.log_path = self.tmpdir / "worker.log"
    self.events_path.touch()
    self.commands_path.touch()

    self.proc = subprocess.Popen(
        [python, str(script), str(self.events_path),
         str(self.commands_path), str(os.getpid())],   # ← os.getpid() родителя
        cwd=str(self.tmpdir),                             # ← cwd = temp, не аддон
        creationflags=CREATE_NO_WINDOW,
    )
    ensure_timer()
    return True
```

Четыре момента, которые стоит понять:

- **`find_python()`** сначала берёт Python, встроенный в Blender
  (`sysconfig.get_config_var("BINDIR")`), — там точно версия и он всегда рядом.
  Если не нашёл, ищет `python3`/`python` в `PATH` и проверяет, что версии ≥ 3.9
  (запускает probe-процесс `python -c "..."`).
- **`cwd=str(self.tmpdir)`.** Воркер запускается в своём временном каталоге, а не в
  каталоге аддона. Об этом ниже — это была реальная причина бага.
- **`str(os.getpid())`** — 4-й аргумент. Воркер по нему узнаёт pid родителя, чтобы
  уметь умирать вместе с ним (см. §3.7).
- **`creationflags=CREATE_NO_WINDOW`** — на Windows консольное окно не мигает.

📦 Полные версии: `state.py`, `find_python()`.

### Почему `cwd` — это важно

Каталог аддона — это каталог **установленного расширения**. Пока процесс работает
«внутри» него (то есть каталог является его рабочим), Windows не даёт удалить или
перезаписать этот каталог. Если Blender аварийно падал, воркер оставался жить,
держал каталог — и следующая установка падала с `[WinError 5] Access is denied`.

Два независимых лекарства:

1. воркер запускается с `cwd = temp-каталог`, значит каталог аддона свободен;
2. у воркера есть **сторож родителя** (§3.7), который убивает его, если Blender
   умер.

---

## 3.6 Таймер: расписание «проверь файл»

Мы уже знаем из главы 1, что вместо потоков Blender даёт «таймер». Здесь он
используется как **насос событий**: 10 раз в секунду заглянуть в `events.jsonl`.

📦 `state.py`:

```python
EVENT_POLL_INTERVAL = 0.1     # секунд между тиками

def ensure_timer() -> None:
    if bpy.app.timers.is_registered(_timer_cb):
        return
    bpy.app.timers.register(
        _timer_cb, first_interval=EVENT_POLL_INTERVAL, persistent=True
    )

def _timer_cb():
    state = get_state()
    if state.proc is None:
        return None           # воркера нет → вернуть None = снять таймер
    try:
        state.pump()
    except Exception as exc:
        print(f"[AssetAssistant] pump error: {exc}")
    return EVENT_POLL_INTERVAL  # иначе — приди снова через 0.1 с
```

Разбор:

- **`is_registered`** — защита от двойной регистрации (одна и та же функция не
  должна висеть в списке дважды).
- **`first_interval`** — когда вызвать в первый раз (через 0.1 с).
- **`return <число>`** — в каком интервале звать снова; **`return None`** —
  остановить таймер. Здесь: пока воркер жив, тикаем; как только `proc is None` —
  останавливаемся. При следующем Send таймер зарегистрируется снова
  (`ensure_timer()` вызывается в `send` и в `ensure_worker`).
- **`persistent=True`** — таймер переживает загрузку нового .blend-файла.
- **`try/except` вокруг `pump()`** — исключение внутри таймера Blender не покажет
  красиво; поэтому ловим и печатаем, но таймер продолжаем.

⚠️ Таймер выполняется в **главном потоке** — это принципиально. Только поэтому
`pump()` может трогать RNA-свойства (`wm.gaa_messages`) и вызывать `tag_redraw()`.
Из фонового потока так нельзя.

---

## 3.7 `pump()` — сердце цикла

```python
def pump(self) -> None:
    if self.proc is None:
        return
    events = self._read_events()          # дочитать новые строки
    for event in events:
        self._handle(event)               # разложить по состоянию

    code = self.proc.poll()               # процесс жив?
    exited = code is not None
    if exited:
        self.proc = None
        if self.busy:                     # ждали ответ — значит, это сбой
            self._fail(i18n.T("err.worker_died", code=code, log=self.log_path))

    if events or exited:                  # перерисовать только если что-то было
        _redraw()
```

`_handle` — маленький диспетчер по типу события:

```python
def _handle(self, event: dict) -> None:
    kind = event.get("type")
    if kind == "chunk":
        self.stream_buf += event.get("text", "")
        set_last_display(self.stream_buf)         # обновляем последнее сообщение
    elif kind == "done":
        if self.stream_buf:
            self.history.append({"role": "assistant", "content": self.stream_buf})
            self.pending_code = extract_last_code(self.stream_buf)  # найти ```python
            self.code_ran = False                 # заново «заряжаем» кнопку
        else:
            set_last_display(i18n.T("msg.empty_reply"))
            drop_last_display_if_empty()
        self.busy = False
        self.stream_buf = ""
    elif kind == "error":
        self._fail(event.get("message") or i18n.T("err.unknown"))
```

Обрати внимание: **`chunk` не создаёт новое сообщение**, он **дописывает** уже
созданный пустой «assistant»-элемент (его создал `send()` заранее). Для этого
`add_display`/`set_last_display` работают с последним элементом списка. Это и даёт
эффект «печатающейся» строки.

### Немного про экономию

```python
if events or exited:
    _redraw()
```

Перерисовка интерфейса — дорогая операция. Таймер тикает 10 раз в секунду; если
перерисовывать на **каждом** тике, Blender будет постоянно перерисовывать окно в
простое. Поэтому перерисовываем только когда реально пришли события или процесс
завершился. Это классическая оптимизация «не буди систему без нужды».

`_redraw()` помечает и **области**, и **регионы** (`region.tag_redraw()`), потому
что всплывающее popup-окно живёт в отдельном регионе, который `area.tag_redraw()`
сам по себе иногда не перерисовывает — иначе текст «замирал», пока не двинешь
мышью.

---

## 3.8 История и бюджет контекста

Модель не бесконечна: отправлять всю историю нельзя (и дорого, и не влезет).
Поэтому есть **бюджет символов**.

📦 `state.py:build_messages`:

```python
def build_messages(history, system_prompt, max_chars=DEFAULT_CONTEXT_CHARS):
    picked = []
    total = len(system_prompt)
    for message in reversed(history):              # идём от новых к старым
        size = len(message.get("content", "")) + 40
        if len(picked) >= 2 and total + size > max_chars:
            break                                  # дальше уже не влезает
        picked.append(message)
        total += size
    return [{"role": "system", "content": system_prompt}, *reversed(picked)]
```

Идея: всегда берём системный промпт (он важный) и жадно набираем **самые свежие**
сообщения, пока не упрёмся в лимит. `len(picked) >= 2` — гарантия, что хотя бы пара
сообщений попадёт даже при крошечном бюджете.

Докстринг честно предупреждает: это «зеркало» функции из `asset_core/session.py`,
продублированное специально, чтобы Blender-процессу **не приходилось импортировать
`asset_core`** только ради одной функции. Дублирование здесь дешевле лишней
зависимости.

---

## 3.9 Сторож родителя: умирать вместе с Blender

Проблема: если Blender аварийно упал, `unregister()` не вызовется, и воркер останется
жить вечно. Решение — в самом воркере, при старте 4-м аргументом приходит pid
родителя.

📦 `asset_core/worker.py`:

```python
def _start_parent_watchdog(parent_pid: int) -> None:
    def _wait() -> None:
        if sys.platform == "win32":
            import ctypes
            SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
            if handle:
                kernel32.WaitForSingleObject(handle, INFINITE)  # спать, пока жив
                os._exit(0)                                    # родитель умер
                return
        while True:                                            # фолбэк для POSIX
            try:
                os.kill(parent_pid, 0)      # проверка «процесс существует»
            except OSError:
                os._exit(0)                 # нет процесса → выходим
            time.sleep(1.0)

    threading.Thread(target=_wait, daemon=True).start()
```

Здесь **поток разрешён**, потому что это отдельный процесс-воркер, а не Blender. В
самом воркере ограничений Blender нет.

- На **Windows** используется `OpenProcess` + `WaitForSingleObject`: поток засыпает
  внутри ядра, пока родительский процесс жив, и мгновенно просыпается на его
  смерть. Ноль опроса, ноль нагрузки.
- На **POSIX** — простой цикл `os.kill(pid, 0)` раз в секунду.
- `daemon=True` — поток не мешает процессу завершиться.
- `os._exit(0)` (а не `sys.exit`) — жёсткий выход прямо из потока, минуя `finally`
  и сборку мусора, потому что нам важно умереть немедленно.

---

## 3.10 Транспорт: как воркер говорит с моделью

📦 `src/asset_core/transport.py`. Файл сознательно написан **только на стандартной
библиотеке** (`urllib`), чтобы гарантированно работать в Python из комплекта
Blender, без `pip install`.

Обе реализации — **генераторы**:

```python
def stream_chat(config, messages):
    if config.kind == "openai":
        yield from _openai_stream(config, messages)
    else:
        yield from _ollama_stream(config, messages)
```

Генератор отдаёт кусочки текста **по мере поступления**, ещё до конца ответа. Именно
поэтому в чате виден стриминг: воркер получает кусочек → пишет `chunk` в
`events.jsonl` → таймер читает → экран обновляется.

Разные форматы потока у двух провайдеров — и оба разбираются **чистыми функциями**
(без сети), поэтому их легко тестировать:

| Провайдер | Запрос | Формат потока | Разбор |
|---|---|---|---|
| Ollama | `POST /api/chat` | NDJSON: по одному JSON на строку | `parse_ollama_lines` |
| OpenAI | `POST /chat/completions` | SSE: строки, начинающиеся с `data:`, в конце `data: [DONE]` | `parse_sse_lines` |

```python
def parse_sse_lines(lines):
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        data = raw[len("data:"):].strip()
        if data == "[DONE]":
            return
        obj = json.loads(data)
        if obj.get("error"):
            raise TransportError(str(obj["error"]))
        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
        if delta.get("content"):
            yield delta["content"]
```

Обрати внимание: `BackendConfig.from_dict` **игнорирует неизвестные ключи** — это
страховка совместимости: если аддон пошлёт новый ключ, а воркер старый, ничего не
сломается.

---

## 3.11 Обработка ошибок и «воркер умер»

Есть три уровня сбоя, и все они видны пользователю:

1. **Ошибка транспорта** (нет сети, HTTP 500, таймаут). `transport.py` ловит
   `HTTPError`/`URLError`/`TimeoutError` и бросает `TransportError` с понятным
   текстом (и на нужном языке — в конфиг передаётся `lang`).
2. **Ошибка в воркере.** `worker.py:handle_chat` оборачивает всё в `try/except` и
   пишет `{"type":"error","message":...}`. Воркер никогда не умирает молча.
3. **Воркер упал целиком.** `pump()` замечает `proc.poll() is not None` и, если
   ещё ждали ответ, зовёт `_fail(...)` с текстом `err.worker_died` и **путём к
   `worker.log`**, куда перенаправлен stderr воркера (см. `_stderr` в
   `ensure_worker`).

Ошибка никогда не теряется: `_fail()` пишет и в `last_error` (для статусной
строки), и отдельным сообщением в чат, и перестаёт быть `busy`.

---

## 3.12 Что попробовать руками

1. **Посмотреть протокол вживую.** Отправь сообщение и, пока идёт генерация, открой
   в проводнике `%TEMP%` → папку `gaa_*` → `events.jsonl` блокнотом. Ты увидишь
   ровно те строки, что разбирались выше.
2. **Остановить воркер.** Нажми «Стоп» — он вызовет `kill_worker()` (терминация с
   таймаутом, затем `kill`).
3. **Проверить сторожа.** Запусти Blender, отправь запрос, затем **убей Blender
   через диспетчер задач** (жёстко). Через секунду процесс `python.exe` с
   `worker.py` должен исчезнуть сам.

---

## 3.13 Итог главы

- Blender нельзя блокировать и нельзя использовать долгоживущие потоки → обмен
  вынесен во внешний процесс через JSONL-файлы.
- Чтение файлов — по **байтовому смещению**, с буфером недописанного хвоста,
  устойчивое к битым строкам.
- Таймер (`bpy.app.timers`) — это «насос» в главном потоке; он же — единственное
  безопасное место, где можно трогать RNA и перерисовку.
- Перерисовка гейтится (только когда есть события) и помечает не только `area`,
  но и `region`.
- Воркер не остаётся сиротой благодаря сторожу по pid родителя, а каталог аддона
  больше не «залипает», потому что `cwd` воркера — временная папка.

Дальше — самое рискованное: выполнение кода, который прислала модель (глава 4).
