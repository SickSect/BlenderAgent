"""Procedural materials built from a description.

A procedural material is a graph: coordinates go into a texture, the texture
goes through a colour ramp, and the result lands in the Principled BSDF. Every
artist writes that graph by hand - and every artist writes it slightly
differently, which is why two "wood" materials never look the same.

This module turns a **specification** into that graph. A specification is
plain data:

    pattern, scale, detail, stretch, colours, roughness, bump...

Two things use it:

* the **recipes** - ready-made specifications for the materials everybody
  needs (wood, marble, rust, fabric...), each with keywords so that a plain
  request ("a dark wooden surface") finds the right one;
* the **model** - it reads the user's description, picks a recipe and adjusts
  the parameters, and the graph is built here, deterministically.

That split matters. The model is good at understanding "dark, glossy, worn
wood" and bad at writing a hundred lines of node code without a typo. Here the
model only produces *numbers*, and the code does the rest - no API errors, no
forgotten sockets, the same result every time.
"""

from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import bpy

from . import api, materials, skills, tools

# The patterns the builder knows.
PATTERNS = ("noise", "voronoi", "wave", "checker", "gradient", "brick")

# The parameters that can be given per material.
PARAMETERS = (
    "pattern",
    "scale",
    "detail",
    "distortion",
    "stretch",
    "color_a",
    "color_b",
    "color_mid",
    "metallic",
    "roughness",
    "roughness_variation",
    "bump_strength",
    "specular",
    "emission",
    "emission_strength",
    "transmission",
    "ior",
    # The layout of the brick pattern.
    "mortar_size",
    "mortar_smooth",
    "brick_width",
    "row_height",
    "brick_bias",
)


def _color(value, default=(0.5, 0.5, 0.5, 1.0)) -> Tuple[float, float, float, float]:
    """A colour from a tuple, a list or a ``#rrggbb`` string."""
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) in (6, 8):
            try:
                parts = [int(text[i:i + 2], 16) / 255.0 for i in range(0, len(text), 2)]
            except ValueError:
                return default
            while len(parts) < 4:
                parts.append(1.0)
            return tuple(parts)
        return default
    if isinstance(value, (list, tuple)):
        parts = [float(v) for v in value[:4]]
        while len(parts) < 4:
            parts.append(1.0)
        return tuple(parts)
    return default


@dataclass
class ProcSpec:
    """Everything the builder needs to know about one material."""

    name: str = "Material"
    pattern: str = "noise"
    scale: float = 5.0
    detail: float = 2.0
    distortion: float = 0.0
    stretch: Tuple[float, float] = (1.0, 1.0)
    color_a: Tuple[float, float, float, float] = (0.15, 0.15, 0.15, 1.0)
    color_b: Tuple[float, float, float, float] = (0.85, 0.85, 0.85, 1.0)
    color_mid: Optional[Tuple[float, float, float, float]] = None
    metallic: float = 0.0
    roughness: float = 0.5
    roughness_variation: float = 0.0
    bump_strength: float = 0.0
    specular: float = 0.5
    emission: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    emission_strength: float = 0.0
    transmission: float = 0.0
    ior: float = 1.45
    # The layout of the brick pattern. For the brick pattern the colours mean:
    # color_a = the brick, color_mid = the second brick tone (the per-brick
    # variation), color_b = the mortar.
    mortar_size: float = 0.02
    mortar_smooth: float = 0.1
    brick_width: float = 0.5
    row_height: float = 0.25
    brick_bias: float = 0.0

    def normalized(self) -> "ProcSpec":
        """The spec with everything brought into a sane range."""
        spec = replace(self)
        if spec.pattern not in PATTERNS:
            spec.pattern = "noise"
        spec.scale = max(0.001, float(spec.scale))
        spec.detail = max(0.0, float(spec.detail))
        spec.distortion = max(0.0, float(spec.distortion))
        spec.stretch = (
            max(0.001, float(spec.stretch[0])),
            max(0.001, float(spec.stretch[1])),
        )
        for field_name in ("metallic", "roughness", "bump_strength", "specular",
                           "transmission"):
            setattr(spec, field_name, max(0.0, min(1.0, float(getattr(spec, field_name)))))
        spec.roughness_variation = max(0.0, min(1.0, float(spec.roughness_variation)))
        spec.emission_strength = max(0.0, float(spec.emission_strength))
        spec.ior = max(1.0, float(spec.ior))
        spec.mortar_size = max(0.0, min(0.5, float(spec.mortar_size)))
        spec.mortar_smooth = max(0.0, min(1.0, float(spec.mortar_smooth)))
        spec.brick_width = max(0.01, float(spec.brick_width))
        spec.row_height = max(0.01, float(spec.row_height))
        spec.brick_bias = max(0.0, min(1.0, float(spec.brick_bias)))
        spec.color_a = _color(spec.color_a)
        spec.color_b = _color(spec.color_b)
        if spec.color_mid is not None:
            spec.color_mid = _color(spec.color_mid)
        return spec


