"""The end-to-end scenario: from a folder of textures to a ready project.

Everything the add-on does about materials was built as separate pieces — a
doctor, texture tools, mass operations. This module chains them into the
workflow an artist actually performs:

1. **scan** the folder and work out what each map is;
2. **build** a PBR material from those maps;
3. **assign** it to the selection;
4. **pack** the textures into the .blend;
5. **check** whether the project is ready to leave this machine.

One call, one undo step, one report the user can read.

Every step is optional and every step reports what it did, so the pipeline is
honest: if a map was missing, the report says so instead of pretending the
material is complete.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import bpy

from . import export, materials, objects, textures, tools


def import_and_prepare(
    folder: str,
    assign: bool = True,
    pack: bool = True,
) -> Dict:
    """Run the whole scenario on a texture folder.

    Returns a report: ``{"steps": [...], "material": name, "problems": [...]}``.
    """
    steps: List[str] = []

    # 1. Scan.
    maps = materials.find_maps(folder)
    if not maps:
        raise ValueError(
            f"no texture maps found in {folder} "
            "(expected albedo/base, normal, roughness, metallic, ORM, "
            "emission or height)"
        )
    roles = ", ".join(materials.ordered_roles(maps))
    steps.append(f"Found in the folder: {roles}")

    # 2. Build the material.
    material, maps, assigned, reused = materials.build_pbr_from_folder(folder)
    if reused:
        steps.append(f"Updated the existing material '{material.name}'")
    else:
        steps.append(f"Built the material '{material.name}'")

    # 3. Assign it.
    if assign:
        targets = objects.selected_objects()
        if targets:
            changed = objects.assign_to_selection(material, targets)
            if changed:
                steps.append(f"Assigned it to {len(changed)} objects")
        else:
            steps.append("Nothing is selected, so the material was not assigned")

    # 4. Pack the textures.
    if pack:
        packed = textures.pack_all()
        if packed:
            steps.append(f"Packed {len(packed)} textures into the .blend")

    # 5. Check that the project can leave this machine.
    findings = export.check_export()
    counts = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    steps.append(
        f"Export check: {counts.get('error', 0)} errors, "
        f"{counts.get('warning', 0)} warnings, {counts.get('info', 0)} notes"
    )

    return {
        "steps": steps,
        "material": material.name,
        "problems": [finding.text("en") for finding in findings],
    }


# --- the tools -------------------------------------------------------------


@tools.tool(
    "project.import_textures",
    "Import textures and prepare the project",
    "The whole workflow in one step: scan a folder of texture maps, build a "
    "PBR material from them, assign it to the selection, pack the textures "
    "into the .blend and check that the project is ready to hand off.",
    params=("folder", "assign", "pack"),
)
def import_textures_tool(folder=None, assign=True, pack=True, **kwargs) -> str:
    """Tool entry point: the end-to-end scenario."""
    if not folder:
        return (
            "No folder given. Ask the user for the path to the texture folder, "
            "or tell them to press the 'Import from folder' button."
        )
    try:
        report_data = import_and_prepare(folder, assign=bool(assign), pack=bool(pack))
    except Exception as exc:  # noqa: BLE001 - the report must explain, not crash
        return f"Could not prepare the project: {type(exc).__name__}: {exc}"

    lines = [f"Prepared the project from {folder}:"] + [
        f"- {step}" for step in report_data["steps"]
    ]
    problems = report_data["problems"]
    if problems:
        lines.append("Problems left:")
        lines += [f"- {problem}" for problem in problems]
    else:
        lines.append("No problems left: the project is ready to hand off.")
    return "\n".join(lines)


@tools.tool(
    "export.check",
    "Check the export",
    "Check whether the project is ready to hand off: texture files that are "
    "gone, paths that will break, meshes without UVs, shaders the exporter "
    "cannot express and textures that are too large.",
    params=("max_texture",),
)
def check_export_tool(max_texture=None, **kwargs) -> str:
    """Tool entry point: the export check."""
    limit = int(max_texture) if max_texture else export.DEFAULT_MAX_TEXTURE
    findings = export.check_export(limit)
    if not findings:
        return "The export check found no problems: the project is ready."
    counts = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines = [
        f"Export check: {counts.get('error', 0)} errors, "
        f"{counts.get('warning', 0)} warnings, {counts.get('info', 0)} notes"
    ]
    lines += [f"- {finding.text('en')}" for finding in findings]
    return "\n".join(lines)
