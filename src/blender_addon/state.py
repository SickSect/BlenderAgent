"""Runtime state: history, sidecar worker bridge, timer pump.

No persistent Python threads are used here (Blender does not support them —
see "Python Threads are Not Supported" in the Blender API docs). All work
happens either in the spawned worker process or in short ``bpy.app.timers``
callbacks on the main thread.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import bpy

from . import i18n, skills, tools

ADDON_DIR = Path(__file__).resolve().parent
EVENT_POLL_INTERVAL = 0.1     # seconds between timer ticks
MAX_DISPLAY_CHARS = 8000      # per-message cap for the on-screen copy
DEFAULT_CONTEXT_CHARS = 24000 # model-facing history budget
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ```python\n...\n``` — the last python block of a reply is what the user may
# run with the "Выполнить код" button.
_CODE_BLOCK_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
_CODE_LANGS = ("", "python", "py", "bpy")


def collapse_code(text: str) -> str:
    """The text with its last code block replaced by a one-line marker.

    After the code has run, the block is dead weight in the chat: it takes
    half the panel and it has already been executed. The full text stays in
    ``pending_code``, so it can still be saved to the Text Editor.
    """
    blocks = list(_CODE_BLOCK_RE.finditer(text or ""))
    if not blocks:
        return text
    last = blocks[-1]
    lines = last.group(2).strip().splitlines()
    marker = i18n.T("msg.code_collapsed", n=len(lines))
    return text[: last.start()] + marker + text[last.end():]


def extract_last_code(text: str) -> str:
    """Last fenced ```python block of an assistant reply ("" when none).

    A bare fence (no language) holding nothing but a skill name is skipped:
    that is the model asking for an instruction with the wrong block style, not
    code the user should be offered to run.
    """
    names = {skill.name for skill in skill_library()}
    last = ""
    for lang, body in _CODE_BLOCK_RE.findall(text or ""):
        if lang.lower() not in _CODE_LANGS:
            continue
        if body.strip() in names:
            continue
        last = body
    return last.strip("\n")


def skill_library() -> List[skills.Skill]:
    """Instruction packs that apply to this Blender version.

    Cached inside :mod:`skills`; never raises — a broken skills directory must
    not take the chat down with it.
    """
    try:
        return skills.get_skills(ADDON_DIR / skills.SKILLS_DIRNAME, tuple(bpy.app.version))
    except Exception as exc:  # noqa: BLE001 - a chat must survive bad content
        print(f"[AssetAssistant] skills unavailable: {exc}")
        return []


def build_messages(
    history: List[dict],
    system_prompt: str,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
) -> List[dict]:
    """Mirror of asset_core.session.build_messages (kept local so the
    Blender process does not need to import asset_core at all)."""
    picked: List[dict] = []
    total = len(system_prompt)
    for message in reversed(history):
        size = len(message.get("content", "")) + 40
        if len(picked) >= 2 and total + size > max_chars:
            break
        picked.append(message)
        total += size
    return [{"role": "system", "content": system_prompt}, *reversed(picked)]


def _worker_script() -> Optional[Path]:
    """asset_core/worker.py — vendored inside the add-on, or the repo copy
    when running from source."""
    candidates = [
        ADDON_DIR / "asset_core" / "worker.py",
        ADDON_DIR.parent / "asset_core" / "worker.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _python_candidates() -> List[Path]:
    out: List[Path] = []
    bindir = sysconfig.get_config_var("BINDIR")
    if bindir:
        directory = Path(bindir)
        if sys.platform == "win32":
            out.append(directory / "python.exe")
        else:
            out.extend(sorted(directory.glob("python3*")))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            out.append(Path(found))
    return out


def find_python() -> Optional[str]:
    """Blender's bundled Python first, then anything on PATH (3.9+)."""
    for candidate in _python_candidates():
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run(
                [
                    str(candidate),
                    "-c",
                    "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)",
                ],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return str(candidate)
    return None


def _wm():
    """Window manager that the UI reads from (guarded for timers/context)."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None and len(bpy.data.window_managers):
        wm = bpy.data.window_managers[0]
    return wm


def add_display(role: str, body: str) -> None:
    wm = _wm()
    if wm is None:
        return
    item = wm.gaa_messages.add()
    item.role = role
    item.body = body[:MAX_DISPLAY_CHARS]
    wm.gaa_active = len(wm.gaa_messages) - 1


def set_last_display(body: str) -> None:
    wm = _wm()
    if wm is None or not len(wm.gaa_messages):
        return
    wm.gaa_messages[-1].body = body[:MAX_DISPLAY_CHARS]


def collapse_last_code() -> bool:
    """Hide the code block of the last message behind a one-line marker.

    Returns True when something was collapsed. The code itself is not lost -
    it stays in ``ChatState.pending_code`` and can be written to the Text
    Editor.
    """
    wm = _wm()
    if wm is None or not len(wm.gaa_messages):
        return False
    message = wm.gaa_messages[-1]
    collapsed = collapse_code(message.body)
    if collapsed == message.body:
        return False
    message.body = collapsed[:MAX_DISPLAY_CHARS]
    return True


def drop_last_display_if_empty() -> None:
    wm = _wm()
    if wm is None or not len(wm.gaa_messages):
        return
    if not wm.gaa_messages[-1].body.strip():
        wm.gaa_messages.remove(len(wm.gaa_messages) - 1)


def _redraw() -> None:
    """Tag every area AND region for redraw.

    Tagging regions as well matters for the chat popup window: it lives in a
    temporary region that area.tag_redraw() alone does not always repaint,
    which would freeze the streaming text until the mouse moves.
    """
    wm = _wm()
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            area.tag_redraw()
            for region in area.regions:
                region.tag_redraw()


class ChatState:
    """Process-wide singleton holding history and the worker handle."""

    def __init__(self) -> None:
        self.history: List[dict] = []  # model-facing conversation
        self.busy = False
        self.stream_buf = ""           # assistant text accumulated so far
        self.last_error: Optional[str] = None
        self.pending_code: str = ""    # last ```python block, not run yet
        self.code_ran: bool = False    # True once "Run code" used that block
        self.code_collapsed: bool = False  # the block in the chat is a marker
        self._turn_code: str = ""      # code of an interrupted round
        self.turn_user_text: str = ""  # the words of the current turn

        # Re-sending the same turn after a skill request needs the original
        # prompt and transport settings kept around.
        self.system_prompt: str = ""
        self.last_config: Optional[dict] = None
        self.last_max_chars: int = DEFAULT_CONTEXT_CHARS
        self.skill_texts: List[tuple] = []  # (name, body) loaded for this turn
        self.auto_skills: List[str] = []    # attached without a round trip
        self._fetched_skills = 0             # on-demand loads this turn
        self.tool_results: List[dict] = []   # reports of executed tools
        self.tool_history: List[dict] = []   # every call, including refusals
        self._ran_tools: List[dict] = []     # (key, report) of executed calls
        self._tool_calls = 0                 # tool calls this turn
        self._tool_ok: Dict[str, int] = {}       # successful runs per tool id
        self._tool_fail: Dict[str, int] = {}     # failed runs per tool id
        self._tool_reports: Dict[str, str] = {}   # last report per tool id
        self._tool_alias: Optional[tuple] = None  # (asked, used) of a near miss
        # A destructive tool waiting for the user's click. While it is set the
        # turn stays open: the model asked, the addon waits.
        self.pending_confirmation: Optional[dict] = None

        self.proc: Optional[subprocess.Popen] = None
        self.tmpdir: Optional[Path] = None
        self.events_path: Optional[Path] = None
        self.commands_path: Optional[Path] = None
        self.log_path: Optional[Path] = None
        self._stderr = None
        self._events_offset = 0
        self._events_pending = b""

    # -- worker lifecycle -------------------------------------------------

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure_worker(self) -> bool:
        if self.alive():
            return True

        script = _worker_script()
        if script is None:
            self.last_error = i18n.T("err.no_worker")
            return False
        python = find_python()
        if python is None:
            self.last_error = i18n.T("err.no_python")
            return False

        self.tmpdir = Path(tempfile.mkdtemp(prefix="gaa_"))
        self.events_path = self.tmpdir / "events.jsonl"
        self.commands_path = self.tmpdir / "commands.jsonl"
        self.log_path = self.tmpdir / "worker.log"
        self.events_path.touch()
        self.commands_path.touch()
        self._events_offset = 0
        self._events_pending = b""

        try:
            self._stderr = open(self.log_path, "w", encoding="utf-8")
            self.proc = subprocess.Popen(
                [
                    python,
                    str(script),
                    str(self.events_path),
                    str(self.commands_path),
                    str(os.getpid()),  # lets the worker notice our death
                ],
                # Run from our temp dir, never from the add-on folder: a worker
                # left behind by a Blender crash would otherwise keep the
                # extension directory locked ("Access is denied" on reinstall).
                cwd=str(self.tmpdir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.last_error = i18n.T("err.worker_start", exc=exc)
            self.proc = None
            return False

        self.last_error = None
        ensure_timer()
        return True

    def kill_worker(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.proc = None
        if self._stderr is not None:
            try:
                self._stderr.close()
            except Exception:
                pass
            self._stderr = None
        # A destructive tool that was waiting for a confirmation is dead with
        # the turn: leaving it pending would let the user run it after a stop.
        self.pending_confirmation = None
        self.busy = False
        self.stream_buf = ""
        # The temp folder holds the two JSONL files and the log. The worker is
        # dead, so nothing writes into it any more - and without this the
        # folders pile up in the system temp directory.
        if self.tmpdir is not None:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.tmpdir = None
            self.events_path = None
            self.commands_path = None
            self.log_path = None

    # -- sending ----------------------------------------------------------

    def _auto_skills(self, user_text: str) -> List[skills.Skill]:
        """Skills the user's own words already point at.

        The model can always ask for a skill explicitly; this is the shortcut
        for "сделай стеклянный материал", where the recipe for glass is
        obviously needed and a round trip would only waste time.
        """
        try:
            library = skill_library()
            if not library:
                return []
            return skills.match(library, user_text)
        except Exception as exc:  # noqa: BLE001 - a bad keyword must not block the chat
            print(f"[AssetAssistant] skill matching unavailable: {exc}")
            return []

    def send(
        self,
        config: dict,
        system_prompt: str,
        user_text: str,
        max_chars: int = DEFAULT_CONTEXT_CHARS,
    ) -> bool:
        if self.busy:
            return False
        if not self.ensure_worker():
            return False

        self.history.append({"role": "user", "content": user_text})
        add_display("user", user_text)
        add_display("assistant", "")
        self.stream_buf = ""
        self.busy = True
        self.last_error = None

        # Remember the turn's inputs: a skill request re-sends this same turn
        # with the instruction appended to the system prompt.
        self.system_prompt = system_prompt
        self.last_config = config
        self.last_max_chars = max_chars
        self.skill_texts = []
        self.auto_skills = []
        self._fetched_skills = 0
        # The words of this turn: the add-on uses them to check that a tool
        # did what the user asked.
        self.turn_user_text = user_text
        self.tool_results = []
        self.tool_history = []
        self._ran_tools = []
        self._tool_calls = 0
        self._tool_ok = {}
        self._tool_fail = {}
        self._tool_reports = {}
        self._tool_alias = None
        self.pending_confirmation = None
        self._turn_code = ""
        self.code_collapsed = False

        # Attach the recipes the user's words already point at, so the model
        # starts with the right instructions instead of asking for them.
        for skill in self._auto_skills(user_text):
            self.skill_texts.append((skill.name, skill.body))
            self.auto_skills.append(skill.name)
        if self.auto_skills:
            set_last_display(
                i18n.T("msg.skill_loading", name=", ".join(self.auto_skills))
            )

        messages = build_messages(
            self.history, self._prompt_with_context(), max_chars
        )
        self._write_command({"type": "chat", "config": config, "messages": messages})
        ensure_timer()
        return True

    def _prompt_with_context(self) -> str:
        """System prompt plus everything gathered for this turn.

        Three kinds of context are appended: the instruction packs (skills)
        and the reports of the tools that ran. All of it goes into the
        **system** message, never into the conversation history — the chat
        template has no notion of a "tool result message", and different
        models read the same conversation differently.
        """
        parts = [self.system_prompt]
        for name, body in self.skill_texts:
            parts.append(f"# skill: {name}\n\n{body}")
        for call in self.tool_results:
            parts.append(
                f"# tool result: {call['id']}\n"
                "This tool has ALREADY RUN. Its output is below. "
                "Do not call it again - use this result to answer the user.\n\n"
                f"{call['report']}"
            )
        return "\n\n".join(parts)

    def _requested_skill(self) -> Optional[str]:
        """First skill the reply asked for that exists and is not loaded yet."""
        wanted = skills.parse_skill_requests(self.stream_buf)
        if not wanted:
            return None
        library = skill_library()
        if not library:
            return None
        loaded = {name for name, _ in self.skill_texts}
        for name in wanted:
            if name in loaded:
                continue
            if skills.by_name(library, name) is not None:
                return name
        return None

    def _fetch_skill(self, name: str) -> bool:
        """Pull one instruction in and ask the model again.

        Returns True while a new round trip is running, so the caller keeps the
        session busy. Every fetch costs one extra request — the budget in
        :data:`skills.MAX_SKILL_LOADS` keeps that in check. Skills that were
        attached automatically do not eat this budget: they cost no round trip.
        """
        if self._fetched_skills >= skills.MAX_SKILL_LOADS:
            return False
        if self.last_config is None or self.commands_path is None:
            return False
        skill = skills.by_name(skill_library(), name)
        if skill is None:
            return False

        self.skill_texts.append((skill.name, skill.body))
        self._fetched_skills += 1
        # The model asked for the instruction - it must see its own request
        # and the fact that the instruction is now in the prompt.
        self._remember_turn(self.stream_buf)
        self.history.append(
            {
                "role": "user",
                "content": i18n.T("msg.skill_ready", name=skill.name),
            }
        )
        # The code of this turn stays available, and the text of the turn
        # stays in the chat.
        self._keep_code(self.stream_buf)
        self._ask_again(i18n.T("msg.skill_loading", name=skill.name))
        return True

    def _requested_tool(self):
        """First tool the reply asked for that exists in the registry.

        Returns ``(tool_id, params)`` or ``None``. A name that is not in the
        registry is looked up by similarity: the model asks for
        "material.preset" when the tool is "procedural.preset", and dropping
        the call would leave the user with a promise instead of a material.
        """
        for name, params in tools.parse_request(self.stream_buf):
            if tools.get(name) is not None:
                return name, params
        for name, params in tools.parse_request(self.stream_buf):
            close = tools.closest(name)
            if close:
                self._tool_alias = (name, close[0].id)
                return close[0].id, params
        return None

    def _remember_turn(self, text: str) -> None:
        """Put the model's own reply into the conversation.

        Without this the model is asked the same question again with no memory
        of what it already said: it starts the same answer from scratch and
        asks for the same tool again - the chat looks like it restarts itself.
        """
        said = (text or "").strip()
        if not said:
            return
        self.history.append({"role": "assistant", "content": said})

    def _run_tool(self, name: str, params: dict) -> bool:
        """Run a tool on the main thread and ask the model again.

        Returns True when the turn is handled: either a new round trip is
        running, or the turn was closed because the model asked for a tool
        that had already run. The report goes into the system prompt, so the
        model reads *what actually happened* instead of guessing.
        """
        if self.last_config is None or self.commands_path is None:
            return False
        item = tools.get(name)
        if item is None:
            return False

        # A model that does not notice the result may ask for the same tool
        # again. Running it twice would duplicate whatever it created, so the
        # second request is not executed: the turn ends with what the model
        # managed to say plus the report of the first run.
        previous = self._tool_report(name, params)
        if previous is not None:
            said = tools.strip_requests(self.stream_buf).strip()
            if said:
                self.stream_buf = f"{said}\n\n{previous}".strip()
            else:
                # The model repeated the call without saying anything useful:
                # show the report itself rather than an empty bubble.
                self.stream_buf = i18n.T("msg.tool_done", report=previous)
            set_last_display(self.stream_buf)
            self._finish_reply()
            return True

        if self._tool_calls >= tools.MAX_TOOL_CALLS:
            self.stream_buf = (
                f"Tool budget for this turn is used up ({tools.MAX_TOOL_CALLS}). "
                f"Last result:\n{self.tool_results[-1]['report'] if self.tool_results else '-'}"
            )
            set_last_display(self.stream_buf)
            self._finish_reply()
            return True

        if self._tool_ok.get(name, 0) or self._tool_fail.get(name, 0):
            # The tool already ran in this turn. A successful run must not be
            # repeated - it would create a second copy of whatever the tool
            # builds. A failed one may be retried once, with fixed parameters;
            # a second failure is the end of the road.
            report = self._tool_reports.get(name, "")
            said = tools.strip_requests(self.stream_buf).strip()
            self.stream_buf = (
                f"{said}\n\n{report}".strip() if said else report
            ) or i18n.T("msg.tool_repeat", name=name)
            set_last_display(self.stream_buf)
            self._finish_reply()
            return True

        self._tool_calls += 1
        if item.destructive:
            # A destructive tool waits for the user. The turn stays open: the
            # model asked, the addon shows a confirmation and waits for the
            # click. Nothing is executed until then.
            self.pending_confirmation = {
                "id": name,
                "params": dict(params),
                "title": item.title,
                "description": item.description,
                # What is about to be destroyed, named out loud.
                "preview": tools.preview_of(name, params),
            }
            # The model's own message stays in the chat; the confirmation gets
            # its own bubble.
            self._remember_turn(self.stream_buf)
            self._keep_code(self.stream_buf)
            add_display("assistant", i18n.T("msg.tool_confirm", name=item.title))
            self.stream_buf = ""
            return True
        result = tools.execute(name, params)
        # The report is checked before it is stored: the note about a wrong
        # recipe has to reach the model *and* the user.
        report = self._warn_on_wrong_recipe(name, params, result.report)
        if self._tool_alias:
            # The model asked for a name that does not exist; the nearest one
            # was used. Say so, or the model will keep using the wrong name.
            asked, used = self._tool_alias
            report += f" (The tool is called '{used}', not '{asked}'.)"
            self._tool_alias = None
        self.tool_results.append({"id": result.id, "report": report})
        self._record_tool(name, params, report, result.ok)
        self._ran_tools.append(
            {
                "key": (name, tuple(sorted((params or {}).items()))),
                "id": name,
                "report": report,
            }
        )
        self._tool_ok[name] = self._tool_ok.get(name, 0) + (1 if result.ok else 0)
        self._tool_fail[name] = self._tool_fail.get(name, 0) + (0 if result.ok else 1)
        self._tool_reports[name] = report

        # The model must see what it said and what the tool answered, or it
        # starts the same answer from scratch and asks for the tool again.
        self._remember_turn(self.stream_buf)
        self.history.append(
            {
                "role": "user",
                "content": i18n.T("msg.tool_result", name=name, report=report),
            }
        )
        # The code of this turn stays available, and so does the text of the
        # turn: only the *next* round gets a new bubble.
        self._keep_code(self.stream_buf)

        add_display("result", report)
        self._ask_again(i18n.T("msg.tool_running", name=name))
        return True

    def _warn_on_wrong_recipe(self, tool_id: str, params: dict, report: str) -> str:
        """Say it out loud when the material is not the one the user asked for.

        The add-on knows the user's words; the model does not always. "Сделай
        текстуру дерева" ended up as bricks because the recipe was not found -
        and the user cannot see the recipe in the viewport, only the result.
        The warning goes into the report, so the model can put it right in the
        next round.
        """
        if tool_id != "procedural.build":
            return report
        recipe = str((params or {}).get("recipe") or "")
        text = self.turn_user_text
        if not recipe or not text:
            return report
        try:
            from . import procedural

            wanted = procedural.match_recipes(text, limit=1)
        except Exception:  # noqa: BLE001 - a check must not break the turn
            return report
        if not wanted or wanted[0][0].name == recipe:
            return report
        return (
            f"{report} Note: the request matches the recipe "
            f"'{wanted[0][0].name}', not '{recipe}' - build that one instead."
        )

    def _tool_report(self, name: str, params: dict):
        """The report of an earlier run of this exact call, or ``None``."""
        key = (name, tuple(sorted((params or {}).items())))
        for call in self._ran_tools:
            if call["key"] == key:
                return call["report"]
        return None

    def _keep_code(self, text: str) -> None:
        """Remember the code of a turn that is about to be interrupted.

        The model often writes the script *before* it calls a tool, and the
        call sends the turn into another round. The script must not be lost:
        it is what the "Run code" button offers.
        """
        code = extract_last_code(text or "")
        if code:
            self._turn_code = code
            self.pending_code = code
            self.code_ran = False

    def _ask_again(self, status: str = "") -> None:
        """Re-send the turn with everything gathered so far attached.

        A new bubble is opened for the next round: the messages of the
        previous rounds stay in the chat. A round trip is not a reason to
        erase what the model wrote - the user still wants to read it and to
        run the code from it.
        """
        self.stream_buf = ""
        add_display("assistant", status)
        self._write_command(
            {
                "type": "chat",
                "config": self.last_config,
                "messages": build_messages(
                    self.history, self._prompt_with_context(), self.last_max_chars
                ),
            }
        )
        ensure_timer()

    def _record_tool(self, name: str, params: dict, report: str, ok: bool) -> None:
        """Remember that a tool ran - the history is shown to the user."""
        self.tool_history.append(
            {"id": name, "params": dict(params or {}), "report": report, "ok": bool(ok)}
        )

    def run_pending_tool(self) -> Optional[dict]:
        """Execute the tool the user just confirmed.

        Returns the report, or ``None`` when nothing was pending.
        """
        pending = self.pending_confirmation
        if pending is None:
            return None
        self.pending_confirmation = None
        self._remember_turn(self.stream_buf)
        result = tools.execute(pending["id"], pending.get("params"))
        self.tool_results.append({"id": result.id, "report": result.report})
        self._record_tool(
            pending["id"], pending.get("params", {}), result.report, result.ok
        )
        self._ran_tools.append(
            {
                "key": (
                    pending["id"],
                    tuple(sorted((pending.get("params") or {}).items())),
                ),
                "id": pending["id"],
                "report": result.report,
            }
        )
        self._tool_ok[pending["id"]] = self._tool_ok.get(pending["id"], 0) + (
            1 if result.ok else 0
        )
        self._tool_fail[pending["id"]] = self._tool_fail.get(pending["id"], 0) + (
            0 if result.ok else 1
        )
        self._tool_reports[pending["id"]] = result.report
        self.history.append(
            {
                "role": "user",
                "content": i18n.T(
                    "msg.tool_result", name=pending["id"], report=result.report
                ),
            }
        )
        return {"id": result.id, "report": result.report, "ok": result.ok}

    def decline_pending_tool(self) -> Optional[dict]:
        """The user said no: tell the model and let it continue."""
        pending = self.pending_confirmation
        if pending is None:
            return None
        self.pending_confirmation = None
        self._remember_turn(self.stream_buf)
        report = i18n.T("msg.tool_declined", name=pending["title"])
        self.tool_results.append({"id": pending["id"], "report": report})
        self._record_tool(pending["id"], pending.get("params", {}), report, False)
        self.history.append(
            {
                "role": "user",
                "content": i18n.T("msg.tool_result", name=pending["id"], report=report),
            }
        )
        return {"id": pending["id"], "report": report, "ok": False}

    def _write_command(self, command: dict) -> None:
        assert self.commands_path is not None
        with open(self.commands_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(command, ensure_ascii=False) + "\n")

    # -- event pump (runs from bpy.app.timers on the main thread) ---------

    def _read_events(self) -> List[dict]:
        out: List[dict] = []
        if self.events_path is None or not self.events_path.exists():
            return out
        with open(self.events_path, "rb") as handle:
            handle.seek(self._events_offset)
            data = handle.read()
        if not data:
            return out
        self._events_offset += len(data)
        self._events_pending += data
        parts = self._events_pending.split(b"\n")
        self._events_pending = parts.pop()
        for raw in parts:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue
        return out

    def pump(self) -> None:
        if self.proc is None:
            return
        events = self._read_events()
        for event in events:
            self._handle(event)

        code = self.proc.poll()
        exited = code is not None
        if exited:
            # The worker exited. If we still expect text, that is a failure.
            log = self.log_path
            self.proc = None
            if self._stderr is not None:
                try:
                    self._stderr.close()
                except Exception:
                    pass
                self._stderr = None
            if self.busy:
                self._fail(
                    i18n.T("err.worker_died", code=code, log=log)
                )
        # Redraw only when something changed: the timer ticks at 10 Hz and an
        # unconditional tag_redraw() would keep repainting the whole UI idle.
        if events or exited:
            _redraw()

    def _handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "chunk":
            self.stream_buf += event.get("text", "")
            set_last_display(self.stream_buf)
        elif kind == "done":
            # A reply that asks for a tool is not an answer: run the tool and
            # re-send the same turn with the report attached.
            call = self._requested_tool()
            if call is not None and self._run_tool(*call):
                return
            # A reply that only asks for an instruction pack is not an answer:
            # pull the text in and re-send the same turn (bounded).
            name = self._requested_skill()
            if name is not None and self._fetch_skill(name):
                return
            self._finish_reply()
        elif kind == "error":
            self._fail(event.get("message") or i18n.T("err.unknown"))

    def _finish_reply(self) -> None:
        """Close the turn: drop request blocks, keep code, release busy."""
        text = tools.strip_requests(skills.strip_skill_requests(self.stream_buf))
        if text.strip():
            self.history.append({"role": "assistant", "content": text})
            # The code of the last round wins; if the last round had none, the
            # code from an interrupted round is still on offer.
            code = extract_last_code(text)
            if not code:
                code = self._turn_code
            if code != self.pending_code:
                # A new block: the chat shows it in full again.
                self.code_collapsed = False
            self.pending_code = code
            if code:
                self.code_ran = False
            if text != self.stream_buf:
                set_last_display(text)
        else:
            set_last_display(i18n.T("msg.empty_reply"))
        self.busy = False
        self.stream_buf = ""
        self.skill_texts = []
        self.auto_skills = []
        self._fetched_skills = 0
        # The tool reports stay: they are the record of what the turn did, and
        # they are reset by send() at the start of the next turn.

    def _fail(self, message: str) -> None:
        self.last_error = message
        # A pending confirmation dies with the turn.
        self.pending_confirmation = None
        if self.stream_buf:
            self.history.append({"role": "assistant", "content": self.stream_buf})
            set_last_display(self.stream_buf + "\n\n" + i18n.T("msg.interrupted"))
            self.stream_buf = ""
        else:
            drop_last_display_if_empty()
        add_display("error", message)
        self.busy = False

    # -- session reset ----------------------------------------------------

    def reset_session(self) -> None:
        self.history.clear()
        self.stream_buf = ""
        self.busy = False
        self.last_error = None
        self.pending_code = ""
        self.code_ran = False
        self._turn_code = ""
        self.turn_user_text = ""
        self.system_prompt = ""
        self.last_config = None
        self.skill_texts = []
        self.auto_skills = []
        self._fetched_skills = 0
        self.tool_results = []
        self._ran_tools = []
        self._tool_calls = 0
        self._tool_ok = {}
        self._tool_fail = {}
        self._tool_reports = {}
        self.pending_confirmation = None


_state = ChatState()


def get_state() -> ChatState:
    return _state


# --- the tools that talk about the tools -------------------------------------
#
# They live here rather than in tools.py because they need the chat state,
# and tools.py cannot import itself.


@tools.tool(
    "tools.history",
    "Tool history",
    "List the tools that ran during this conversation, with their reports. Use "
    "it when the user asks what has been done to the scene.",
)
def history_tool(**kwargs) -> str:
    """Tool entry point: the call history of this conversation."""
    history = list(getattr(get_state(), "tool_history", []))
    if not history:
        return "No tool has run in this conversation yet."
    lines = [f"{len(history)} tool call(s) so far:"]
    for entry in history:
        mark = "ok" if entry.get("ok") else "not done"
        lines.append(f"- {entry['id']} ({mark}): {entry['report']}")
    return "\n".join(lines)


@tools.tool(
    "tools.list",
    "List the tools",
    "List the tools the add-on can perform: materials, textures, UV, baking "
    "and the procedural material recipes.",
)
def list_tools_tool(**kwargs) -> str:
    """Tool entry point: the tool catalogue."""
    lines = ["Tools:"]
    for item in tools.all_tools():
        mark = " (destructive)" if item.destructive else ""
        lines.append(f"- {item.id}: {item.description}{mark}")
    return "\n".join(lines)


# -- timer plumbing ---------------------------------------------------------

def ensure_timer() -> None:
    if bpy.app.timers.is_registered(_timer_cb):
        return
    bpy.app.timers.register(
        _timer_cb, first_interval=EVENT_POLL_INTERVAL, persistent=True
    )


def remove_timer() -> None:
    if bpy.app.timers.is_registered(_timer_cb):
        bpy.app.timers.unregister(_timer_cb)


def _timer_cb():
    state = get_state()
    if state.proc is None:
        return None  # worker stopped: let the timer go (re-registered on send)
    try:
        state.pump()
    except Exception as exc:  # a timer callback must never raise silently
        print(f"[AssetAssistant] pump error: {exc}")
    return EVENT_POLL_INTERVAL
