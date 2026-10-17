"""Sidecar worker: talks to the LLM backend for the Blender add-on.

Why a separate process with files instead of threads inside Blender:

  Blender's API docs ("Python Threads are Not Supported") state that
  background threads living past the script cause hard-to-diagnose crashes
  in Blender's own drawing code. So the add-on never blocks and never
  spawns threads — it starts this worker and polls two append-only JSONL
  files from a ``bpy.app.timers`` callback:

      add-on  --commands.jsonl-->  worker  --HTTP-->  Ollama / OpenAI API
      add-on  <--events.jsonl----  worker

Protocol (one JSON object per line):

  commands: {"type": "chat", "config": {...}, "messages": [...]}
            {"type": "ping"} | {"type": "shutdown"}
  events:   {"type": "chunk", "text": "..."} | {"type": "done"}
            {"type": "error", "message": "..."} | {"type": "pong"} | {"type": "bye"}

Usage: ``python worker.py <events.jsonl> <commands.jsonl>``

The same channel will carry tool-call requests/results in a later phase,
executed on the Blender main thread.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import List

if __package__ in (None, ""):
    # Started as a plain script (this is how the add-on spawns us):
    # make the package importable no matter where it lives on disk.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from asset_core.transport import BackendConfig, TransportError, stream_chat
else:
    from .transport import BackendConfig, TransportError, stream_chat

POLL_INTERVAL = 0.05  # seconds between command-file polls


class JsonlWriter:
    """Append-only JSONL writer. One flush per line, no buffering surprises."""

    def __init__(self, path):
        self.path = Path(path)
        self._file = open(self.path, "a", encoding="utf-8")

    def emit(self, obj: dict) -> None:
        self._file.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


class JsonlReader:
    """Reads new complete lines from an append-only file since last read."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self._pending = b""

    def read_new(self) -> List[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        if not data:
            return []
        self.offset += len(data)
        self._pending += data
        parts = self._pending.split(b"\n")
        self._pending = parts.pop()
        out: List[dict] = []
        for raw in parts:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue  # tolerate a torn line from a killed writer
        return out


def handle_chat(writer: JsonlWriter, command: dict) -> None:
    """Stream one chat request, emitting chunk/done or a single error event."""
    try:
        config = BackendConfig.from_dict(command.get("config"))
        messages = command.get("messages") or []
        for chunk in stream_chat(config, messages):
            writer.emit({"type": "chunk", "text": chunk})
        writer.emit({"type": "done"})
    except TransportError as exc:
        writer.emit({"type": "error", "message": str(exc)})
    except Exception as exc:  # never die silently — the add-on watches for this
        writer.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print("usage: worker.py <events.jsonl> <commands.jsonl>", file=sys.stderr)
        return 2

    events = JsonlWriter(argv[1])
    commands = JsonlReader(argv[2])
    print(f"[asset_core.worker] started, python {sys.version.split()[0]}", file=sys.stderr)

    try:
        while True:
            for command in commands.read_new():
                kind = command.get("type")
                if kind == "chat":
                    handle_chat(events, command)
                elif kind == "ping":
                    events.emit({"type": "pong"})
                elif kind == "shutdown":
                    events.emit({"type": "bye"})
                    return 0
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        return 0
    finally:
        events.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
