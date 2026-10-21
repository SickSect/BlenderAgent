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


# --- keyword matching -------------------------------------------------------

# Words that show up in almost any material question. They count, but only as
# a tie-breaker: alone they must never pull a skill in, otherwise every
# request about "a texture" would drag in the colour-space recipe.
_WEAK_KEYWORDS = frozenset(
    {
        "map", "maps", "texture", "textures", "data", "material", "color",
        "colour", "normal", "ao", "metal", "roughness", "albedo", "image",
        "bsdf", "socket", "карта", "текстура", "текстуры", "материал",
    }
)

# Minimum evidence before a skill is attached without asking the model. The
# score is the sum of the lengths of the matched (non-weak) keywords, so
# "стекло" (6) qualifies and "ao" (2) does not.
MIN_AUTO_SCORE = 5

# How many skills may be attached to a prompt automatically. Two: a request
# about a material matches both the "metal" recipe and the procedural one, and
# the better material comes from having both.
MAX_AUTO_SKILLS = 2

_KEYWORD_PATTERNS: Dict[str, "re.Pattern[str]"] = {}

# Words that name no material. They are cut out of the text before the match,
# because they start like the words that do: "текстура" starts like
# "текстиль" (fabric) and "материал" like "материал". Left in, they send the
# search to the wrong recipe - that is how "текстуру дерева" found fabric.
_GENERIC_RE = re.compile(
    r"(?<!\w)(?:текстур\w*|материал\w*|поверхност\w*|textur\w*|material\w*|surface\w*)",
    re.IGNORECASE,
)


def strip_generic(text: str) -> str:
    """The text without the words that name no material."""
    return _GENERIC_RE.sub(" ", text or "")


def keyword_stem(keyword: str) -> str:
    """The beginning of a keyword, long enough to survive inflection.

    Russian changes the ending ("дерево" -> "дерева"), English adds one
    ("brick" -> "bricks"). A match on the whole word with word boundaries
    misses both - and that is how "сделай текстуру дерева" ended up building
    bricks: the recipe was not found, so nothing guided the model.

    The stem is four characters (three for a four-letter word): long enough
    to be specific, short enough to survive a case ending. Three would be
    shorter still, but "сте" would then match "стена" - a wall is not glass.
    """
    if len(keyword) <= 3:
        return keyword
    if len(keyword) == 4:
        return keyword[:3]
    return keyword[:4]


def _keyword_re(keyword: str):
    """Word-boundary pattern for *keyword* (cached).

    The boundaries matter: "ao" must not match inside "board", and "map"
    must not match inside "mapping".
    """
    pattern = _KEYWORD_PATTERNS.get(keyword)
    if pattern is None:
        pattern = re.compile(
            r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", re.IGNORECASE
        )
        _KEYWORD_PATTERNS[keyword] = pattern
    return pattern


def keyword_score(text: str, keyword: str) -> int:
    """How strongly *keyword* is present in *text* (0 = absent).

    A whole word with word boundaries scores the length of the keyword. A
    match by the stem alone scores the length of the *stem* (plus a little):
    the length of the keyword would let "металл с царапинами" beat the exact
    "чистый металл", which is nonsense.
    """
    if not text or not keyword or len(keyword) < 2:
        return 0
    text = strip_generic(text)
    if _keyword_re(keyword).search(text):
        return len(keyword)
    stem = keyword_stem(keyword)
    if stem == keyword:
        return 0
    pattern = _KEYWORD_PATTERNS.get(f"~{stem}")
    if pattern is None:
        pattern = re.compile(r"(?<!\w)" + re.escape(stem), re.IGNORECASE)
        _KEYWORD_PATTERNS[f"~{stem}"] = pattern
    return len(stem) + 2 if pattern.search(text) else 0


def keyword_in(text: str, keyword: str) -> bool:
    """True when *keyword* is in *text* as a word - or as an inflected form."""
    return keyword_score(text, keyword) > 0


def score_keywords(skills: Sequence[Skill], text: str) -> List[Tuple[int, Skill]]:
    """``(score, skill)`` pairs for the skills whose keywords appear in *text*.

    Sorted strongest first; ties broken by name so the result is stable.
    """
    if not text:
        return []
    text = strip_generic(text)
    scored: List[Tuple[int, Skill]] = []
    for skill in skills:
        score = 0
        for keyword in skill.keywords:
            if len(keyword) < 2:
                continue
            hit = keyword_score(text, keyword)
            if not hit:
                continue
            score += 1 if keyword.lower() in _WEAK_KEYWORDS else hit
        if score:
            scored.append((score, skill))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return scored


def match(
    skills: Sequence[Skill],
    text: str,
    limit: int = MAX_AUTO_SKILLS,
    min_score: int = MIN_AUTO_SCORE,
) -> List[Skill]:
    """Skills to attach to the prompt *without* a second round trip.

    The model may always ask for a skill explicitly (that path is unchanged);
    this is the shortcut for the common case where the user's own words
    already say which recipe they need.
    """
    picked: List[Skill] = []
    for score, skill in score_keywords(skills, text):
        if score < min_score:
            break
        picked.append(skill)
        if len(picked) >= limit:
            break
    return picked


def strip_skill_requests(text: str) -> str:
    """*text* without request blocks — keeps them out of history and code."""
    if not text:
        return ""
    cleaned = _REQUEST_FENCE_RE.sub("", text)
    cleaned = _REQUEST_LINE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