@dataclass
class Recipe:
    """A named specification with keywords."""

    name: str
    title: str
    description: str
    keywords: Tuple[str, ...]
    spec: ProcSpec


def _recipe(name, title, description, keywords, **kwargs) -> Recipe:
    return Recipe(
        name=name,
        title=title,
        description=description,
        keywords=tuple(keywords),
        spec=ProcSpec(name=title, **kwargs),
    )


# The catalogue. Every entry is a plain specification - nothing here knows
# anything about nodes.
RECIPES: Dict[str, Recipe] = {}


def _add(recipe: Recipe) -> None:
    RECIPES[recipe.name] = recipe


_add(_recipe(
    "wood", "Wood", "Stretched grain with a strong bump",
    ("wood", "tree", "timber", "plank", "oak", "pine", "дерево", "доска"),
    pattern="noise", scale=3.0, detail=6.0, distortion=1.5,
    stretch=(1.0, 12.0),
    color_a=(0.09, 0.05, 0.02, 1.0), color_b=(0.42, 0.26, 0.13, 1.0),
    roughness=0.55, roughness_variation=0.25, bump_strength=0.35,
))
_add(_recipe(
    "marble", "Marble", "Soft veins with a polished surface",
    ("marble", "vein", "мрамор"),
    pattern="noise", scale=2.0, detail=8.0, distortion=6.0,
    color_a=(0.75, 0.75, 0.73, 1.0), color_b=(0.12, 0.12, 0.13, 1.0),
    roughness=0.18, roughness_variation=0.08, bump_strength=0.05,
))
_add(_recipe(
    "stone", "Stone", "Chunky speckled rock",
    # "камня" is spelled out: the stem of "камень" does not survive the
    # Russian case ("камень" -> "камня"), and a request for stone is the
    # most common one in this whole catalogue.
    ("stone", "rock", "granite", "камень", "камня", "бетон", "гранит"),
    pattern="voronoi", scale=8.0, detail=4.0,
    color_a=(0.22, 0.21, 0.20, 1.0), color_b=(0.55, 0.54, 0.52, 1.0),
    roughness=0.75, roughness_variation=0.2, bump_strength=0.3,
))
_add(_recipe(
    "concrete", "Concrete", "Flat rough surface with fine noise",
    ("concrete", "plaster", "cement", "бетон", "штукатурка"),
    pattern="noise", scale=12.0, detail=3.0,
    color_a=(0.48, 0.48, 0.47, 1.0), color_b=(0.62, 0.62, 0.61, 1.0),
    roughness=0.85, roughness_variation=0.1, bump_strength=0.12,
))
_add(_recipe(
    "rust", "Rust", "Corroded metal",
    ("rust", "corroded", "oxide", "rusty", "ржавчина", "окисление"),
    pattern="voronoi", scale=6.0, detail=6.0,
    color_a=(0.28, 0.10, 0.03, 1.0), color_b=(0.62, 0.30, 0.10, 1.0),
    metallic=0.55, roughness=0.82, roughness_variation=0.3, bump_strength=0.4,
))
_add(_recipe(
    "metal_brushed", "Brushed metal", "Streaked metal surface",
    ("metal", "brushed", "steel", "aluminium", "aluminum", "chrome",
     "металл", "сталь", "алюминий"),
    pattern="noise", scale=4.0, detail=4.0, stretch=(1.0, 40.0),
    color_a=(0.55, 0.56, 0.58, 1.0), color_b=(0.72, 0.73, 0.75, 1.0),
    metallic=1.0, roughness=0.32, roughness_variation=0.12, bump_strength=0.08,
))
_add(_recipe(
    "fabric", "Fabric", "Soft woven cloth",
    ("fabric", "cloth", "textile", "canvas", "ткань", "холст", "текстиль"),
    pattern="wave", scale=60.0, detail=2.0,
    color_a=(0.35, 0.33, 0.30, 1.0), color_b=(0.48, 0.46, 0.42, 1.0),
    roughness=0.9, roughness_variation=0.05, bump_strength=0.15,
))
_add(_recipe(
    "leather", "Leather", "Pebbled surface",
    ("leather", "кожа"),
    pattern="voronoi", scale=22.0, detail=4.0,
    color_a=(0.10, 0.06, 0.04, 1.0), color_b=(0.26, 0.16, 0.10, 1.0),
    roughness=0.62, roughness_variation=0.2, bump_strength=0.45,
))
_add(_recipe(
    "paper", "Paper", "Fine fibrous sheet",
    ("paper", "cardboard", "бумага", "картон"),
    pattern="noise", scale=90.0, detail=2.0,
    color_a=(0.82, 0.80, 0.75, 1.0), color_b=(0.90, 0.89, 0.85, 1.0),
    roughness=0.92, roughness_variation=0.03, bump_strength=0.05,
))
_add(_recipe(
    "snow", "Snow", "Soft bright drifts",
    ("snow", "ice", "снег", "лёд", "лед"),
    pattern="noise", scale=4.0, detail=8.0, distortion=2.0,
    color_a=(0.72, 0.78, 0.86, 1.0), color_b=(0.95, 0.97, 1.0, 1.0),
    roughness=0.65, roughness_variation=0.15, bump_strength=0.3,
))
_add(_recipe(
    "lava", "Lava", "Glowing cracks",
    ("lava", "magma", "volcanic", "лава", "магма"),
    pattern="voronoi", scale=4.0, detail=6.0,
    color_a=(0.35, 0.02, 0.0, 1.0), color_b=(1.0, 0.45, 0.05, 1.0),
    roughness=0.7, roughness_variation=0.2, bump_strength=0.25,
    emission=(1.0, 0.35, 0.05, 1.0), emission_strength=4.0,
))
_add(_recipe(
    "plastic", "Plastic", "Smooth coloured plastic",
    ("plastic", "toy", "пластик", "пластмасса"),
    pattern="noise", scale=6.0, detail=1.0,
    color_a=(0.05, 0.25, 0.55, 1.0), color_b=(0.08, 0.35, 0.70, 1.0),
    roughness=0.35, roughness_variation=0.02,
))
_add(_recipe(
    "checker", "Checker", "Chequerboard",
    ("checker", "checkered", "шахмат", "клетчатый"),
    pattern="checker", scale=10.0,
    color_a=(0.05, 0.05, 0.05, 1.0), color_b=(0.9, 0.9, 0.9, 1.0),
    roughness=0.6,
))
_add(_recipe(
    "brick", "Brick", "Brick wall with mortar joints",
    ("brick", "кирпич", "кладка", "кирпичная", "стена", "wall", "masonry",
     "кирпичный", "кладка кирпича"),
    pattern="brick", scale=6.0,
    # color_a is the brick, color_mid is the second brick tone (the per-brick
    # variation) and color_b is the mortar.
    color_a=(0.42, 0.16, 0.10, 1.0), color_mid=(0.55, 0.24, 0.15, 1.0),
    color_b=(0.62, 0.60, 0.56, 1.0),
    mortar_size=0.02, mortar_smooth=0.1,
    brick_width=0.5, row_height=0.25,
    roughness=0.75, bump_strength=0.5,
))
_add(_recipe(
    "tile", "Tile", "Square tiles with thin grout lines",
    ("tile", "tiles", "плитка", "плиточ", "кафель", "черепица"),
    pattern="brick", scale=8.0,
    color_a=(0.62, 0.64, 0.63, 1.0), color_mid=(0.70, 0.72, 0.70, 1.0),
    color_b=(0.35, 0.35, 0.34, 1.0),
    mortar_size=0.008, mortar_smooth=0.0,
    brick_width=0.5, row_height=0.5,   # square tiles: the width equals the height
    roughness=0.28, bump_strength=0.2,
))
_add(_recipe(
    "cobble", "Cobblestone", "Rounded stones with deep joints",
    ("cobble", "cobblestone", "булыжник", "мостовая", "газон"),
    pattern="brick", scale=4.0,
    color_a=(0.38, 0.36, 0.33, 1.0), color_mid=(0.48, 0.45, 0.41, 1.0),
    color_b=(0.20, 0.19, 0.17, 1.0),
    mortar_size=0.06, mortar_smooth=0.3,
    brick_width=0.6, row_height=0.35, brick_bias=0.2,
    roughness=0.85, bump_strength=0.6,
))


