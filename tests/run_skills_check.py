"""Blender-side checks for the add-on's skill library.

    blender --background --factory-startup --python tests/run_skills_check.py

Three parts:

1. load ``src/blender_addon/skills/`` from the *working tree* and run the checks
   that need a real Blender version;
2. execute every ```python block of every skill on the running Blender — this
   is what catches ``Transmission`` -> ``Transmission Weight`` before a user
   does;
3. when the add-on is installed, drive the skill round trip in ``state`` with
    synthetic events, so the second-pass loop is covered without a model.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "src" / "blender_addon"
SKILLS_DIR = ADDON_DIR / "skills"

FAILURES = []


def check(condition, what):
    if condition:
        print(f"ok   {what}")
    else:
        FAILURES.append(what)
        print(f"FAIL {what}")


def fail(what, detail):
    FAILURES.append(f"{what}: {detail}")
    print(f"FAIL {what}: {detail}")


def load_source_skills():
    """Import skills.py straight from the working tree, no installation."""
    spec = importlib.util.spec_from_file_location("gaa_skills_under_test", ADDON_DIR / "skills.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gaa_skills_under_test"] = module
    spec.loader.exec_module(module)
    return module


# -- part 1: library logic --------------------------------------------------


def part_library():
    skills = load_source_skills()
    version = tuple(bpy.app.version)
    loaded = skills.load_all(SKILLS_DIR, version)
    print(f"\n== library on Blender {'.'.join(str(part) for part in version)}")
    print(f"   {len(loaded)} skills: {', '.join(skill.name for skill in loaded)}")
    check(len(loaded) >= 6, "at least six skills are found")
    catalog = skills.catalog(loaded)
    check(catalog.count("\n") + 1 == len(loaded), "catalog has one line per skill")

    fields, body = skills.parse_document("---\nname: x\n---\nBody\n")
    check(fields.get("name") == "x" and body.strip() == "Body", "front matter parsed")

    check(skills.version_satisfies(">=4.2", (5, 2, 0)), "version >=4.2 accepts 5.2")
    check(not skills.version_satisfies(">=4.2", (3, 6, 0)), "version >=4.2 rejects 3.6")

    request = "нужна инструкция\n```skill\nmaterial-glass\n```\nготово"
    check(skills.parse_skill_requests(request) == ["material-glass"], "request parsed")
    check(skills.parse_skill_requests("```python\nprint(1)\n```") == [], "python is not a request")
    check("skill" not in skills.strip_skill_requests(request), "request stripped")
    return skills, loaded


# -- part 2: every code block runs -----------------------------------------


def part_blocks(skills, loaded):
    active = bpy.context.view_layer.objects.active
    if active is None or active.type != "MESH":
        try:
            bpy.ops.mesh.primitive_cube_add()
            active = bpy.context.view_layer.objects.active
        except Exception as exc:  # noqa: BLE001
            print(f"   (could not create a cube: {exc})")
    print(f"\n== executing code blocks (active object: "
          f"{getattr(active, 'name', None)!r})")
    for skill in loaded:
        for index, block in enumerate(skill.code_blocks(), start=1):
            namespace = {"bpy": bpy}
            try:
                exec(compile(block, f"<skill:{skill.name}#{index}>", "exec"), namespace)
            except Exception as exc:  # noqa: BLE001 - that is the point
                fail(f"{skill.name} block {index}", f"{type(exc).__name__}: {exc}")
            else:
                print(f"ok   {skill.name} block {index}")


# -- part 3: the round trip in state.py ------------------------------------


def part_loop():
    module = "bl_ext.user_default.asset_assistant"
    try:
        bpy.ops.preferences.addon_enable(module=module)
    except Exception as exc:  # noqa: BLE001
        print(f"\n== round trip: skipped, extension not installed ({exc})")
        return

    import importlib

    installed = importlib.import_module(module + ".state")
    print("\n== skill round trip (installed add-on)")
    library = installed.skill_library()
    check(bool(library), "installed add-on ships the skills directory")
    if not library:
        return

    chat = installed.ChatState()
    chat.commands_path = Path(tempfile.mkdtemp(prefix="gaa_skills_")) / "commands.jsonl"
    chat.commands_path.write_text("", encoding="utf-8")
    installed.add_display("assistant", "")
    chat.history = [{"role": "user", "content": "сделай стеклянный материал"}]
    chat.system_prompt = "SYS"
    chat.last_config = {"kind": "ollama", "host": "http://localhost:11434"}
    chat.last_max_chars = 1000
    chat.busy = True

    # First reply: the model asks for an instruction.
    chat._handle({"type": "chunk", "text": "Нужна инструкция\n```skill\nmaterial-glass\n```\n"})
    chat._handle({"type": "done"})
    check(chat.busy, "turn stays busy while the instruction is fetched")
    check([name for name, _ in chat.skill_texts] == ["material-glass"], "glass was loaded")
    written = chat.commands_path.read_text(encoding="utf-8").strip().splitlines()
    check(len(written) == 1, "exactly one extra request was written")
    system = json.loads(written[-1])["messages"][0]["content"]
    check("# skill: material-glass" in system, "instruction appended to the system prompt")
    check("Transmission Weight" in system, "the appended text is the real body")

    # Second reply: repeats the request (must not loop) and finally answers.
    chat._handle(
        {
            "type": "chunk",
            "text": "```python\nmat = None\n```\n```skill\nmaterial-glass\n```\n",
        }
    )
    chat._handle({"type": "done"})
    check(not chat.busy, "turn closes after the answer")
    check(chat.pending_code.strip() == "mat = None", "code block extracted from the answer")
    check(chat.history[-1]["role"] == "assistant", "answer stored as an assistant message")
    check(not chat.skill_texts, "skill buffer cleared with the turn")


# -- runner -----------------------------------------------------------------


def main():
    skills, loaded = part_library()
    part_blocks(skills, loaded)
    part_loop()
    print()
    if FAILURES:
        print(f"RESULT: FAILED ({len(FAILURES)})")
        for line in FAILURES:
            print(f"  - {line}")
        sys.exit(1)
    print("RESULT: OK")


main()
