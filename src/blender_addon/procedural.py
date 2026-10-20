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

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import bpy

from . import api, tools

# The patterns the builder knows.
PATTERNS = ("noise", "voronoi", "wave", "checker", "gradient")

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
    ("marble", "stone", "vein", "мрамор", "камень"),
    pattern="noise", scale=2.0, detail=8.0, distortion=6.0,
    color_a=(0.75, 0.75, 0.73, 1.0), color_b=(0.12, 0.12, 0.13, 1.0),
    roughness=0.18, roughness_variation=0.08, bump_strength=0.05,
))
_add(_recipe(
    "stone", "Stone", "Chunky speckled rock",
    ("stone", "rock", "granite", "concrete", "камень", "бетон", "гранит"),
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
    """
    if not text:
        return []
    lowered = text.lower()
    scored = []
    for recipe in RECIPES.values():
        score = 0
        for keyword in recipe.keywords:
            if keyword in _WEAK_WORDS:
                continue
            if keyword in lowered:
                score += len(keyword)
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
    else:
        node = tree.nodes.new("ShaderNodeTexNoise")
    node.location = location
    return node


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

    if spec.pattern == "checker":
        # A checker has its own colours; the ramp would only blur them.
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    elif spec.pattern == "gradient":
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        links.new(_fac_output(texture), ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])

    # Roughness may vary with the texture.
    if spec.roughness_variation > 0.0:
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
    if spec.bump_strength > 0.0:
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


def build_from_recipe(recipe_name: str, name: str = "", **overrides) -> Optional[bpy.types.Material]:
    """Build a material from a named recipe, with the given overrides."""
    recipe = RECIPES.get((recipe_name or "").strip().lower())
    if recipe is None:
        return None
    spec = replace(recipe.spec)
    spec.name = name or recipe.title
    for key, value in overrides.items():
        if key in PARAMETERS and value is not None:
            setattr(spec, key, value)
    return build_material(spec)


# --- the tools -------------------------------------------------------------


@tools.tool(
    "procedural.list",
    "List the procedural recipes",
    "List the ready-made procedural material recipes (wood, marble, rust, "
    "fabric...) with what each one is for.",
)
def list_tool(**kwargs) -> str:
    """Tool entry point: the recipe catalogue."""
    lines = ["Procedural material recipes:"]
    for recipe in sorted(RECIPES.values(), key=lambda r: r.name):
        keywords = ", ".join(recipe.keywords[:5])
        lines.append(f"- {recipe.name}: {recipe.description} (keywords: {keywords})")
    return "\n".join(lines)


@tools.tool(
    "procedural.build",
    "Build a procedural material",
    "Build a procedural material from a recipe or from explicit parameters: "
    "the pattern, the scale, the two colours, the roughness and the bump. The "
    "graph is built by the add-on, so it always comes out valid.",
    params=("recipe", "name", "color_a", "color_b", "scale", "roughness",
            "metallic", "bump_strength"),
)
def build_tool(
    recipe=None, name="", color_a=None, color_b=None, scale=None,
    roughness=None, metallic=None, bump_strength=None, **kwargs
) -> str:
    """Tool entry point: build a procedural material."""
    overrides = {
        "color_a": color_a,
        "color_b": color_b,
        "scale": scale,
        "roughness": roughness,
        "metallic": metallic,
        "bump_strength": bump_strength,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}

    if recipe:
        material = build_from_recipe(str(recipe), str(name or ""), **overrides)
        if material is None:
            known = ", ".join(sorted(RECIPES))
            return f"There is no recipe called '{recipe}'. Known ones: {known}."
        return (
            f"Built the procedural material '{material.name}' from the recipe "
            f"'{recipe}'."
        )

    # No recipe: the model described the material directly, so the
    # parameters come from what it sent.
    spec = ProcSpec(name=str(name or "Procedural"))
    for key, value in overrides.items():
        setattr(spec, key, value)
    material = build_material(spec)
    return f"Built the procedural material '{material.name}'."