# --- matching --------------------------------------------------------------

# Words that mean nothing when matching (they appear in every request).
_WEAK_WORDS = frozenset(
    {"a", "an", "the", "make", "create", "material", "with", "and", "for",
     "сделай", "материал", "и", "для", "на"}
)


def match_recipes(text: str, limit: int = 3) -> List[Tuple[Recipe, int]]:
    """The recipes whose keywords appear in *text*, best first.

    Returns ``(recipe, score)`` pairs. The score is the sum of the lengths of
    the matched keywords, so a specific word ("wood") beats a generic one.

    The match survives the Russian cases: "текстуру дерева" finds the wood
    recipe, because the keyword "дерево" is matched by its stem.
    """
    if not text:
        return []
    lowered = skills.strip_generic(text.lower())
    scored = []
    for recipe in RECIPES.values():
        score = 0
        for keyword in recipe.keywords:
            if keyword in _WEAK_WORDS:
                continue
            score += skills.keyword_score(lowered, keyword)
        if score:
            scored.append((recipe, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].name))
    return scored[:limit]


# --- the builder -----------------------------------------------------------


def _pattern_node(tree, pattern: str, location):
    """The texture node of the requested pattern."""
    if pattern == "voronoi":
        node = tree.nodes.new("ShaderNodeTexVoronoi")
    elif pattern == "wave":
        node = tree.nodes.new("ShaderNodeTexWave")
    elif pattern == "checker":
        node = tree.nodes.new("ShaderNodeTexChecker")
    elif pattern == "gradient":
        node = tree.nodes.new("ShaderNodeTexGradient")
    elif pattern == "brick":
        node = tree.nodes.new("ShaderNodeTexBrick")
    else:
        node = tree.nodes.new("ShaderNodeTexNoise")
    node.location = location
    return node


