"""Material presets: the author's own hand-built procedural materials.

Each preset is a standalone bpy script in ``presets/``. The script builds a
material from a set of module-level parameters, so the add-on can read those
parameters, show them to the model and override the ones that matter.

Why this exists
---------------

A good procedural material is not "a noise texture with the right colours" -
it is hundreds of decisions: which layers, in which order, how the bumps are
chained. A small local model cannot invent that. But it **can** pick one of
these materials and turn the numbers, which is exactly what the model is good
at. The model stops being the artist and becomes the director.

The scripts were written by hand (with a model as a drafting assistant); they
are the part of the project that cannot be generated on the fly.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import bpy

from . import tools

PRESETS_DIR = Path(__file__).resolve().parent / "presets"

# A parameter of a preset: NAME = value  # what it does.
_PARAM_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.+?)\s*(?:#\s*(.*))?$")
# The function that builds the material: build_material or build_<something>.
_BUILD_RE = re.compile(r"^def (build_\w+)\(", re.MULTILINE)

# The parameters that are not material parameters: the preview plane and the
# name the script gives the material.
_INTERNAL = ("PLANE_SIZE", "MATERIAL_NAME", "PREVIEW_SHAPE")


@dataclass(frozen=True)
class Preset:
    """One preset script: what it is and what can be tuned."""

    name: str
    title: str
    description: str
    path: str
    parameters: Tuple[Tuple[str, str, str], ...]  # (name, value, comment)

    def parameter_names(self) -> List[str]:
        return [name for name, _value, _comment in self.parameters]

    def describe(self) -> str:
        """The catalogue line for the model."""
        lines = [f"- {self.name}: {self.title} - {self.description}"]
        for name, value, comment in self.parameters:
            note = f" ({comment})" if comment else ""
            lines.append(f"    {name} = {value}{note}")
        return "\n".join(lines)


def _read(path: Path) -> Tuple[str, str, List[Tuple[str, str, str]]]:
    """The title, the description and the parameters of a preset file."""
    text = path.read_text(encoding="utf-8")
    title = path.stem.replace("_", " ")
    description = ""
    if text.count('"""') >= 2:
        doc = text.split('"""')[1].strip().splitlines()
        if doc:
            title = doc[0].strip()
            description = " ".join(line.strip() for line in doc[1:4] if line.strip())

    parameters: List[Tuple[str, str, str]] = []
    for line in text.splitlines():
        if line.startswith((" ", "\t", "#")):
            continue
        match = _PARAM_RE.match(line)
        if match is None:
            continue
        name, value, comment = match.groups()
        if name in _INTERNAL:
            continue
        parameters.append((name, value.strip(), (comment or "").strip()))
    return title, description, parameters


def all_presets() -> List[Preset]:
    """Every preset script, sorted by name."""
    found: List[Preset] = []
    if not PRESETS_DIR.is_dir():
        return found
    for path in sorted(PRESETS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            title, description, parameters = _read(path)
        except OSError:
            continue
        found.append(
            Preset(
                name=path.stem,
                title=title,
                description=description,
                path=str(path),
                parameters=tuple(parameters),
            )
        )
    return found


def get(name: str) -> Optional[Preset]:
    """The preset with that name, or None."""
    wanted = (name or "").strip().lower()
    for preset in all_presets():
        if preset.name.lower() == wanted:
            return preset
    return None


def short_list() -> str:
    """The names of the presets, one line - for the system prompt."""
    names = [preset.name for preset in all_presets()]
    return ", ".join(names) if names else ""


def catalog() -> str:
    """The catalogue of the presets, for the model."""
    presets = all_presets()
    if not presets:
        return ""
    lines = ["The author's own procedural materials (presets):"]
    for preset in presets:
        lines.append(preset.describe())
    return "\n".join(lines)


def _load_module(preset: Preset):
    """Import a preset script as a module (without running its main block)."""
    name = f"gaa_preset_{preset.name}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, preset.path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the preset {preset.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build(
    preset_name: str,
    name: str = "",
    overrides: Optional[Dict] = None,
    assign: bool = True,
) -> Tuple[Optional[bpy.types.Material], str]:
    """Build the material of a preset.

    *overrides* are the parameters to change; the value is written into the
    module before the builder is called, because the builder reads the
    parameters as globals.
    """
    preset = get(preset_name)
    if preset is None:
        known = ", ".join(p.name for p in all_presets())
        return None, (
            f"There is no preset called '{preset_name}'. The known ones are: "
            f"{known}."
        )

    known = set(preset.parameter_names())
    applied: Dict[str, object] = {}
    unknown: List[str] = []
    for key, value in (overrides or {}).items():
        if key in known:
            applied[key] = value
        else:
            unknown.append(key)
    if unknown:
        return None, (
            f"'{unknown[0]}' is not a parameter of '{preset.name}'. The "
            f"parameters are: {', '.join(sorted(known))}."
        )

    try:
        module = _load_module(preset)
        for key, value in applied.items():
            setattr(module, key, value)
        # The builder is called "build_material" in one script and
        # "build_clean" / "build_scratched" / "build_worn" in the others.
        builder = getattr(module, "build_material", None)
        if builder is None:
            for candidate in dir(module):
                if candidate.startswith("build_") and callable(getattr(module, candidate)):
                    builder = getattr(module, candidate)
                    break
        if builder is None:
            return None, f"The preset '{preset.name}' has no build function."

        material = builder()
        if material is None:
            return None, f"The preset '{preset.name}' did not build a material."
        if name:
            material.name = name
        if assign:
            assigner = getattr(module, "assign_material", None)
            if assigner is not None:
                assigner(material)
    except Exception as exc:  # noqa: BLE001 - a broken preset must not kill the chat
        return None, f"{type(exc).__name__}: {exc}"

    report = f"Built '{material.name}' from the preset '{preset.name}'."
    if applied:
        changes = ", ".join(f"{key}={value}" for key, value in applied.items())
        report += f" Changed: {changes}."
    if assign:
        report += " Assigned to the active object."
    return material, report


@tools.tool(
    "procedural.preset",
    "Build a material from a preset",
    "Build one of the author's own procedural materials - the layered, "
    "hand-made graphs that a small model could never invent. Pick the preset "
    "and change the parameters you care about; the graph is the one that was "
    "written by hand. Call this without a preset to get the list with the "
    "parameters of each. The presets are: "
    + (", ".join(preset.name for preset in all_presets()) or "none"),
    params=("preset", "name", "params"),
    aliases=("material.preset",),
)
def preset_tool(preset=None, name="", params=None, **kwargs) -> str:
    """Tool entry point: build a preset material."""
    if not preset:
        catalogue = catalog()
        return (
            "Name the preset to build. These are available:\n" + catalogue
            if catalogue
            else "There are no material presets installed."
        )
    material, report = build(str(preset), str(name or ""), params or {})
    return report
