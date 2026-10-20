"""Is the project ready to hand off?

The material doctor asks "is this material *correct*?". The export check asks
a different question: "is this project *ready to leave this machine*?".

The two see the same scene from different sides:

* the doctor looks **inside** a material (wrong colour space, a normal map
  wired into the wrong socket);
* the export check looks **outside** (a file that is gone, a path that will
  break on another machine, a shader the exporter cannot express).

Most findings are shared with the doctor — a lost texture file is broken both
for the viewport and for the export — so the shared codes reuse the same
messages. What is added here is what only matters at hand-off time: a
non-standard shader, a displacement, an oversized texture.

The findings reuse :class:`doctor.Finding`, so the report looks the same
wherever it is shown.
"""

from __future__ import annotations

from typing import List, Optional

import bpy

from . import api, doctor, materials, textures

# What the exporters cannot express. The glTF and FBX exporters take a
# Principled BSDF and map it onto their own model; anything else is either
# dropped or approximated.
CUSTOM_SHADER = "export_custom_shader"
DISPLACEMENT = "export_displacement"
TEXTURE_TOO_LARGE = "export_texture_large"

# The default limit: 4K is the usual ceiling for game assets, and anything
# above it is a waste of memory on every device.
DEFAULT_MAX_TEXTURE = 4096

# Node types the standard exporters cannot map.
_CUSTOM_SHADERS = {
    "BSDF_GLASS": "Glass BSDF",
    "BSDF_REFRACTION": "Refraction BSDF",
    "BSDF_TRANSPARENT": "Transparent BSDF",
    "BSDF_TOON": "Toon BSDF",
    "BSDF_DIFFUSE": "Diffuse BSDF",
    "BSDF_GLOSSY": "Glossy BSDF",
    "BSDF_TRANSLUCENT": "Translucent BSDF",
}


def _finding(code: str, severity: str, params: dict, subject: str = "") -> doctor.Finding:
    return doctor.Finding(
        code=code,
        severity=severity,
        key=f"find.{code}",
        params=params,
        subject=subject,
        fix="",
        material=params.get("mat", ""),
    )


def _material_users(mat) -> List:
    try:
        objects = list(bpy.context.scene.objects)
    except Exception:
        return []
    users = []
    for obj in objects:
        slots = getattr(getattr(obj, "data", None), "materials", None)
        if slots is None:
            continue
        for slot in slots:
            if slot is not None and slot == mat:
                users.append(obj)
                break
    return users


def check_export(max_texture: int = DEFAULT_MAX_TEXTURE) -> List[doctor.Finding]:
    """Everything that will break or be lost when the project leaves here."""
    findings: List[doctor.Finding] = []

    try:
        mats = list(bpy.data.materials)
    except Exception:
        mats = []

    for mat in mats:
        tree = getattr(mat, "node_tree", None)
        if tree is None:
            continue

        # A shader the exporters cannot map.
        output = api.find_output(tree)
        bsdf = api.find_principled(tree)
        if output is not None and bsdf is not None:
            surface = output.inputs.get("Surface")
            if surface is not None and surface.links:
                source = surface.links[0].from_node
                if source != bsdf:
                    label = _CUSTOM_SHADERS.get(source.type, source.bl_idname)
                    findings.append(
                        _finding(
                            CUSTOM_SHADER,
                            doctor.WARNING,
                            {"mat": mat.name, "node": label},
                            subject=mat.name,
                        )
                    )

        # Displacement is not part of the PBR exchange formats.
        for link in tree.links:
            if link.to_node.type == "OUTPUT_MATERIAL" and link.to_socket.name == "Displacement":
                findings.append(
                    _finding(
                        DISPLACEMENT,
                        doctor.WARNING,
                        {"mat": mat.name},
                        subject=mat.name,
                    )
                )
                break

    # Textures: gone, unpinned, or too big.
    for image in textures.file_images():
        if textures.is_missing(image):
            findings.append(
                _finding(
                    doctor.MISSING_FILE,
                    doctor.ERROR,
                    {"image": image.name, "path": image.filepath},
                    subject=image.name,
                )
            )
            continue
        if image.packed_file is None:
            findings.append(
                _finding(
                    doctor.NOT_PACKED,
                    doctor.INFO,
                    {"image": image.name},
                    subject=image.name,
                )
            )
        width, height = image.size
        if width and height and max(width, height) > max_texture:
            findings.append(
                _finding(
                    TEXTURE_TOO_LARGE,
                    doctor.INFO,
                    {"image": image.name, "size": f"{width}x{height}", "max": max_texture},
                    subject=image.name,
                )
            )

    # Meshes without a UV layout cannot carry a texture at all.
    try:
        objects = list(bpy.context.scene.objects)
    except Exception:
        objects = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        data = getattr(obj, "data", None)
        if data is None:
            continue
        uv_layers = getattr(data, "uv_layers", None)
        if uv_layers is not None and len(uv_layers):
            continue
        slots = getattr(data, "materials", None)
        if not slots:
            continue
        for slot in slots:
            mat = slot
            if mat is None:
                continue
            tree = getattr(mat, "node_tree", None)
            if tree is None:
                continue
            if any(node.type == "TEX_IMAGE" for node in tree.nodes):
                findings.append(
                    _finding(
                        doctor.NO_UV,
                        doctor.WARNING,
                        {"obj": obj.name, "mat": mat.name},
                        subject=obj.name,
                    )
                )
                break

    return doctor.sorted_findings(findings)


def report(max_texture: int = DEFAULT_MAX_TEXTURE) -> str:
    """The export check as a readable report."""
    findings = check_export(max_texture)
    if not findings:
        return "Проблем с экспортом не найдено — проект готов передать."
    counts = doctor.summary(findings)
    lines = [
        f"Проверка экспорта: ошибок {counts[doctor.ERROR]}, "
        f"предупреждений {counts[doctor.WARNING]}, заметок {counts[doctor.INFO]}"
    ]
    for finding in findings:
        lines.append(f"- {finding.text()}")
    return "\n".join(lines)