def _brick_factor(node):
    """The mortar mask of the brick node: 1 in the mortar, 0 in the brick.

    The output was called "Fac" up to Blender 4.x and is called "Factor" in
    5.x, so both names are tried.
    """
    for name in ("Factor", "Fac"):
        socket = node.outputs.get(name)
        if socket is not None:
            return socket
    return node.outputs[-1]


def _set_input(node, name, value) -> bool:
    socket = node.inputs.get(name)
    if socket is None:
        return False
    try:
        socket.default_value = value
    except (TypeError, ValueError):
        return False
    return True


def _fac_output(node):
    """The scalar output of a texture node.

    Not every pattern has a "Fac": the Voronoi texture reports a "Distance"
    instead, and the checker and the gradient have no scalar at all - their
    "Color" is the value.
    """
    for name in ("Fac", "Distance"):
        socket = node.outputs.get(name)
        if socket is not None:
            return socket
    return node.outputs[0]


def build_material(spec: ProcSpec) -> bpy.types.Material:
    """Build the material described by *spec* and return it."""
    spec = spec.normalized()
    material = bpy.data.materials.new(spec.name)
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links

    bsdf = api.find_principled(tree)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Principled BSDF"
    bsdf.location = (200, 0)

    # Coordinates -> mapping -> the texture itself.
    coords = nodes.new("ShaderNodeTexCoord")
    coords.name = "Texture Coordinate"
    coords.location = (-1100, 0)

    mapping = nodes.new("ShaderNodeMapping")
    mapping.name = "Mapping"
    mapping.location = (-900, 0)
    # The Scale socket is a 3D vector: the third axis stays neutral.
    mapping.inputs["Scale"].default_value = (
        spec.stretch[0],
        spec.stretch[1],
        1.0,
    )

    texture = _pattern_node(tree, spec.pattern, (-700, 0))
    texture.name = f"{spec.pattern.capitalize()} Texture"
    _set_input(texture, "Scale", spec.scale)
    if spec.pattern in ("noise", "voronoi", "wave"):
        _set_input(texture, "Detail", spec.detail)
    if spec.pattern in ("noise", "voronoi", "wave"):
        _set_input(texture, "Distortion", spec.distortion)

    links.new(coords.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], texture.inputs["Vector"])

    # The colour ramp turns the texture into the two colours of the material.
    # The brick, the checker and the gradient do their own colouring, so for
    # them no ramp is created at all - a dead node in the graph is a dead node
    # the user has to look at.
    ramp = None
    if spec.pattern not in ("brick", "checker", "gradient"):
        ramp = nodes.new("ShaderNodeValToRGB")
        ramp.name = "Color Ramp"
        ramp.location = (-450, 150)
        elements = ramp.color_ramp.elements
        elements[0].position = 0.25
        elements[0].color = spec.color_a
        elements[1].position = 0.75
        elements[1].color = spec.color_b
        if spec.color_mid is not None:
            middle = elements.new(0.5)
            middle.color = spec.color_mid

    if spec.pattern == "brick":
        # The brick node does the colouring itself: it knows the bricks, the
        # mortar and the per-brick variation. A ramp would only blur it.
        _set_input(texture, "Color1", spec.color_a)
        _set_input(texture, "Color2", spec.color_mid or spec.color_a)
        _set_input(texture, "Mortar", spec.color_b)
        _set_input(texture, "Mortar Size", spec.mortar_size)
        _set_input(texture, "Mortar Smooth", spec.mortar_smooth)
        _set_input(texture, "Bias", spec.brick_bias)
        _set_input(texture, "Brick Width", spec.brick_width)
        _set_input(texture, "Row Height", spec.row_height)
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    elif spec.pattern in ("checker", "gradient"):
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        links.new(_fac_output(texture), ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])

    # Roughness may vary with the texture.
    if spec.pattern == "brick":
        # The mortar is rougher than the brick. The brick node's factor is 1
        # in the mortar and 0 in the brick, so it drives the range directly.
        rough = nodes.new("ShaderNodeMapRange")
        rough.name = "Mortar Roughness"
        rough.location = (-450, -150)
        rough.inputs["To Min"].default_value = max(0.0, spec.roughness - 0.05)
        rough.inputs["To Max"].default_value = min(1.0, spec.roughness + 0.2)
        links.new(_brick_factor(texture), rough.inputs["Value"])
        links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    elif spec.roughness_variation > 0.0:
        rough = nodes.new("ShaderNodeMapRange")
        rough.name = "Roughness Variation"
        rough.location = (-450, -150)
        rough.inputs["To Min"].default_value = max(
            0.0, spec.roughness - spec.roughness_variation * 0.5
        )
        rough.inputs["To Max"].default_value = min(
            1.0, spec.roughness + spec.roughness_variation * 0.5
        )
        links.new(_fac_output(texture), rough.inputs["Value"])
        links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = spec.roughness

    # Bump from the same texture.
    if spec.pattern == "brick":
        # The mortar sits lower than the bricks: the height is the inverted
        # mortar mask, so the bricks are high and the joints are recessed.
        height = nodes.new("ShaderNodeMapRange")
        height.name = "Mortar Height"
        height.location = (-450, -350)
        height.inputs["To Min"].default_value = 1.0
        height.inputs["To Max"].default_value = 0.0
        links.new(_brick_factor(texture), height.inputs["Value"])

        bump = nodes.new("ShaderNodeBump")
        bump.name = "Bump"
        bump.location = (-250, -350)
        bump.inputs["Strength"].default_value = spec.bump_strength
        links.new(height.outputs["Result"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    elif spec.bump_strength > 0.0:
        bump = nodes.new("ShaderNodeBump")
        bump.name = "Bump"
        bump.location = (-250, -350)
        bump.inputs["Strength"].default_value = spec.bump_strength
        links.new(_fac_output(texture), bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    # The surface itself.
    bsdf.inputs["Metallic"].default_value = spec.metallic
    api.set_input(bsdf, spec.specular, "Specular IOR Level", "Specular")
    api.set_input(bsdf, spec.transmission, "Transmission Weight", "Transmission")
    api.set_input(bsdf, spec.ior, "IOR")
    if spec.emission_strength > 0.0:
        api.set_input(
            bsdf, spec.emission, "Emission Color", "Emission"
        )
        api.set_input(bsdf, spec.emission_strength, "Emission Strength")

    return material


def spec_for(recipe_name: str, name: str = "", **overrides) -> Optional[ProcSpec]:
    """The specification of a recipe with the overrides applied.

    ``None`` when there is no such recipe. The tool uses it to report what
    was actually built - the model has to be able to see that its parameters
    arrived.
    """
    recipe = RECIPES.get((recipe_name or "").strip().lower())
    if recipe is None:
        return None
    spec = replace(recipe.spec)
    spec.name = name or recipe.title
    for key, value in overrides.items():
        if key in PARAMETERS and value is not None:
            setattr(spec, key, value)
    return spec.normalized()


def build_from_recipe(recipe_name: str, name: str = "", **overrides) -> Optional[bpy.types.Material]:
    """Build a material from a named recipe, with the given overrides."""
    spec = spec_for(recipe_name, name, **overrides)
    if spec is None:
        return None
    return build_material(spec)


# --- the tools -------------------------------------------------------------


@tools.tool(
    "procedural.list",
    "List the procedural recipes",
    "List the ready-made procedural material recipes (wood, marble, rust, "
    "fabric...) with what each one is for.",
)
def list_tool(**kwargs) -> str:
    """Tool entry point: the recipe catalogue.

    The pattern is part of the line on purpose: the model has to know that
    "brick" is a structure and not noise, and the catalogue is where it
    learns which is which.
    """
    lines = ["Procedural material recipes (pattern | scale | roughness | bump):"]
    for recipe in sorted(RECIPES.values(), key=lambda r: r.name):
        keywords = ", ".join(recipe.keywords[:6])
        spec = recipe.spec
        lines.append(
            f"- {recipe.name}: {recipe.description} "
            f"[pattern={spec.pattern}, scale={spec.scale:g}, "
            f"roughness={spec.roughness:g}, bump={spec.bump_strength:g}] "
            f"(keywords: {keywords})"
        )
    # The author's own materials: the graphs a small model could never invent.
    # The model is much better at choosing one of them and turning its numbers
    # than at building a graph from scratch.
    try:
        from . import presets

        catalogue = presets.catalog()
    except Exception:  # noqa: BLE001 - the presets are optional
        catalogue = ""
    if catalogue:
        lines.append("")
        lines.append(catalogue)
    return "\n".join(lines)


def closest_recipes(name: str, limit: int = 3) -> List[Recipe]:
    """The recipes whose names are the closest to *name*.

    Used to answer "there is no recipe called X" with something useful
    instead of a bare list of every name.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return []
    matches = difflib.get_close_matches(wanted, list(RECIPES), n=limit, cutoff=0.7)
    return [RECIPES[key] for key in matches]


def _structure_hint(name: str) -> Optional[Recipe]:
    """The recipe that fits a material name better than noise does.

    A material called "BrickWall" built with noise is white smoke: the name
    says what the material is, and the catalogue knows the pattern for it.
    """
    if not name:
        return None
    matches = match_recipes(name, limit=1)
    if not matches:
        return None
    recipe = matches[0][0]
    if recipe.spec.pattern == "noise":
        return None  # noise is the right answer for this one
    return recipe


def _describe(spec: ProcSpec) -> str:
    """The report of what was built, in one line.

    The model reads this line: it has to be able to see that its parameters
    arrived, or it will happily believe that it set the colours.
    """
    text = (
        f"(pattern={spec.pattern}, scale={spec.scale:g}, "
        f"roughness={spec.roughness:g}, bump={spec.bump_strength:g})"
    )
    hint = _structure_hint(spec.name)
    if hint is not None:
        text += (
            f" Note: '{spec.name}' sounds like '{hint.name}' - that recipe uses "
            f"pattern={hint.spec.pattern}, which is what a surface needs. Noise "
            f"alone reads as clouds."
        )
    return text


def _install(material) -> str:
    """Put the new material on the active object; return its name or "".

    A material the user cannot see is a material that did not happen: the
    model says "the material is ready" and the object still wears the old
    one. The builder therefore installs it right away, the same way the
    "PBR from folder" button does.
    """
    obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return ""
    return materials.assign_material(obj, material)


def _default_name(recipe: str) -> str:
    """A name for a material the model did not name: 'Wood_Procedural'.

    The marker says what the material is *and* that it is a graph. The baked
    twin drops it and is called 'Wood_Bake' - the word "procedural" never
    survives into the result.
    """
    title = " ".join(part.capitalize() for part in str(recipe).split("_"))
    return f"{title}_Procedural"


@tools.tool(
    "procedural.find",
    "Find a procedural recipe by description",
    "Find the procedural recipes that match a description: the words of the "
    "request are matched against the recipe keywords and the closest recipes "
    "come with their pattern. Use it when the user names a material that you "
    "are not sure about - do not guess the recipe name.",
    params=("query",),
)
def find_tool(query=None, **kwargs) -> str:
    """Tool entry point: find the recipes that match the user's words."""
    text = str(query or "")
    matches = match_recipes(text, limit=4)
    if not matches:
        return (
            f"Nothing matches '{text}'. The known recipes are: "
            f"{', '.join(sorted(RECIPES))}."
        )
    lines = [f"Recipes that match '{text}':"]
    for recipe, _score in matches:
        spec = recipe.spec
        lines.append(
            f"- {recipe.name}: {recipe.description} "
            f"[pattern={spec.pattern}, scale={spec.scale:g}, "
            f"roughness={spec.roughness:g}, bump={spec.bump_strength:g}]"
        )
    return "\n".join(lines)


@tools.tool(
    "procedural.build",
    "Build a procedural material",
    "Build a procedural material from a recipe or from explicit parameters: "
    "the pattern (noise/voronoi/wave/checker/brick), the scale, the two or three "
    "colours, the roughness and the bump. The graph is built by the add-on, so "
    "it always comes out valid. For the brick pattern color_a is the brick, "
    "color_b is the mortar and color_mid is the second brick tone. A recipe can "
    "also be given as a plain description: it is matched against the keywords.",
    params=("recipe", "query", "name", "pattern", "color_a", "color_b", "color_mid",
            "scale", "detail", "distortion", "roughness", "roughness_variation",
            "metallic", "bump_strength", "mortar_size", "brick_width",
            "row_height"),
)
def build_tool(
    recipe=None, query=None, name="", pattern=None, color_a=None, color_b=None,
    color_mid=None, scale=None, detail=None, distortion=None, roughness=None,
    roughness_variation=None, metallic=None, bump_strength=None,
    mortar_size=None, brick_width=None, row_height=None, **kwargs
) -> str:
    """Tool entry point: build a procedural material."""
    overrides = {
        "pattern": pattern,
        "color_a": color_a,
        "color_b": color_b,
        "color_mid": color_mid,
        "scale": scale,
        "detail": detail,
        "distortion": distortion,
        "roughness": roughness,
        "roughness_variation": roughness_variation,
        "metallic": metallic,
        "bump_strength": bump_strength,
        "mortar_size": mortar_size,
        "brick_width": brick_width,
        "row_height": row_height,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}

    if not recipe and query:
        # The model passed a description instead of a recipe name: the
        # keywords know better than the model's guess.
        matches = match_recipes(str(query), limit=1)
        if matches:
            recipe = matches[0][0].name

    if recipe:
        # A material the model did not name gets a name that says what it is:
        # "Wood_Procedural" says both the material and the fact that it is a
        # graph. The baked twin is then named "Wood_Bake" - the word
        # "procedural" never survives into the result.
        material = build_from_recipe(
            str(recipe), str(name or _default_name(recipe)), **overrides
        )
        if material is None:
            # The model may have used its own word for the material ("bricks",
            # "кирпич"). The keywords know it, the name of the recipe does not.
            matched = match_recipes(str(recipe), limit=1)
            if matched:
                recipe = matched[0][0].name
                material = build_from_recipe(str(recipe), str(name or ""), **overrides)
        if material is None:
            close = closest_recipes(str(recipe))
            hint = ", ".join(r.name for r in close) or ", ".join(sorted(RECIPES))
            return (
                f"There is no recipe called '{recipe}'. The closest ones are: "
                f"{hint}. Call procedural.find with the description of the "
                f"material, or build it from parameters (pattern=...) without a "
                f"recipe."
            )
        spec = spec_for(str(recipe), str(name or ""), **overrides)
        installed = _install(material)
        text = (
            f"Built the procedural material '{material.name}' from the recipe "
            f"'{recipe}' {_describe(spec)}"
        )
        if installed:
            text += f" and assigned it to '{installed}'."
        return text

    if not pattern:
        # Building a default material would give grey noise: the user asked
        # for a surface, not for fog. Say what is missing instead.
        return (
            "A procedural material needs a pattern: noise, voronoi, wave, "
            "checker, gradient or brick - and the two colours, the roughness "
            "and the bump. Call procedural.find with the description of the "
            "material to get a ready-made recipe, or call again with "
            "pattern=..., color_a=..., color_b=..., roughness=..., "
            "bump_strength=..."
        )

    if str(pattern).lower() not in PATTERNS:
        # An unknown pattern would be silently replaced with noise - the model
        # would ask for planks and get fog. Say so instead.
        close = difflib.get_close_matches(
            str(pattern).lower(), list(PATTERNS), n=1, cutoff=0.5
        )
        hint = f" Did you mean '{close[0]}'?" if close else ""
        return (
            f"'{pattern}' is not a pattern the builder knows.{hint} The "
            f"patterns are: {', '.join(PATTERNS)}. A surface needs a structure "
            f"(brick, checker, voronoi, wave); noise alone reads as clouds."
        )

    # No recipe: the model described the material directly, so the
    # parameters come from what it sent.
    spec = ProcSpec(name=str(name or _default_name(pattern)))
    for key, value in overrides.items():
        setattr(spec, key, value)
    material = build_material(spec)
    installed = _install(material)
    text = f"Built the procedural material '{material.name}' {_describe(spec.normalized())}"
    if installed:
        text += f" and assigned it to '{installed}'."
    return text
