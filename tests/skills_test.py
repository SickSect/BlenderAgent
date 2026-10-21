# tests/skills_test.py
"""Pure-python checks for the add-on's skill library.

``skills.py`` is deliberately bpy-free, so it can be imported here by path —
without Blender and without importing the add-on package (whose ``__init__``
needs ``bpy``).

The companion ``tests/run_skills_check.py`` runs *inside* Blender and executes
every ```python block of every skill; that is what catches an API rename.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_PY = ROOT / "src" / "blender_addon" / "skills.py"
SKILLS_DIR = SKILLS_PY.parent / "skills"

_spec = importlib.util.spec_from_file_location("gaa_skills_under_test", SKILLS_PY)
skills = importlib.util.module_from_spec(_spec)
sys.modules["gaa_skills_under_test"] = skills  # dataclasses look the module up
_spec.loader.exec_module(skills)

# The version the add-on ships against; keep in sync with blender_manifest.toml.
BLENDER = (5, 2, 0)

# A skill is attached to the prompt of every request that matches it, so its
# size is paid on every such request. The limit keeps the whole prompt inside
# the context window: the system prompt, the tool catalogue and the snapshot
# take ~12000 characters, and a skill of 12 kB left the model 3000 tokens for
# the actual work.
MAX_SKILL_CHARS = 7000


def library():
    skills.reset_cache()
    return skills.get_skills(SKILLS_DIR, BLENDER)


# -- front matter -----------------------------------------------------------


def test_parse_document():
    fields, body = skills.parse_document("---\nname: x\ntitle: T\nblender: \">=4.2\"\n---\nBody\n")
    assert fields["name"] == "x"
    assert fields["title"] == "T"
    assert fields["blender"] == ">=4.2"  # quotes stripped
    assert body.strip() == "Body"


def test_parse_document_without_front_matter():
    fields, body = skills.parse_document("plain markdown")
    assert fields == {}
    assert body == "plain markdown"


def test_readme_without_name_is_not_a_skill(tmp_path):
    (tmp_path / "README.md").write_text("# just docs\n", encoding="utf-8")
    (tmp_path / "real.md").write_text(
        "---\nname: real\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    loaded = skills.load_all(tmp_path, BLENDER)
    assert [skill.name for skill in loaded] == ["real"]


# -- versions ---------------------------------------------------------------


def test_version_satisfies():
    assert skills.version_satisfies(">=4.2", (5, 2, 0))
    assert skills.version_satisfies(">=4.2", (4, 2, 0))
    assert skills.version_satisfies("", (3, 0, 0))  # unset means "always"
    assert not skills.version_satisfies(">=4.2", (3, 6, 0))
    assert skills.version_satisfies(">4.2", (4, 3, 0))
    assert not skills.version_satisfies(">4.2", (4, 2, 0))
    assert skills.version_satisfies("<=5.2", (5, 2, 2))


def test_version_is_read_from_a_tuple_or_a_string():
    assert skills.version_numbers((5, 2, 2)) == (5, 2, 2)
    assert skills.version_numbers("4.2") == (4, 2)
    assert skills.version_numbers(None) == ()


# -- requests ---------------------------------------------------------------


def test_fenced_request():
    text = "Мне нужна инструкция\n```skill\nmaterial-glass\n```\nи продолжу"
    assert skills.parse_skill_requests(text) == ["material-glass"]


def test_line_request_and_dedup():
    text = "SKILL: material-glass\n```skill\nmaterial-glass\n```"
    assert skills.parse_skill_requests(text) == ["material-glass"]


def test_uppercase_and_spaces_are_tolerated():
    assert skills.parse_skill_requests("```SKILL\n  material-glass  \n```") == ["material-glass"]


def test_python_block_is_not_a_request():
    assert skills.parse_skill_requests("```python\nprint(1)\n```") == []
    assert skills.parse_skill_requests("```\nmaterial-glass\n```") == []


def test_strip_requests():
    text = "До:\n```skill\nmaterial-glass\n```\nПосле"
    stripped = skills.strip_skill_requests(text)
    assert "skill" not in stripped
    assert stripped.startswith("До:")
    assert stripped.endswith("После")


# -- the real skills directory ---------------------------------------------


def test_skills_directory_loads():
    loaded = library()
    assert loaded, "no skills found"
    names = [skill.name for skill in loaded]
    assert len(names) == len(set(names)), f"duplicate skill names: {names}"


def test_every_skill_is_complete():
    for skill in library():
        assert skill.description, f"{skill.name}: empty description (catalog line)"
        assert skill.blender, f"{skill.name}: no blender constraint"
        assert skill.body, f"{skill.name}: empty body"
        assert skill.code_blocks(), f"{skill.name}: no ```python block to verify"


def test_the_skills_fit_in_the_context():
    """A skill is attached to *every* request, so it may not be fat.

    The whole prompt - the system prompt, the tool catalogue, the snapshot
    and the attached skill - has to fit into the context window. A skill of
    12 kB ate 80% of it and left the model 3000 tokens for the actual work.
    """
    for skill in library():
        assert len(skill.body) <= MAX_SKILL_CHARS, (
            f"{skill.name}: {len(skill.body)} characters, the limit is "
            f"{MAX_SKILL_CHARS}. Move the knowledge into the code (a tool) or "
            f"cut the prose."
        )


def test_catalog_lists_every_skill():
    loaded = library()
    lines = skills.catalog(loaded).splitlines()
    assert len(lines) == len(loaded)
    for skill in loaded:
        assert any(line.startswith(f"- {skill.name}:") for line in lines)


def test_by_name():
    loaded = library()
    assert skills.by_name(loaded, "MATERIAL-GLASS") is not None
    assert skills.by_name(loaded, "material-glass") is not None
    assert skills.by_name(loaded, "no-such-skill") is None


# -- the word matching ------------------------------------------------------


def test_keywords_survive_inflection():
    """A request in a Russian case must still find its keyword.

    "сделай текстуру дерева" found no recipe at all, and the model built
    bricks instead of wood.
    """
    assert skills.keyword_score("сделай текстуру дерева", "дерево")
    assert skills.keyword_score("деревянная текстура", "дерево")
    assert skills.keyword_score("стена из дерева", "дерево")
    assert skills.keyword_score("bricks", "brick")
    assert skills.keyword_score("a wooden plank", "wood")


def test_the_stem_is_not_too_short():
    """Three characters would match "стена" with "стекло" - a wall is not glass."""
    assert not skills.keyword_score("кирпичная стена", "стекло")
    assert not skills.keyword_score("бетонная стена", "стекло")


def test_the_generic_words_are_cut_out():
    """"текстура" starts like "текстиль" (fabric) - that must not match."""
    assert not skills.keyword_score("текстура ткани", "текстиль")
    assert skills.keyword_score("текстура ткани", "ткань")


def test_a_russian_request_attaches_the_skill():
    matched = skills.match(library(), "сделай текстуру дерева")
    assert matched, "the procedural skill was not attached"
    if matched:
        assert matched[0].name == "material-procedural"


def test_cache_returns_the_same_list_until_files_change():
    first = library()
    second = skills.get_skills(SKILLS_DIR, BLENDER)
    assert first is second


# -- keyword matching -------------------------------------------------------


def test_keyword_match_picks_the_obvious_recipe():
    loaded = library()
    picked = skills.match(loaded, "сделай стеклянный материал")
    assert [skill.name for skill in picked] == ["material-glass"]


def test_keyword_match_works_in_english():
    loaded = library()
    picked = skills.match(loaded, "make a chrome material")
    assert "material-metal-roughness" in [skill.name for skill in picked]


def test_generic_words_alone_do_not_pull_a_skill_in():
    loaded = library()
    # "texture" and "map" are weak keywords: they appear in almost every
    # material question and must never decide on their own.
    assert skills.match(loaded, "текстура") == []
    assert skills.match(loaded, "a texture") == []


def test_keyword_match_respects_word_boundaries():
    loaded = library()
    # "ao" must not match inside "board"; "map" must not match "mapping".
    assert skills.match(loaded, "board texture mapping") == []


def test_keyword_match_returns_nothing_for_an_empty_request():
    loaded = library()
    assert skills.match(loaded, "") == []
    assert skills.match(loaded, "   ") == []


def test_keyword_match_respects_the_limit():
    loaded = library()
    picked = skills.match(loaded, "стекло прозрачность металл roughness", limit=2)
    assert len(picked) <= 2


def test_score_keywords_orders_by_strength():
    loaded = library()
    scored = skills.score_keywords(loaded, "сделай стеклянный материал")
    assert scored
    scores = [score for score, _ in scored]
    assert scores == sorted(scores, reverse=True)
    assert scored[0][1].name == "material-glass"
