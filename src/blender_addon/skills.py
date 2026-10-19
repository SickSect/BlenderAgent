"""Skill library — versioned instruction packs the assistant can pull in.

A *skill* is a markdown file with a small front matter block:

    ---
    name: material-glass
    title: Glass material
    description: Transmission Weight, IOR, EEVEE settings
    keywords: glass, transmission, ior
    blender: ">=4.2"
    ---
    ...instructions and at least one ```python block...

Why files instead of a longer system prompt:

* the catalog (name + one line) is cheap and always present;
* a body is pulled in only when the model asks for it, so it never competes
  with the conversation history for context tokens;
* skills live in git next to the code, so a wrong instruction is a reviewable
  diff instead of a model hallucination;
* every ```python block is executed by ``tests/blender_skills_test.py`` on the
  running Blender version, so a renamed socket (``Transmission`` ->
  ``Transmission Weight``) is caught before a user ever hits it.

This module is deliberately bpy-free: parsing and matching can be tested
without Blender, and the add-on can import it from any host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SKILLS_DIRNAME = "skills"

# How many bodies may be pulled in for a single question. Every fetch costs one
# extra round trip to the model, so the budget stays deliberately small.
MAX_SKILL_LOADS = 2

# --- syntax -----------------------------------------------------------------

_FRONT_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
# ```skill\n<name>\n``` — how the model asks for a body...
_REQUEST_FENCE_RE = re.compile(
    r"```[ \t]*skill[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE
)
# ...with a plain line as a tolerated fallback.
_REQUEST_LINE_RE = re.compile(
    r"^[ \t>*#-]*SKILL:[ \t]*([A-Za-z0-9_.\-/]+)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9_.\-/]*\Z")
_CODE_LANGS = ("", "python", "py", "bpy")
_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


@dataclass(frozen=True)
class Skill:
    """One instruction pack. Immutable so caching it is safe."""

    name: str
    title: str
    description: str
    keywords: Tuple[str, ...]
    blender: str
    body: str
    path: str

    def code_blocks(self) -> List[str]:
        """Every python block of the body — what the test suite executes."""
        return [
            body
            for lang, body in _FENCE_RE.findall(self.body)
            if lang.lower() in _CODE_LANGS
        ]


# --- front matter -----------------------------------------------------------


def parse_document(text: str) -> Tuple[Dict[str, str], str]:
    """Split ``---`` front matter from the body: ``(fields, body)``."""
    match = _FRONT_RE.match(text or "")
    if match is None:
        return {}, text or ""
    fields: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip().strip("\"'")
    return fields, (text or "")[match.end():]


def version_numbers(value) -> Tuple[int, ...]:
    """``(5, 2, 2)`` from a tuple, ``bpy.app.version`` or ``"4.2"``."""
    if isinstance(value, (tuple, list)):
        parts = list(value)
    else:
        parts = re.findall(r"\d+", str(value or ""))
    out: List[int] = []
    for part in parts:
        try:
            out.append(int(part))
        except (TypeError, ValueError):
            break
    return tuple(out)


def version_satisfies(constraint: str, version) -> bool:
    """True when *version* matches *constraint* (``">=4.2"``). Empty = any.

    Only the parts named by the constraint are compared, so ``">=4.2"`` accepts
    ``5.2.2`` and ``"<=5.2"`` accepts ``5.2.2`` as well (the patch number is
    simply not part of the constraint).
    """
    text = (constraint or "").strip()
    if not text:
        return True
    operator = ">="
    for candidate in _OPERATORS:
        if text.startswith(candidate):
            operator = candidate
            text = text[len(candidate):].strip()
            break
    want = version_numbers(text)
    if not want:
        return True
    have = version_numbers(version)[: len(want)]
    if len(have) < len(want):
        have += (0,) * (len(want) - len(have))
    if operator == ">=":
        return have >= want
    if operator == "<=":
        return have <= want
    if operator == ">":
        return have > want
    if operator == "<":
        return have < want
    if operator == "!=":
        return have != want
    return have == want


# --- loading ----------------------------------------------------------------


def load_all(root, blender_version=None) -> List[Skill]:
    """Every valid skill under *root*, filtered by the Blender version.

    Files without a ``name:`` field are ignored, so a README can live next to
    the skills without becoming one.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    found: List[Skill] = []
    for path in sorted(root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, body = parse_document(text)
        name = fields.get("name", "").strip().lower()
        if not name:
            continue
        if blender_version is not None and not version_satisfies(
            fields.get("blender", ""), blender_version
        ):
            continue
        found.append(
            Skill(
                name=name,
                title=fields.get("title", "") or name,
                description=fields.get("description", "").strip(),
                keywords=tuple(
                    word.strip().lower()
                    for word in fields.get("keywords", "").split(",")
                    if word.strip()
                ),
                blender=fields.get("blender", "").strip(),
                body=body.strip(),
                path=str(path),
            )
        )
    found.sort(key=lambda skill: skill.name)
    return found


_CACHE: Dict[Tuple[str, Tuple[int, ...], float], List[Skill]] = {}


def _stamp(root) -> float:
    """Newest mtime under *root* — lets an edited skill invalidate the cache."""
    root = Path(root)
    newest = 0.0
    try:
        for path in root.rglob("*.md"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return 0.0
    return newest


def get_skills(root, blender_version=None) -> List[Skill]:
    """``load_all`` with a small cache keyed by path, version and mtime."""
    key = (str(Path(root)), version_numbers(blender_version), _stamp(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    skills = load_all(root, blender_version)
    _CACHE.clear()  # only the current directory matters at runtime
    _CACHE[key] = skills
    return skills


def reset_cache() -> None:
    _CACHE.clear()


# --- helpers for the prompt / the pump --------------------------------------


def catalog(skills: Sequence[Skill]) -> str:
    """``- name: description`` lines for the system prompt."""
    lines = []
    for skill in skills:
        detail = skill.description or skill.title
        lines.append(f"- {skill.name}: {detail}")
    return "\n".join(lines)


def by_name(skills: Sequence[Skill], name: str) -> Optional[Skill]:
    wanted = (name or "").strip().lower()
    for skill in skills:
        if skill.name == wanted:
            return skill
    return None


def parse_skill_requests(text: str) -> List[str]:
    """Skill names requested in *text*, in order, without duplicates."""
    names: List[str] = []

    def remember(raw: str) -> None:
        candidate = (raw or "").strip().strip("`").strip().strip("*").strip()
        if not candidate:
            return
        candidate = candidate.splitlines()[0].strip().strip("`").lower()
        if _NAME_RE.match(candidate) and candidate not in names:
            names.append(candidate)

    for block in _REQUEST_FENCE_RE.findall(text or ""):
        remember(block)
    for line in _REQUEST_LINE_RE.findall(text or ""):
        remember(line)
    return names


def strip_skill_requests(text: str) -> str:
    """*text* without request blocks — keeps them out of history and code."""
    if not text:
        return ""
    cleaned = _REQUEST_FENCE_RE.sub("", text)
    cleaned = _REQUEST_LINE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
