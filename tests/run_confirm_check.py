"""Blender-side checks for the confirmation of destructive tools.

    blender --background --factory-startup --python tests/run_confirm_check.py

When the assistant asks for a tool that changes data irreversibly, the add-on
must not run it: it waits for the user. This script drives that whole loop
with synthetic events - no model needed:

    model asks  ->  the add-on waits  ->  the user confirms  ->  the tool runs
                                                 ->  the model is asked again

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "src" / "blender_addon"

FAILURES = []


def check(condition, what):
    if condition:
        print(f"ok   {what}")
    else:
        FAILURES.append(what)
        print(f"FAIL {what}")


def load_package():
    package = types.ModuleType("blender_addon")
    package.__path__ = [str(ADDON_DIR)]
    package.__package__ = "blender_addon"
    sys.modules["blender_addon"] = package
    spec = importlib.util.spec_from_file_location(
        "blender_addon.__init__", ADDON_DIR / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["blender_addon.__init__"] = module
    spec.loader.exec_module(module)
    return module


def make_chat(state):
    """A chat state wired to a commands file, ready for synthetic events."""
    chat = state.ChatState()
    chat.commands_path = Path(tempfile.mkdtemp(prefix="gaa_confirm_")) / "commands.jsonl"
    chat.commands_path.write_text("", encoding="utf-8")
    chat.system_prompt = "SYS"
    chat.last_config = {"kind": "ollama", "host": "http://localhost:11434"}
    chat.last_max_chars = 4000
    chat.busy = True
    chat.history = [{"role": "user", "content": "удали неиспользуемое"}]
    return chat


def written_commands(chat):
    return [
        json.loads(line)
        for line in chat.commands_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# -- checks -----------------------------------------------------------------


def part_waiting(state, tools):
    print("\n== the add-on waits instead of running")
    chat = make_chat(state)

    # A destructive tool: it removes data.
    chat._handle(
        {
            "type": "chunk",
            "text": "Сейчас почищу.\n```tool\nmaterials.cleanup_orphans\n```\n",
        }
    )
    chat._handle({"type": "done"})

    check(chat.busy, "the turn stays open, waiting for the user")
    check(
        chat.pending_confirmation is not None,
        "a confirmation is pending",
    )
    if chat.pending_confirmation:
        check(
            chat.pending_confirmation["id"] == "materials.cleanup_orphans",
            "the pending tool is the one the model asked for",
        )
    check(not chat.tool_results, "nothing was executed yet")
    check(
        len(written_commands(chat)) == 0,
        "the model was not asked again while we wait",
    )


def part_confirm(state, tools):
    print("\n== the user confirms")
    chat = make_chat(state)
    chat._handle(
        {
            "type": "chunk",
            "text": "```tool\nmaterials.cleanup_orphans\n```\n",
        }
    )
    chat._handle({"type": "done"})
    check(chat.pending_confirmation is not None, "the confirmation is pending")

    orphan = bpy.data.materials.new("ConfirmOrphan")
    check(orphan.users == 0, "there is something to clean up")

    result = chat.run_pending_tool()
    check(result is not None, "the tool ran")
    check(chat.pending_confirmation is None, "the confirmation is cleared")
    check(bool(chat.tool_results), "the report was recorded")
    check(
        bpy.data.materials.get("ConfirmOrphan") is None,
        "the orphan material is really gone",
    )
    check(
        any("removed" in call["report"].lower() for call in chat.tool_results),
        f"the report says what happened ({chat.tool_results})",
    )

    # Now the model continues with the result in its context.
    chat._ask_again()
    commands = written_commands(chat)
    check(len(commands) == 1, "the model was asked again")
    if commands:
        system = commands[-1]["messages"][0]["content"]
        check("# tool result:" in system, "the report is in the system prompt")
        check(
            "ALREADY RUN" in system,
            "the model is told the tool has already run",
        )


def part_cancel(state, tools):
    print("\n== the user declines")
    chat = make_chat(state)
    chat._handle(
        {
            "type": "chunk",
            "text": "```tool\nmaterials.cleanup_orphans\n```\n",
        }
    )
    chat._handle({"type": "done"})
    check(chat.pending_confirmation is not None, "the confirmation is pending")

    keeper = bpy.data.materials.new("KeepMe")
    result = chat.decline_pending_tool()
    check(result is not None, "the decline was recorded")
    check(chat.pending_confirmation is None, "the confirmation is cleared")
    check(
        bpy.data.materials.get("KeepMe") is not None,
        "nothing was deleted",
    )
    check(
        any("declined" in call["report"].lower() for call in chat.tool_results),
        f"the model is told the user declined ({chat.tool_results})",
    )


def part_safe_tool(state, tools):
    print("\n== a safe tool runs without asking")
    chat = make_chat(state)
    chat._handle(
        {
            "type": "chunk",
            "text": "```tool\nmaterial.audit\n```\n",
        }
    )
    chat._handle({"type": "done"})
    check(chat.pending_confirmation is None, "no confirmation was asked for")
    check(bool(chat.tool_results), "the tool ran right away")
    check(len(written_commands(chat)) == 1, "the model was asked again")


def part_preview(state, tools):
    print("\n== the confirmation says what would be destroyed")
    bpy.data.materials.new("OrphanForPreview")
    chat = state.ChatState()
    chat.commands_path = Path(tempfile.mkdtemp(prefix="gaa_prev_")) / "commands.jsonl"
    chat.commands_path.write_text("", encoding="utf-8")
    chat.system_prompt = "SYS"
    chat.last_config = {"kind": "ollama", "host": "http://localhost:11434"}
    chat.last_max_chars = 4000
    chat.busy = True

    chat._handle(
        {
            "type": "chunk",
            "text": "```tool\nmaterials.cleanup_orphans\n```\n",
        }
    )
    chat._handle({"type": "done"})
    check(chat.pending_confirmation is not None, "the confirmation is pending")
    if chat.pending_confirmation:
        preview = chat.pending_confirmation.get("preview", "")
        check(bool(preview), f"the preview is there ({preview[:60]})")
        check(
            "OrphanForPreview" in preview,
            f"the preview names the material ({preview[:80]})",
        )


def part_history(state, tools):
    print("\n== the call history")
    chat = state.ChatState()
    chat.commands_path = Path(tempfile.mkdtemp(prefix="gaa_hist_")) / "commands.jsonl"
    chat.commands_path.write_text("", encoding="utf-8")
    chat.system_prompt = "SYS"
    chat.last_config = {"kind": "ollama", "host": "http://localhost:11434"}
    chat.last_max_chars = 4000
    chat.busy = True

    chat._handle(
        {"type": "chunk", "text": "```tool\nmaterial.audit\n```\n"}
    )
    chat._handle({"type": "done"})
    check(bool(chat.tool_history), "the call was recorded")
    if chat.tool_history:
        entry = chat.tool_history[0]
        check(entry["id"] == "material.audit", f"the history names the tool ({entry['id']})")
        check(entry["ok"] is True, "the history knows it worked")

    # The history tool reads the *global* state, so use it.
    result = tools.execute("tools.history")
    check(result.ok, "the history tool runs")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    package.register()
    try:
        state = importlib.import_module("blender_addon.state")
        tools = importlib.import_module("blender_addon.tools")

        part_waiting(state, tools)
        part_confirm(state, tools)
        part_cancel(state, tools)
        part_safe_tool(state, tools)
        part_preview(state, tools)
        part_history(state, tools)
    finally:
        package.unregister()

    print()
    if FAILURES:
        print(f"RESULT: FAILED ({len(FAILURES)})")
        for line in FAILURES:
            print(f"  - {line}")
        sys.exit(1)
    print("RESULT: OK")


main()
