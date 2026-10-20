# tests/tools_test.py
"""Pure-python checks for the tool registry.

``tools.py`` is deliberately bpy-free, so it can be imported here by path —
without Blender and without importing the add-on package (whose ``__init__``
needs ``bpy``).

The companion ``tests/run_tools_check.py`` runs *inside* Blender and executes
every registered tool on a synthetic scene; that is what catches a tool that
needs a window or that breaks the scene.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_PY = ROOT / "src" / "blender_addon" / "tools.py"

_spec = importlib.util.spec_from_file_location("gaa_tools_under_test", TOOLS_PY)
tools = importlib.util.module_from_spec(_spec)
sys.modules["gaa_tools_under_test"] = tools
_spec.loader.exec_module(tools)


# -- registration -----------------------------------------------------------


def test_register_and_find():
    @tools.tool("test.one", "Test one", "Does one thing")
    def one(**kwargs):
        return "done"

    assert tools.get("test.one") is not None
    assert tools.get("TEST.ONE") is not None  # ids are case-insensitive
    assert tools.get("test.missing") is None


def test_tool_records_destructive_flag():
    @tools.tool("test.safe", "Safe", "Safe action")
    def safe(**kwargs):
        return "ok"

    @tools.tool("test.hot", "Hot", "Dangerous action", destructive=True)
    def hot(**kwargs):
        return "boom"

    assert tools.get("test.safe").destructive is False
    assert tools.get("test.hot").destructive is True


def test_catalog_has_one_line_per_tool():
    @tools.tool("test.alpha", "Alpha", "First")
    def alpha(**kwargs):
        return "a"

    @tools.tool("test.beta", "Beta", "Second")
    def beta(**kwargs):
        return "b"

    lines = tools.catalog().splitlines()
    assert any(line.startswith("- test.alpha:") for line in lines)
    assert any(line.startswith("- test.beta:") for line in lines)
    for line in lines:
        assert line.startswith("- ")


def test_catalog_marks_destructive_tools():
    @tools.tool("test.hot2", "Hot", "Dangerous", destructive=True)
    def hot(**kwargs):
        return "boom"

    assert "(destructive)" in tools.catalog()


# -- request parsing --------------------------------------------------------


def test_fenced_request():
    calls = tools.parse_request("сделай\n```tool\nmaterial.audit\n```\nготово")
    assert calls == [("material.audit", {})]


def test_request_with_json_parameters():
    calls = tools.parse_request(
        '```tool\nmaterial.build_from_folder {"folder": "D:/tex"}\n```'
    )
    assert calls == [("material.build_from_folder", {"folder": "D:/tex"})]


def test_request_with_broken_json_still_parses():
    calls = tools.parse_request("```tool\nmaterial.audit {oops\n```")
    assert calls == [("material.audit", {})]


def test_plain_json_call_is_understood():
    # A model without native tool-calling, handed a tool schema, emits the
    # call as plain JSON instead of using the fence.
    calls = tools.parse_request(
        '{"name": "material.audit", "arguments": {"material": "Wood"}}'
    )
    assert calls == [("material.audit", {"material": "Wood"})]


def test_plain_json_call_with_arguments():
    calls = tools.parse_request(
        '{"name": "material.audit", "arguments": {"material": "Wood"}}'
    )
    assert calls[0][0] == "material.audit"


def test_json_that_is_not_a_call_is_ignored():
    calls = tools.parse_request('{"note": "no tool here"}')
    assert calls == []


def test_json_call_inside_prose_is_found():
    calls = tools.parse_request(
        'Сейчас проверю.\n{"name": "material.audit", "arguments": {}}\nГотово.'
    )
    assert calls == [("material.audit", {})]


def test_line_request():
    calls = tools.parse_request("TOOL: material.audit")
    assert calls == [("material.audit", {})]


def test_duplicate_requests_are_collapsed():
    calls = tools.parse_request(
        "```tool\nmaterial.audit\n```\nTOOL: material.audit"
    )
    assert len(calls) == 1


def test_python_block_is_not_a_tool_request():
    assert tools.parse_request("```python\nprint(1)\n```") == []
    assert tools.parse_request("```skill\nmaterial-glass\n```") == []


def test_strip_requests():
    text = "До:\n```tool\nmaterial.audit\n```\nПосле"
    stripped = tools.strip_requests(text)
    assert "tool" not in stripped
    assert stripped.startswith("До:")
    assert stripped.endswith("После")


# -- execution --------------------------------------------------------------


def test_execute_returns_the_report():
    @tools.tool("test.report", "Report", "Returns a report")
    def report(**kwargs):
        return "the report"

    result = tools.execute("test.report")
    assert result.ok is True
    assert result.report == "the report"


def test_execute_passes_parameters():
    @tools.tool("test.echo", "Echo", "Echoes the folder")
    def echo(folder=None, **kwargs):
        return f"folder={folder}"

    result = tools.execute("test.echo", {"folder": "D:/tex"})
    assert result.report == "folder=D:/tex"


def test_a_failing_tool_does_not_raise():
    @tools.tool("test.boom", "Boom", "Always fails")
    def boom(**kwargs):
        raise RuntimeError("kaboom")

    result = tools.execute("test.boom")
    assert result.ok is False
    assert "kaboom" in result.report


def test_unknown_tool_is_reported_not_raised():
    result = tools.execute("test.nope")
    assert result.ok is False
    assert "Unknown tool" in result.report


def test_refusal_blocks_destructive_tools():
    @tools.tool("test.delete", "Delete", "Deletes things", destructive=True)
    def delete(**kwargs):
        return "deleted"

    result = tools.refuse("test.delete")
    assert result.ok is False
    assert result.refused is True
    assert "not" in result.report.lower()
