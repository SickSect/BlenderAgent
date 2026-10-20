"""Material doctor: find what is wrong with the materials in the scene.

The hardest part of working with materials is not building them — it is
**diagnosing** them. A roughness map read through the sRGB curve looks "a bit
washed out", not broken. A normal map wired straight into Base Color looks
like a lighting problem. A texture file that is gone looks like an empty slot.

Those mistakes survive because nobody can see them. The doctor can: it walks
the node graphs and the file system and reports what it finds.

Three layers, each usable on its own:

1. :func:`audit_material` — what is wrong with *one* material;
2. :func:`audit_scene` — the same for every material, plus the object-level
   problems (no UV, no subdivision for displacement);
3. :func:`fix` — repair the findings that can be repaired safely.

Every finding carries a **code** (stable, for tools and tests), a severity, an
i18n key with its parameters, and the id of the fix that would repair it (or
an empty string when nothing can be done automatically).

The module is the single source of truth for these checks: the scene snapshot
(chapter 7) renders the very same findings for the model, so a problem is
never described differently in two places.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import bpy

from . import api, findings, i18n, materials, tools

# The finding type lives in its own module so that the other checkers (the
# export check, the UV analyzer) can use it without depending on the doctor.
Finding = findings.Finding
ERROR = findings.ERROR
WARNING = findings.WARNING
INFO = findings.INFO

# --- finding codes ---------------------------------------------------------

COLORSPACE = "colorspace"
NORMAL_WITHOUT_NODE = "normal_without_node"
NORMAL_TO_BASE = "normal_to_base"
NO_UV = "no_uv"
MISSING_FILE = "missing_file"
NOT_PACKED = "not_packed"
ABSOLUTE_PATH = "absolute_path"
SURFACE_UNCONNECTED = "surface_unconnected"
DISPLACEMENT_NO_SUBDIV = "displacement_no_subdiv"
ORPHAN_MATERIAL = "orphan_material"
DUPLICATE_MATERIAL = "duplicate_material"
RESOLUTION_MISMATCH = "resolution_mismatch"

# Fixes that can be applied without asking and without losing data. Anything
# destructive (deleting an orphan, merging duplicates) is deliberately absent.
SAFE_FIXES = (COLORSPACE, NORMAL_WITHOUT_NODE, NORMAL_TO_BASE, SURFACE_UNCONNECTED)


@dataclass(frozen=True)
class Finding:
    """One problem found in the scene."""

    code: str
    severity: str
    key: str                    # i18n key, "find.<code>"
    params: Dict = field(default_factory=dict)
    subject: str = ""           # what the finding is about
    fix: str = ""               # the fix id, "" when nothing can be done
    material: str = ""          # the material it belongs to

    def text(self, lang: Optional[str] = None) -> str:
        """The finding as a sentence, in the given language."""
        return i18n.T(self.key, lang=lang, **self.params)


def _finding(
    code: str,
    severity: str,
    params: Dict,
    subject: str = "",
    fix: str = "",
    material: str = "",
) -> Finding:
    return findings.make(
        code, severity, params, subject=subject, fix=fix, material=material
    )


# --- helpers ---------------------------------------------------------------


def _node_role(node) -> str:
    """The role of the image in *node*, guessed from the file name."""
    image = getattr(node, "image", None)
    if image is None:
        return ""
    return materials.detect_role(image.name) or ""


def _data_socket_links(tree):
    """Links where an image texture feeds a *data* socket of the shader.

    Yields ``(link, node, image, socket_name)``. A data socket is one that
    carries numbers, not colour — such an image must be in ``Non-Color``.
    """
    for link in tree.links:
        from_node = link.from_node
        if from_node.type != "TEX_IMAGE":
            continue
        image = getattr(from_node, "image", None)
        if image is None:
            continue
        if link.to_socket.name in api.DATA_SOCKETS:
            yield link, from_node, image, link.to_socket.name


def _image_missing(image) -> bool:
    """True when the file behind a file-backed image is gone.

    ``image.has_data`` is **not** a reliable signal: Blender loads file images
    lazily, so a perfectly good texture reports ``has_data == False`` until
    something asks for its pixels — and in background mode that never happens.
    The filesystem is the honest check, with one exception: a packed image
    keeps its data inside the .blend, so the file on disk does not matter.
    """
    if image.source != "FILE":
        return False
    if image.packed_file is not None:
        return False
    path = image.filepath
    if not path:
        return True
    try:
        absolute = bpy.path.abspath(path)
    except Exception:
        absolute = path
    return not os.path.exists(absolute)


def _is_absolute_path(path: str) -> bool:
    """True when *path* is absolute rather than relative to the .blend.

    Blender stores a relative path as ``//textures/wood.png`` - the double
    slash means "next to the .blend file". On Windows ``os.path.isabs`` calls
    that absolute (it reads the leading slashes as a UNC path), so the check
    has to be done by hand.
    """
    if not path:
        return False
    if path.startswith("//"):
        return False
    return os.path.isabs(path)


def _material_users(mat) -> List:
    """The objects of the current scene that use *mat*."""
    users = []
    try:
        objects = list(bpy.context.scene.objects)
    except Exception:
        return users
    for obj in objects:
        slots = getattr(getattr(obj, "data", None), "materials", None)
        if slots is None:
            continue
        for slot in slots:
            if slot is not None and slot == mat:
                users.append(obj)
                break
    return users


def signature(mat):
    """A structural fingerprint of *mat*.

    Two materials with the same signature are duplicates: the same nodes, the
    same textures, the same connections. Node *names* are ignored on purpose
    — Blender renames the copy ("Image" -> "Image.001"), and that is exactly
    the difference we must not see.
    """
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return ("simple", tuple(round(float(v), 4) for v in mat.diffuse_color))
    nodes = []
    for node in tree.nodes:
        image = getattr(node, "image", None)
        nodes.append((node.type, image.name if image is not None else ""))
    links = []
    for link in tree.links:
        links.append(
            (
                link.from_node.type,
                link.from_socket.name,
                link.to_node.type,
                link.to_socket.name,
            )
        )
    return (tuple(sorted(nodes)), tuple(sorted(links)))


# --- the audit -------------------------------------------------------------


def audit_material(mat) -> List[Finding]:
    """Every problem found in *mat*."""
    findings: List[Finding] = []
    if mat is None:
        return findings

    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return findings

    # 1. A data map in the wrong colour space.
    seen_pairs = set()
    for link, node, image, socket_name in _data_socket_links(tree):
        if image.colorspace_settings.name == "Non-Color":
            continue
        key = (image.name, socket_name)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        findings.append(
            _finding(
                COLORSPACE,
                ERROR,
                {
                    "image": image.name,
                    "mat": mat.name,
                    "socket": socket_name,
                    "space": image.colorspace_settings.name,
                },
                subject=image.name,
                fix=COLORSPACE,
                material=mat.name,
            )
        )

    # 2. An image wired straight into the Normal socket (no Normal Map node).
    for link in tree.links:
        if link.from_node.type != "TEX_IMAGE":
            continue
        if link.to_socket.name != "Normal":
            continue
        image = getattr(link.from_node, "image", None)
        if image is None:
            continue
        findings.append(
            _finding(
                NORMAL_WITHOUT_NODE,
                ERROR,
                {"image": image.name, "mat": mat.name},
                subject=image.name,
                fix=NORMAL_WITHOUT_NODE,
                material=mat.name,
            )
        )

    # 3. A normal-looking map wired into Base Color.
    for link in tree.links:
        if link.from_node.type != "TEX_IMAGE":
            continue
        if link.to_socket.name != "Base Color":
            continue
        if _node_role(link.from_node) != "normal":
            continue
        image = getattr(link.from_node, "image", None)
        if image is None:
            continue
        findings.append(
            _finding(
                NORMAL_TO_BASE,
                WARNING,
                {"image": image.name, "mat": mat.name},
                subject=image.name,
                fix=NORMAL_TO_BASE,
                material=mat.name,
            )
        )

    # 4. The shader output is not connected.
    output = api.find_output(tree)
    bsdf = api.find_principled(tree)
    if output is not None and bsdf is not None:
        if not output.inputs["Surface"].links:
            findings.append(
                _finding(
                    SURFACE_UNCONNECTED,
                    ERROR,
                    {"mat": mat.name},
                    subject=mat.name,
                    fix=SURFACE_UNCONNECTED,
                    material=mat.name,
                )
            )

    # 5. Texture files: missing, not packed, absolute path.
    for node in tree.nodes:
        if node.type != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        if image is None or image.source != "FILE":
            continue
        if _image_missing(image):
            findings.append(
                _finding(
                    MISSING_FILE,
                    ERROR,
                    {"image": image.name, "path": image.filepath},
                    subject=image.name,
                    material=mat.name,
                )
            )
            continue
        if image.packed_file is None:
            findings.append(
                _finding(
                    NOT_PACKED,
                    INFO,
                    {"image": image.name},
                    subject=image.name,
                    material=mat.name,
                )
            )
        if _is_absolute_path(image.filepath):
            findings.append(
                _finding(
                    ABSOLUTE_PATH,
                    WARNING,
                    {"image": image.name, "path": image.filepath},
                    subject=image.name,
                    material=mat.name,
                )
            )

    # 6. Displacement without a subdivision modifier on the users.
    displaced = any(
        link.to_node.type == "OUTPUT_MATERIAL"
        and link.to_socket.name == "Displacement"
        for link in tree.links
    )
    if displaced:
        for obj in _material_users(mat):
            modifiers = getattr(obj, "modifiers", None)
            if modifiers is None:
                continue
            if not any(m.type == "SUBSURF" for m in modifiers):
                findings.append(
                    _finding(
                        DISPLACEMENT_NO_SUBDIV,
                        WARNING,
                        {"mat": mat.name, "obj": obj.name},
                        subject=obj.name,
                        material=mat.name,
                    )
                )

    # 7. Maps of different sizes in one material.
    sizes = {}
    for node in tree.nodes:
        if node.type != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        if image is None:
            continue
        width, height = image.size
        if width and height:
            sizes.setdefault(f"{width}x{height}", []).append(image.name)
    if len(sizes) > 1:
        findings.append(
            _finding(
                RESOLUTION_MISMATCH,
                INFO,
                {"mat": mat.name, "sizes": ", ".join(sorted(sizes))},
                subject=mat.name,
                material=mat.name,
            )
        )

    return findings


def audit_scene(materials_list=None) -> List[Finding]:
    """Every problem found in the scene.

    Besides the per-material audit it reports the object-level problems: a
    textured mesh without a UV layout, an unused material and a duplicate of
    another material.
    """
    if materials_list is None:
        try:
            materials_list = list(bpy.data.materials)
        except Exception:
            return []

    findings: List[Finding] = []
    for mat in materials_list:
        findings.extend(audit_material(mat))

    # Textured mesh without UV.
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
                        NO_UV,
                        WARNING,
                        {"obj": obj.name, "mat": mat.name},
                        subject=obj.name,
                        material=mat.name,
                    )
                )
                break

    # Unused material.
    for mat in materials_list:
        try:
            if mat.users == 0:
                findings.append(
                    _finding(
                        ORPHAN_MATERIAL,
                        INFO,
                        {"mat": mat.name},
                        subject=mat.name,
                        material=mat.name,
                    )
                )
        except Exception:
            continue

    # Duplicates.
    seen = {}
    for mat in materials_list:
        key = signature(mat)
        if key in seen:
            findings.append(
                _finding(
                    DUPLICATE_MATERIAL,
                    WARNING,
                    {"mat": mat.name, "other": seen[key].name},
                    subject=mat.name,
                    material=mat.name,
                )
            )
        else:
            seen[key] = mat

    return findings


# --- the repairs -----------------------------------------------------------


def fix(material, finding: Finding) -> str:
    """Apply the fix of *finding* to *material*; return what was done.

    Returns an empty string when the finding has no automatic fix.
    """
    if finding.fix not in SAFE_FIXES:
        return ""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return ""

    if finding.fix == COLORSPACE:
        image = bpy.data.images.get(finding.params.get("image", ""))
        if image is None:
            return ""
        before = image.colorspace_settings.name
        try:
            image.colorspace_settings.name = "Non-Color"
        except Exception as exc:  # noqa: BLE001 - some sources refuse it
            return f"{image.name}: {exc}"
        return f"{image.name}: colour space {before} -> Non-Color"

    if finding.fix == SURFACE_UNCONNECTED:
        output = api.find_output(tree)
        bsdf = api.find_principled(tree)
        if output is None or bsdf is None:
            return ""
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        return f"{material.name}: connected the Principled BSDF to the output"

    if finding.fix in (NORMAL_WITHOUT_NODE, NORMAL_TO_BASE):
        return _fix_normal(material, tree, finding)

    return ""


def _fix_normal(material, tree, finding: Finding) -> str:
    """Put a normal map through a Normal Map node.

    Two shapes of the same repair:

    * the image feeds ``Normal`` directly — wrap it in a Normal Map node;
    * the image feeds ``Base Color`` — wrap it *and* move it to ``Normal``.
    """
    image_name = finding.params.get("image", "")
    node = None
    for candidate in tree.nodes:
        if candidate.type != "TEX_IMAGE":
            continue
        image = getattr(candidate, "image", None)
        if image is not None and image.name == image_name:
            node = candidate
            break
    if node is None:
        return ""
    bsdf = api.find_principled(tree)
    if bsdf is None:
        return ""

    normal_map = tree.nodes.new("ShaderNodeNormalMap")
    normal_map.name = "Normal Map (doctor)"
    normal_map.location = (node.location.x + 300, node.location.y - 200)
    tree.links.new(node.outputs["Color"], normal_map.inputs["Color"])

    if finding.fix == NORMAL_WITHOUT_NODE:
        tree.links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
        return f"{image_name}: added a Normal Map node"

    # NORMAL_TO_BASE: take the image out of Base Color and put it in Normal.
    # NOTE: compare with ``==``, not ``is``: bpy hands out a fresh Python
    # wrapper on every access, so ``link.from_node is node`` is always False.
    for link in list(tree.links):
        if link.from_node == node and link.to_socket.name == "Base Color":
            tree.links.remove(link)
    tree.links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    # An input socket holds ONE link in Blender: wiring the normal map into
    # Base Color *replaced* the albedo, not joined it. The albedo node is
    # still in the graph, just unconnected - so reconnect it.
    base = api.get_input(bsdf, "Base Color")
    if base is not None and not base.links:
        for candidate in tree.nodes:
            if candidate.type != "TEX_IMAGE" or candidate == node:
                continue
            image = getattr(candidate, "image", None)
            if image is None:
                continue
            if materials.detect_role(image.name) != "base":
                continue
            if any(link.from_node == candidate for link in tree.links):
                continue
            tree.links.new(candidate.outputs["Color"], base)
            return (
                f"{image_name}: moved it from Base Color through a Normal Map "
                f"node and reconnected the albedo '{image.name}'."
            )

    note = ""
    if base is not None and not base.links:
        note = " Base Color is now empty - reconnect the albedo by hand."
    return f"{image_name}: moved it from Base Color through a Normal Map node.{note}"


def apply_fixes(material, findings: List[Finding]) -> List[str]:
    """Apply every safe fix of *findings*; return the list of what was done."""
    done = []
    for finding in findings:
        if finding.fix not in SAFE_FIXES:
            continue
        message = fix(material, finding)
        if message:
            done.append(message)
    return done


# --- reporting -------------------------------------------------------------


def sorted_findings(findings_list: List[Finding]) -> List[Finding]:
    """The findings, worst first: errors, then warnings, then notes."""
    return findings.sorted_findings(findings_list)


def format_findings(findings_list: List[Finding], lang: Optional[str] = None) -> str:
    """The findings as a readable list, in the given language."""
    return findings.format_findings(findings_list, lang)


def summary(findings_list: List[Finding]) -> Dict[str, int]:
    """How many findings of each severity."""
    return findings.summary(findings_list)


# --- the tools -------------------------------------------------------------
#
# The tools report in English: their text goes into the model's context, and
# the model reads English better than the interface language.


@tools.tool(
    "material.audit",
    "Audit materials",
    "Find problems in the materials of the scene: data maps in the wrong "
    "colour space, normal maps wired without a Normal Map node, missing "
    "texture files, meshes without UVs, unused and duplicate materials. Call "
    "this instead of inspecting the scene by hand.",
    params=("material",),
)
def audit_tool(material=None, **kwargs) -> str:
    """Tool entry point: audit the scene (or one named material)."""
    if material:
        mat = bpy.data.materials.get(material)
        if mat is None:
            return f"No material named '{material}' in this file."
        findings = audit_material(mat)
        scope = f"material '{mat.name}'"
    else:
        findings = audit_scene()
        scope = "the scene"
    if not findings:
        return f"Audited {scope}: no problems found."
    counts = summary(findings)
    header = (
        f"Audited {scope}: {counts[ERROR]} errors, {counts[WARNING]} warnings, "
        f"{counts[INFO]} notes."
    )
    body = "\n".join(f"- {f.text('en')}" for f in sorted_findings(findings))
    return f"{header}\n{body}"


@tools.tool(
    "material.fix",
    "Fix materials",
    "Apply the automatic repairs of the material doctor: set the colour space "
    "of data maps to Non-Color, put normal maps through a Normal Map node and "
    "connect an unconnected shader output. Nothing is deleted.",
)
def fix_tool(**kwargs) -> str:
    """Tool entry point: apply the safe repairs."""
    findings = audit_scene()
    if not findings:
        return "Nothing to fix: no problems found."
    done = []
    for finding in findings:
        if finding.fix not in SAFE_FIXES:
            continue
        mat = bpy.data.materials.get(finding.material)
        if mat is None:
            continue
        message = fix(mat, finding)
        if message:
            done.append(message)
    if not done:
        return (
            f"Found {len(findings)} problems, but none of them has an automatic "
            "repair. Tell the user what is wrong and what to do."
        )
    remaining = audit_scene()
    return (
        f"Applied {len(done)} repairs:\n"
        + "\n".join(f"- {line}" for line in done)
        + f"\n{len(remaining)} problems left."
    )
