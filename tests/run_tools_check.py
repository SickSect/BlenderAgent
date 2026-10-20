"""Blender-side checks for the tool registry.

    blender --background --factory-startup --python tests/run_tools_check.py

A tool is a promise: it runs in the current scene, it needs no window, it
answers with a report, and running it twice does not break anything. This
script holds every registered tool to that promise on a synthetic scene.

It also checks the version-tolerant helpers (``api.py``) against the *running*
Blender: a renamed socket would be caught here, not by a user.

Exit code 1 and a "RESULT: FAILED" line when anything failed.
"""

from __future__ import annotations

import importlib
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
    """Import the add-on package from the working tree, without installing it.

    The package ``__init__`` is executed (it only defines functions), but
    ``register()`` is not called: this script tests the modules, not the
    Blender registration.
    """
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


# -- a synthetic scene ------------------------------------------------------


def write_texture(directory, name, colorspace="sRGB", size=64):
    """A real PNG on disk, loaded as a file-backed image."""
    path = Path(directory) / f"{name}.png"
    scratch = bpy.data.images.new(f"{name}_scratch", width=size, height=size)
    scratch.filepath_raw = str(path)
    scratch.file_format = "PNG"
    scratch.save()
    bpy.data.images.remove(scratch)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = name
    image.colorspace_settings.name = colorspace
    return image


def build_scene():
    """A cube with an active object and a folder of texture maps."""
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ToolCube"

    # A folder with a stable name: the material is named after it.
    root = Path(tempfile.mkdtemp(prefix="gaa_tools_"))
    folder = root / "ToolPack"
    folder.mkdir()
    write_texture(folder, "Tool_BaseColor", "sRGB")
    write_texture(folder, "Tool_Roughness", "Non-Color")
    write_texture(folder, "Tool_Normal", "Non-Color")
    return cube, folder


# -- checks -----------------------------------------------------------------


def part_api(api):
    print("\n== version-tolerant helpers")
    mat = bpy.data.materials.new("ApiCheck")
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = api.find_principled(tree)
    check(bsdf is not None, "the Principled BSDF is found by type")
    check(api.find_output(tree) is not None, "the material output is found")

    # Every spelling must resolve to the same socket.
    transmission = api.get_input(bsdf, "Transmission Weight", "Transmission")
    check(transmission is not None, "Transmission Weight resolves")
    check(
        api.set_input(bsdf, 0.25, "Roughness"),
        "Roughness accepts a value",
    )
    check(
        abs(api.get_input(bsdf, "Roughness").default_value - 0.25) < 1e-6,
        "the value was written",
    )
    check(
        api.set_input(bsdf, 1.0, "Transmission Weight", "Transmission"),
        "Transmission accepts a value on this version",
    )
    check(not api.set_input(bsdf, 1.0, "NoSuchSocket"), "a missing socket is refused")
    check(
        api.canonical_socket("Transmission") == "Transmission Weight",
        "a legacy name maps to the canonical one",
    )
    check("Roughness" in api.DATA_SOCKETS, "Roughness is a data socket")
    bpy.data.materials.remove(mat)


def part_registry(tools):
    print("\n== the registry")
    registered = tools.all_tools()
    print(f"   {len(registered)} tools: {', '.join(t.id for t in registered)}")
    check(bool(registered), "at least one tool is registered")
    for item in registered:
        check(bool(item.description), f"{item.id} has a description for the prompt")
        check(bool(item.title), f"{item.id} has a title")
    catalog = tools.catalog()
    for item in registered:
        check(item.id in catalog, f"{item.id} is in the prompt catalog")


def part_tools(tools, cube, folder):
    print("\n== every tool runs headless")
    for item in tools.all_tools():
        params = {}
        if "folder" in item.params:
            params["folder"] = str(folder)
        result = tools.execute(item.id, params)
        check(result.ok, f"{item.id} runs without raising")
        check(bool(result.report), f"{item.id} returns a report")

        # Running it a second time must not break the scene or pile up data.
        again = tools.execute(item.id, params)
        check(again.ok, f"{item.id} survives a second run")
        check(
            bpy.context.view_layer.objects.active is not None,
            f"{item.id} leaves a valid active object",
        )


