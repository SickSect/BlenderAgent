"""Registry of deterministic tools.

A **tool** is a named action the add-on can perform. One implementation, three
consumers:

* a **button** in the UI (an operator in ``operators.py`` calls the handler);
* a **call the model may request** by name (a text protocol, like the one the
  skills use — local models often have no native tool-calling);
* a **target for the headless test harness** (``tests/run_tools_check.py``),
  which runs every tool on a synthetic scene in ``--background``.

Why a registry at all? Because the alternative is three copies of the same
feature. Adding a button means writing the function, then an operator, then a
UI row, then — later — a model-call path. With the registry the function is
written once and decorated; the rest appears by itself.

Safety is part of the contract, not an afterthought: a tool declares whether it
is **destructive**. Non-destructive tools may be executed on the model's
request; destructive ones are refused and the model is told to ask the user to
press the button instead.

The module is bpy-free on purpose: the registry, the catalog and the request
parser are plain Python and can be unit-tested without Blender.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# ```tool\nmaterial.audit {"material": "Wood"}\n``` — how the model asks.
_REQUEST_FENCE_RE = re.compile(
    r"```[ \t]*tool[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE
)
# A bare line is a tolerated fallback: TOOL: material.audit
_REQUEST_LINE_RE = re.compile(
    r"^[ \t>*#-]*TOOL:[ \t]*([A-Za-z0-9_.\-]+)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
# Some models ignore the fence and emit the call as plain JSON:
#   {"name": "material.audit", "arguments": {"material": "Wood"}}
# That is worth understanding: it is what a model without native
# tool-calling does when it is handed a tool schema. The parser accepts it,
# so the same text protocol works with those models too.
_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9_.\-]*\Z")

# ```python\n...\n``` - code the user may want to run. A JSON object inside a
# code block is NOT a tool call: models write dictionaries in code all the
# time, and reading them as calls would run tools nobody asked for.
_CODE_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_CODE_LANGS = ("python", "py", "bpy", "text")


def _without_code(text: str) -> str:
    """The text with python/text code blocks taken out.

    Tool and json blocks stay: they are the places where a call may live.
    """
    out = []
    position = 0
    for match in _CODE_FENCE_RE.finditer(text or ""):
        out.append(text[position:match.start()])
        if match.group(1).lower() not in _CODE_LANGS:
            out.append(match.group(0))  # tool and json blocks stay
        position = match.end()
    out.append(text[position:])
    return "".join(out)


def _json_objects(text: str) -> List[Dict]:
    """Every top-level JSON object in *text*.

    The objects are found by balancing the braces, so nested arguments do
    not break the scan - a plain regex cannot do that.
    """
    found: List[Dict] = []
    if not text:
        return found
    for match in re.finditer(r"\{", text):
        start = match.start()
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:index + 1])
                    except (ValueError, TypeError):
                        data = None
                    if isinstance(data, dict):
                        found.append(data)
                    break
    return found

# How many tool calls may run in a single turn. Every call costs one extra
# round trip to the model, so the budget stays small on purpose.
MAX_TOOL_CALLS = 4


@dataclass(frozen=True)
class Tool:
    """One registered action.

    ``handler`` is called as ``handler(**params)`` and returns a report
    string - a short, factual summary of what it did, meant for both the
    status bar and the model's context.

    ``preview`` is optional: for a destructive tool it returns what is going
    to be destroyed, so the confirmation can say it out loud instead of just
    naming the tool.
    """

    id: str
    title: str
    description: str
    handler: Callable
    destructive: bool = False
    params: Tuple[str, ...] = ()
    preview: Optional[Callable] = None


@dataclass
class ToolResult:
    """What a tool call produced."""

    id: str
    ok: bool
    report: str
    refused: bool = False
    params: Dict = field(default_factory=dict)


_REGISTRY: Dict[str, Tool] = {}


def tool(
    tool_id: str,
    title: str,
    description: str,
    destructive: bool = False,
    params: Sequence[str] = (),
    preview: Optional[Callable] = None,
    aliases: Sequence[str] = (),
):
    """Register a function as a tool.

    Usage::

        @tool("material.audit", "Audit materials",
              "Find wrong colour spaces, missing textures and missing UVs")
        def audit(**kwargs):
            return "..."

    ``preview`` is optional and only used for destructive tools: it returns
    what is about to be destroyed, so the confirmation can say it out loud.

    ``aliases`` are other names the same tool answers to. A small model
    guesses the name from the pattern it sees ("material.preset" instead of
    "procedural.preset"), and a call that hits nothing is a call that is
    silently dropped.
    """

    def wrap(func: Callable) -> Callable:
        item = Tool(
            id=tool_id,
            title=title,
            description=description,
            handler=func,
            destructive=destructive,
            params=tuple(params),
            preview=preview,
        )
        _REGISTRY[tool_id] = item
        for alias in aliases:
            _REGISTRY[alias] = item
        return func

    return wrap


def get(tool_id: str) -> Optional[Tool]:
    """The tool with that id, or ``None``."""
    return _REGISTRY.get((tool_id or "").strip().lower())


def closest(tool_id: str, limit: int = 1) -> List[Tool]:
    """The tools whose ids are the closest to *tool_id*.

    The model asks for "material.preset" when the tool is "procedural.preset".
    Dropping the call would be worse than picking the nearest name.
    """
    wanted = (tool_id or "").strip().lower()
    if not wanted:
        return []
    import difflib

    names = difflib.get_close_matches(wanted, list(_REGISTRY), n=limit, cutoff=0.6)
    return [_REGISTRY[name] for name in names]



def all_tools() -> List[Tool]:
    """Every registered tool, sorted by id."""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def catalog() -> str:
    """``- id: description`` lines for the system prompt.

    One line per tool, always present, exactly like the skill catalog. The
    model needs to know what it *can* do before it can ask for it.
    """
    lines = []
    for item in all_tools():
        detail = item.description or item.title
        suffix = " (destructive)" if item.destructive else ""
        lines.append(f"- {item.id}: {detail}{suffix}")
    return "\n".join(lines)


def _json_candidates(text: str) -> List[str]:
    """The ways *text* might be read as JSON.

    Models write the parameters by hand, and their JSON is often a little
    broken: a trailing comma, a Python tuple instead of a list, single quotes.
    Every repair below produces valid JSON, so trying them in order is safe.
    """
    text = (text or "").strip()
    if not text:
        return []
    out = [text]
    no_commas = re.sub(r",\s*([}\]])", r"\1", text)
    out.append(no_commas)
    # (1, 12) is a Python tuple; in JSON it is [1, 12].
    out.append(
        re.sub(r",\s*([}\]])", r"\1", no_commas.replace("(", "[").replace(")", "]"))
    )
    if '"' not in text:
        out.append(no_commas.replace("'", '"'))
    return out


def _json_payload(text: str) -> Dict:
    """The parameters of a tool call, as a dict.

    Models write the parameters in many ways: on one line, across several
    lines, with a trailing comma, or wrapped in a sentence. A plain
    ``json.loads`` fails on most of that, and the difference matters: without
    the parameters the tool would build a default material while the model
    believes it set the colours.
    """
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    for candidate in _json_candidates(text):
        for data in _json_objects(candidate):
            return data
    return {}


def parse_request(text: str) -> List[Tuple[str, Dict]]:
    """Tool calls requested in *text*, in order, without duplicates.

    A call is one of:

    * a fenced block::

        ```tool
        material.audit {"material": "Wood"}
        ```

    * a single line ``TOOL: material.audit``;
    * a plain JSON object ``{"name": ..., "arguments": {...}}`` - what a model
      without native tool-calling produces when it is handed a tool schema.

    The parameters are optional and may be JSON.
    """
    calls: List[Tuple[str, Dict]] = []

    def remember(raw: str) -> None:
        candidate = (raw or "").strip().strip("`").strip()
        if not candidate:
            return
        # The name comes first; the parameters (optional) follow, possibly on
        # the next line.
        match = re.match(r"\A([A-Za-z0-9_.\-]+)\s*(.*)\Z", candidate, re.DOTALL)
        if match is None:
            return
        name = match.group(1).lower()
        if not _NAME_RE.match(name):
            return
        rest = match.group(2).strip()
        payload: Dict = _json_payload(rest)
        for existing, _ in calls:
            if existing == name:
                return
        calls.append((name, payload))

    for block in _REQUEST_FENCE_RE.findall(text or ""):
        remember(block)
    for line in _REQUEST_LINE_RE.findall(text or ""):
        remember(line)
    for data in _json_objects(_without_code(text)):
        name = str(data.get("name", "")).strip().lower()
        if not _NAME_RE.match(name):
            continue
        arguments = data.get("arguments")
        payload = arguments if isinstance(arguments, dict) else {}
        if not any(existing == name for existing, _ in calls):
            calls.append((name, payload))
    return calls


def strip_requests(text: str) -> str:
    """*text* without tool requests — keeps them out of history and code."""
    if not text:
        return ""
    cleaned = _REQUEST_FENCE_RE.sub("", text)
    cleaned = _REQUEST_LINE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def execute(tool_id: str, params: Optional[Dict] = None) -> ToolResult:
    """Run a tool and wrap the outcome.

    A tool that raises is **not** a crash: the error becomes the report, so
    the model can read what went wrong and try something else.
    """
    params = dict(params or {})
    item = get(tool_id)
    if item is None:
        return ToolResult(
            id=tool_id,
            ok=False,
            report=f"Unknown tool: {tool_id}",
            params=params,
        )
    try:
        report = item.handler(**params)
    except Exception as exc:  # noqa: BLE001 - a bad tool must not kill the chat
        return ToolResult(
            id=item.id,
            ok=False,
            report=f"{type(exc).__name__}: {exc}",
            params=params,
        )
    text = "" if report is None else str(report)
    return ToolResult(id=item.id, ok=True, report=text, params=params)


def preview_of(tool_id: str, params: Optional[Dict] = None) -> str:
    """What a destructive tool is about to destroy ("" when it cannot say)."""
    item = get(tool_id)
    if item is None or item.preview is None:
        return ""
    try:
        return str(item.preview(dict(params or {})) or "")
    except Exception:  # noqa: BLE001 - a broken preview must not break the flow
        return ""


def refuse(tool_id: str, params: Optional[Dict] = None) -> ToolResult:
    item = get(tool_id)
    name = item.title if item is not None else tool_id
    return ToolResult(
        id=tool_id,
        ok=False,
        refused=True,
        report=(
            f"Tool '{tool_id}' ({name}) changes data irreversibly, so it was NOT "
            "run. Tell the user which button to press in the AI panel instead."
        ),
        params=dict(params or {}),
    )
