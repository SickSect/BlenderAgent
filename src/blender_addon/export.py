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

import os
from typing import List, Optional

import bpy

from . import api, doctor, materials, textures, tools

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


# --- the export itself ------------------------------------------------------
#
# The check above answers "is the project ready?". This writes the file.

# The formats we can write.
FORMATS = ("glb", "gltf", "fbx")

# The file name we give the export: the scene name, or the object when only
# one object is exported.
_DEFAULT_NAME = "Asset"


def _call_exporter(operator, **kwargs):
    """Call an export operator with only the parameters it knows.

    The exporters change their parameters between Blender versions, and an
    unknown keyword is a TypeError that would kill the export.
    """
    try:
        known = set(operator.get_rna_type().properties.keys())
    except Exception:  # noqa: BLE001 - the operator is not introspectable
        known = set(kwargs)
    return operator(**{k: v for k, v in kwargs.items() if k in known})


def _selection(objects=None) -> List:
    """The objects to export: the given ones, or the selection."""
    if objects:
        return list(objects)
    return [obj for obj in bpy.context.selected_objects]


def _export_name(objects) -> str:
    """The name of the file: the scene, or the single exported object."""
    if len(objects) == 1:
        return objects[0].name
    scene = getattr(bpy.context, "scene", None)
    name = getattr(scene, "name", "") if scene else ""
    return name or _DEFAULT_NAME


def export(
    folder: str,
    fmt: str = "glb",
    objects=None,
    apply_modifiers: bool = True,
    strict: bool = True,
) -> Dict:
    """Write the selected objects to *folder* in the *fmt* format.

    ``strict`` refuses the export when the readiness check found errors: the
    assistant should fix the project first. A person who presses the button
    gets the export anyway - with the findings in the report.
    """
    result: Dict = {
        "ok": False,
        "path": "",
        "objects": [],
        "findings": [],
        "size": 0,
    }
    fmt = (fmt or "glb").strip().lower()
    if fmt not in FORMATS:
        result["error"] = f"unknown format '{fmt}' (known: {', '.join(FORMATS)})"
        return result

    chosen = _selection(objects)
    if not chosen:
        result["error"] = "Nothing is selected, so there is nothing to export."
        return result
    result["objects"] = [obj.name for obj in chosen]

    findings = check_export()
    result["findings"] = [
        {"severity": finding.severity, "text": finding.text()} for finding in findings
    ]
    blocking = [f for f in findings if f.severity == doctor.ERROR]
    if blocking and strict:
        result["error"] = (
            "The project is not ready to be exported:\n"
            + "\n".join(f"- {f.text()}" for f in blocking)
        )
        return result

    # The exporters are built-in add-ons; they may be switched off.
    module = "io_scene_gltf2" if fmt in ("glb", "gltf") else "io_scene_fbx"
    try:
        if module not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module=module)
    except Exception as exc:  # noqa: BLE001 - the exporter may be an extension
        result["error"] = (
            f"The {fmt.upper()} exporter is not available ({exc}). Enable it in "
            f"the preferences."
        )
        return result

    name = _export_name(chosen)
    suffix = {"glb": ".glb", "gltf": ".gltf", "fbx": ".fbx"}[fmt]
    path = os.path.join(folder, name + suffix)

    # Only the exported objects may be selected, or the exporter writes the
    # whole scene.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in chosen:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = chosen[0]

    try:
        if fmt in ("glb", "gltf"):
            _call_exporter(
                bpy.ops.export_scene.gltf,
                filepath=path,
                export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE",
                use_selection=True,
                export_apply=apply_modifiers,
                export_yup=True,
                export_materials="EXPORT",
                export_cameras=False,
                export_lights=False,
                export_extras=True,
                export_texture_dir="textures",
            )
        else:
            _call_exporter(
                bpy.ops.export_scene.fbx,
                filepath=path,
                use_selection=True,
                apply_unit_scale=True,
                axis_forward="-Z",
                axis_up="Y",
                path_mode="COPY",
                embed_textures=False,
                use_mesh_modifiers=apply_modifiers,
                bake_space_transform=False,
            )
    except Exception as exc:  # noqa: BLE001 - a failed export must not kill Blender
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["ok"] = True
    result["path"] = path
    try:
        result["size"] = os.path.getsize(path)
    except OSError:
        result["size"] = 0
    return result


def _report(result: Dict) -> str:
    """The export result as a readable report."""
    if not result.get("ok"):
        return f"The export did not happen: {result.get('error', 'unknown reason')}"
    lines = [
        f"Exported {len(result['objects'])} objects to {result['path']}"
        + (f" ({result['size'] // 1024} kB)" if result.get("size") else "")
    ]
    warnings = [f for f in result.get("findings", []) if f["severity"] != doctor.ERROR]
    if warnings:
        lines.append("With warnings:")
        for finding in warnings[:5]:
            lines.append(f"- {finding['text']}")
    return "\n".join(lines)


def _default_folder() -> str:
    """Where the export goes when the user does not say: next to the .blend."""
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return os.path.expanduser("~")


@tools.tool(
    "export.glb",
    "Export the selection to a glTF file",
    "Export the selected objects for a game: glTF, the format Unity and "
    "Unreal read. The readiness check runs first - if it finds errors, the "
    "export is refused, because a broken file is worse than no file. The "
    "textures are copied next to it.",
    params=("folder",),
)
def export_glb_tool(folder=None, **kwargs) -> str:
    """Tool entry point: export to glTF."""
    result = export(str(folder or _default_folder()), "glb")
    return _report(result)


@tools.tool(
    "export.fbx",
    "Export the selection to an FBX file",
    "Export the selected objects for a game: FBX, with the axes the engines "
    "expect and the textures copied next to the file. The readiness check runs "
    "first - if it finds errors, the export is refused.",
    params=("folder",),
)
def export_fbx_tool(folder=None, **kwargs) -> str:
    """Tool entry point: export to FBX."""
    result = export(str(folder or _default_folder()), "fbx")
    return _report(result)
