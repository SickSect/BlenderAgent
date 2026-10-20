# Глава 18. Подтверждение: почему аддон ждёт, а не делает

> 📦 `src/blender_addon/state.py` (`pending_confirmation`, `run_pending_tool`,
> `decline_pending_tool`), `tools.py` (флаг `destructive`),
> `operators.py` (`gaa.tool_confirm`, `gaa.tool_cancel`), `ui.py`,
> `tests/run_confirm_check.py`

Глава 13 объявила правило: **разрушающие инструменты не выполняются
автоматически**. Тогда это выглядело так — модель просит удалить неиспользуемое,
аддон отвечает отказом и велит модели попросить пользователя нажать кнопку.

Работало, но неудобно: человек идёт ищет кнопку, которая не выглядит как
«удалить всё неиспользуемое». Эта глава — про нормальный диалог.

---

## 18.1. Что значит «разрушающий»

Инструмент объявляет это **сам**, один раз, при регистрации:

```python
@tools.tool(
    "materials.cleanup_orphans",
    "Clean up orphans",
    "Delete the materials and images that nothing uses...",
    destructive=True,
)
def cleanup_orphans_tool(**kwargs):
    ...
```

Разрушающих инструментов сейчас два: `materials.cleanup_orphans` (удаляет
данные) и `textures.unpack_all` (пишет файлы на диск и может их перезаписать).

Всё остальное — назначение, замена, упаковка, починка, вариации — **обратимо**
и выполняется без вопросов.

⚠️ Разрушающий — это не «меняет сцену». Упаковка меняет `.blend` и увеличивает
его, но откатывается одним `Ctrl+Z`. Удаление не откатывается.

---

## 18.2. Состояние: ход остаётся открытым

Ключевое решение: **ход не закрывается, пока пользователь не решил**.

```python
if item.destructive:
    self.pending_confirmation = {
        "id": name,
        "params": dict(params),
        "title": item.title,
        "description": item.description,
    }
    set_last_display(i18n.T("msg.tool_confirm", name=item.title))
    return True          # ← ход остаётся busy, модель не переспрашивается
```

Почему не закрывать: иначе модель получит отказ, напишет пользователю
«нажмите кнопку» и завершит ход. Пользователь нажмёт кнопку — но **отчёт
инструмента уже никто не увидит**, и модель не сможет продолжить разговор.

А так:

```
модель: «сейчас почищу неиспользуемое»
аддон:  ждёт подтверждения
пользователь: [Выполнить]
аддон:  выполняет инструмент → отчёт → переспрашивает модель
модель: «готово, удалил 3 материала»
```

Пока пользователь думает, воркер простаивает, а ход остаётся `busy`. Кнопка
**«Стоп»** работает и здесь — можно отменить ожидание.

---

## 18.3. Две кнопки

```python
class GAA_OT_tool_confirm(bpy.types.Operator):
    bl_idname = "gaa.tool_confirm"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        chat = state.get_state()
        result = chat.run_pending_tool()
        state.add_display("result", result["report"])
        chat._ask_again()      # модель продолжает с результатом
        return {"FINISHED"}
```

```python
class GAA_OT_tool_cancel(bpy.types.Operator):
    bl_idname = "gaa.tool_cancel"

    def execute(self, context):
        chat = state.get_state()
        result = chat.decline_pending_tool()
        chat._ask_again()
        return {"FINISHED"}
```

**Отмена — не ошибка.** Модели сообщается, что пользователь отказался, и
запрещается выполнять инструмент ещё раз:

```python
report = i18n.T("msg.tool_declined", name=pending["title"])
# "The user declined the tool '{name}'. Do not run it; tell the user that it
#  was not done and what to do instead."
```

Иначе модель, получив отказ, могла бы попросить тот же инструмент снова и
зациклиться.

---

## 18.4. Как это выглядит

В панели чата появляется блок:

```
┌───────────────────────────────────────────┐
│ ⚠ Подтвердите инструмент «Clean up orphans» │
│ Delete the materials and images that       │
│ nothing uses...                           │
│ [✓ Выполнить]  [✕ Отмена]                │
└───────────────────────────────────────────┘
```

Кнопки — обычные операторы, значит работают и в сайдбаре, и во всплывающем
окне чата.

---

## 18.5. Как это проверяется

📦 `tests/run_confirm_check.py` — весь цикл на **синтетических событиях**,
без модели:

```
== the add-on waits instead of running
ok   the turn stays open, waiting for the user
ok   a confirmation is pending
ok   nothing was executed yet
ok   the model was not asked again while we wait
== the user confirms
ok   the orphan material is really gone
ok   the model was asked again
ok   the model is told the tool has already run
== the user declines
ok   nothing was deleted
ok   the model is told the user declined
== a safe tool runs without asking
ok   no confirmation was asked for
ok   the tool ran right away
RESULT: OK
```

Модель тут не нужна: события `chunk`/`done` подставляются руками, как это уже
делалось для цикла скиллов (глава 11).

⚠️ Проверка «the model was not asked again while we wait» — самая важная.
Именно она отличает «ждём пользователя» от «отказали и забыли».

---

## 18.6. Почему диалог, а не модальное окно

В Blender есть готовый механизм подтверждений — `invoke_confirm`, который
показывает системный попап. Почему здесь нарисован блок в панели, а не попап?

| | `invoke_confirm` | Блок в панели |
|---|---|---|
| Где появляется | поверх всего, в центре экрана | в той же панели, где идёт разговор |
| Что видно | только текст вопроса | вопрос **и** весь контекст разговора |
| Прерывает работу | да, модально | нет — можно дочитать переписку и потом решить |
| Работает в popup-окне чата | неудобно | удобно |

Для аддона, у которого весь смысл в диалоге, блок в панели логичнее: человек
видит, **о чём** речь, и **что** именно будет удалено.

---

## 18.7. Итог главы

- Разрушающий инструмент **объявляет себя сам** — один флаг в реестре.
- Аддон **не выполняет** его и **не отказывает**: он **ждёт**.
- Ход остаётся открытым, модель не переспрашивается зря и получает отчёт
  после подтверждения.
- Отмена — это **ответ**, а не ошибка: модель знает, что инструмент не
  выполнен, и рассказывает об этом пользователю.
- Блок подтверждения живёт **в панели чата**: контекст разговора виден
  вместе с вопросом.

На этом планируемая линия «материалы и текстуры» закрыта. План — в
[`12-materials-roadmap.md`](12-materials-roadmap.md).
