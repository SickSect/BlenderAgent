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

from . import i18n, skills

ADDON_DIR = Path(__file__).resolve().parent
EVENT_POLL_INTERVAL = 0.1     # seconds between timer ticks
MAX_DISPLAY_CHARS = 8000      # per-message cap for the on-screen copy
DEFAULT_CONTEXT_CHARS = 24000 # model-facing history budget
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ```python\n...\n``` — the last python block of a reply is what the user may
# run with the "Выполнить код" button.
_CODE_BLOCK_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
_CODE_LANGS = ("", "python", "py", "bpy")


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

        # Re-sending the same turn after a skill request needs the original
        # prompt and transport settings kept around.
        self.system_prompt: str = ""
        self.last_config: Optional[dict] = None
        self.last_max_chars: int = DEFAULT_CONTEXT_CHARS
        self.skill_texts: List[tuple] = []  # (name, body) loaded for this turn

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
        self.busy = False
        self.stream_buf = ""

    # -- sending ----------------------------------------------------------

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

        messages = build_messages(self.history, system_prompt, max_chars)
        self._write_command({"type": "chat", "config": config, "messages": messages})
        ensure_timer()
        return True

    def _prompt_with_skills(self) -> str:
        """System prompt plus every instruction pulled in for this turn."""
        if not self.skill_texts:
            return self.system_prompt
        parts = [self.system_prompt]
        for name, body in self.skill_texts:
            parts.append(f"# skill: {name}\n\n{body}")
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
        :data:`skills.MAX_SKILL_LOADS` keeps that in check.
        """
        if len(self.skill_texts) >= skills.MAX_SKILL_LOADS:
            return False
        if self.last_config is None or self.commands_path is None:
            return False
        skill = skills.by_name(skill_library(), name)
        if skill is None:
            return False

        self.skill_texts.append((skill.name, skill.body))
        set_last_display(i18n.T("msg.skill_loading", name=skill.name))
        self.stream_buf = ""
        self._write_command(
            {
                "type": "chat",
                "config": self.last_config,
                "messages": build_messages(
                    self.history, self._prompt_with_skills(), self.last_max_chars
                ),
            }
        )
        ensure_timer()
        return True

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
        text = skills.strip_skill_requests(self.stream_buf)
        if text.strip():
            self.history.append({"role": "assistant", "content": text})
            self.pending_code = extract_last_code(text)
            self.code_ran = False
            if text != self.stream_buf:
                set_last_display(text)
        else:
            set_last_display(i18n.T("msg.empty_reply"))
        self.busy = False
        self.stream_buf = ""
        self.skill_texts = []

    def _fail(self, message: str) -> None:
        self.last_error = message
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
        self.system_prompt = ""
        self.last_config = None
        self.skill_texts = []


_state = ChatState()


def get_state() -> ChatState:
    return _state


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
