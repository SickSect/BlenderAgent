"""Blender-side checks for the AI loop.

    blender --background --factory-startup --python tests/run_loop_check.py

The loop has two halves, and both are checked here:

* the result of "Run code" must reach the model, with success and failure
  told apart (``state.remember_code_result``);
* the doctor must tell the model when a material is still a flat colour
  (``doctor.FLAT_BASE_COLOR``) - that is the signal the model sees after it
  generated a material and the user ran the script.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
import sys
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
    """Import the add-on package from the working tree, without installing it."""
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


def part_code_result(state, i18n):
    print("\n== the code result reaches the model")
    chat = state.get_state()
    chat.history.clear()

    state.remember_code_result(True, "printed 1")
    check(len(chat.history) == 1, "a successful run adds one message")
    message = chat.history[-1]
    check(message["role"] == "user", "the result is a user turn")
    check("printed 1" in message["content"], "the output is in the message")
    check(
        message["content"] != "msg.code_result_for_model",
        "the message is resolved, not a raw key",
    )
    check(
        "without errors" in i18n.T("msg.code_result_for_model", lang="en"),
        "the success message says the code worked (en)",
    )

    state.remember_code_result(False, "Traceback: boom")
    check(len(chat.history) == 2, "a failure adds one message")
    message = chat.history[-1]
    check("Traceback: boom" in message["content"], "the traceback is in the message")
    check(
        "failed" in i18n.T("msg.code_error_for_model", lang="en"),
        "the failure message says the code failed (en)",
    )

    sent = state.build_messages(chat.history, "system", 100000)
    check(
        any("Traceback: boom" in item["content"] for item in sent),
        "build_messages sends the result to the model",
    )


def part_buttons(state):
    print("\n== the generic button report is not duplicated")
    chat = state.get_state()
    chat.history.clear()
    state.add_display("result", "generic report", remember=False)
    check(len(chat.history) == 0, "remember=False keeps the report out of history")
    state.add_display("result", "generic report")
    check(len(chat.history) == 1, "a button report is still remembered by default")


def part_snapshot():
    print("\n== the flat colour reaches the snapshot")
    snapshot = importlib.import_module("blender_addon.snapshot")
    mat = bpy.data.materials.new("LoopFlat")
    mat.use_nodes = True
    # The default Principled has an unlinked Base Color - exactly the shape the
    # model leaves behind when it only sets a colour.
    text = snapshot.build_snapshot(None)
    check("flat colour" in text.lower(), "the snapshot lists the flat Base Color")
    bpy.data.materials.remove(mat)


def main():
    package = load_package()
    package.register()
    try:
        state = importlib.import_module("blender_addon.state")
        i18n = importlib.import_module("blender_addon.i18n")
        part_code_result(state, i18n)
        part_buttons(state)
        part_snapshot()
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