def part_build_from_folder(tools, materials, cube, folder):
    print("\n== material.build_from_folder")
    result = tools.execute("material.build_from_folder", {"folder": str(folder)})
    check(result.ok, "the tool succeeds")
    check("ToolPack" in result.report, f"the report names the material: {result.report}")

    material = bpy.data.materials.get("ToolPack")
    check(material is not None, "the material exists")
    if material is not None:
        bsdf = next(
            (n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None
        )
        check(bsdf is not None, "the material has a Principled BSDF")
        textures = [
            n for n in material.node_tree.nodes if n.type == "TEX_IMAGE"
        ]
        check(len(textures) == 3, f"three texture nodes were wired ({len(textures)})")
        rough = bpy.data.images.get("Tool_Roughness")
        check(
            rough is not None and rough.colorspace_settings.name == "Non-Color",
            "the roughness map kept its colour space",
        )
        check(
            material.get("gaa_source_folder") == str(folder),
            "the material remembers the folder it came from",
        )
    check(
        cube.data.materials and cube.data.materials[0] is material,
        "the material is assigned to the active object",
    )

    # A second run must not create "ToolPack.001".
    tools.execute("material.build_from_folder", {"folder": str(folder)})
    check(
        bpy.data.materials.get("ToolPack.001") is None,
        "a second run reuses the material instead of duplicating it",
    )
    check(
        len([m for m in bpy.data.materials if m.name == "ToolPack"]) == 1,
        "exactly one material was created for the folder",
    )


def part_operator(tools, materials, cube, folder):
    print("\n== the button and the tool share one implementation")
    operator_result = materials.build_pbr_from_folder(str(folder), assign_to=cube)
    tool_result = tools.execute("material.build_from_folder", {"folder": str(folder)})
    check(operator_result[0] is not None, "the shared function builds a material")
    check(tool_result.ok, "the tool reports success")
    check(
        operator_result[3] is True,
        "the second build reuses the material (the folder is remembered)",
    )


def part_context(state, tools):
    print("\n== the model-call loop")
    chat = state.ChatState()
    chat.commands_path = Path(tempfile.mkdtemp(prefix="gaa_ctx_")) / "commands.jsonl"
    chat.commands_path.write_text("", encoding="utf-8")
    chat.system_prompt = "SYS"
    chat.last_config = {"kind": "ollama", "host": "http://localhost:11434"}
    chat.last_max_chars = 4000
    chat.busy = True
    chat.history = [{"role": "user", "content": "построй материал"}]

    # The model asks for a tool; the add-on must run it and ask again.
    chat._handle(
        {"type": "chunk", "text": "Сейчас сделаю.\n```tool\nmaterial.build_from_folder\n```\n"}
    )
    chat._handle({"type": "done"})
    check(chat.busy, "the turn stays busy while the tool runs")
    check(bool(chat.tool_results), "a tool result was recorded")
    if chat.tool_results:
        check(
            "# tool result:" in chat._prompt_with_context(),
            "the report is attached to the system prompt",
        )
    written = chat.commands_path.read_text(encoding="utf-8").strip().splitlines()
    check(len(written) == 1, "exactly one extra request was written")
    if written:
        import json

        system = json.loads(written[-1])["messages"][0]["content"]
        check("# tool result:" in system, "the re-sent prompt carries the report")

    # The second reply answers; the turn must close and the request block must
    # not leak into the history.
    chat._handle({"type": "chunk", "text": "Готово, материал собран.\n"})
    chat._handle({"type": "done"})
    check(not chat.busy, "the turn closes after the answer")
    check("tool" not in chat.history[-1]["content"], "the request block is stripped")


# -- runner -----------------------------------------------------------------


def main():
    package = load_package()
    # Register the add-on for real: the model-call loop touches the
    # WindowManager props, which only exist after registration.
    package.register()
    try:
        api = importlib.import_module("blender_addon.api")
        tools = importlib.import_module("blender_addon.tools")
        materials = importlib.import_module("blender_addon.materials")
        state = importlib.import_module("blender_addon.state")

        cube, folder = build_scene()
        part_api(api)
        part_registry(tools)
        # The specific checks come before the generic loop: running every
        # tool has side effects (a variant is a copy of a material), and the
        # specific checks count materials.
        part_build_from_folder(tools, materials, cube, folder)
        part_operator(tools, materials, cube, folder)
        part_tools(tools, cube, folder)
        part_context(state, tools)
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
