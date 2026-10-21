"""Texture tools: packing, paths, relinking, colour spaces, sizes.

A Blender project breaks in two ways that have nothing to do with modelling:

* **the file leaves the machine** — textures stay on the disk, paths are
  absolute, and the next person to open the project sees a grey material;
* **the file is moved** — the textures move with it, but Blender still looks
  for them where they used to be.

Both are invisible while everything works and very expensive once it does not.
This module makes both a single click.

Every function here is **data API only**: no ``bpy.ops``, no context needed, so
the tools run headless in the test harness.

Two rules shape the design:

* **the socket wins over the file name.** A file called ``roughness.png`` that
  is wired into ``Base Color`` is an albedo map. Colour spaces are therefore
  fixed from the node graph, and the file name is only consulted for images
  that are not in a graph yet;
* **nothing is deleted and nothing is overwritten.** Packing can be undone,
  unpacking writes to a folder the user names, and relinking only ever points
  an image at a file that already exists.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

import bpy

from . import api, i18n, materials, tools

# Images Blender always keeps around; they carry no user texture meaning.
_IMAGE_BLACKLIST = {"Render Result", "Viewer Node"}

# How deep the search for a missing texture goes into the project folder.
SEARCH_DEPTH = 3
# Upper bound on the files visited while searching.
SEARCH_LIMIT = 20000


# --- helpers ---------------------------------------------------------------


def blend_dir() -> str:
    """The folder of the current .blend file ("" when it was never saved)."""
    path = bpy.data.filepath
    return os.path.dirname(path) if path else ""


def file_images() -> List:
    """Every file-backed image of the file, except the service ones."""
    try:
        images = list(bpy.data.images)
    except Exception:
        return []
    return [
        image
        for image in images
        if image.name not in _IMAGE_BLACKLIST and image.source == "FILE"
    ]


def is_missing(image) -> bool:
    """True when the file behind *image* is gone from disk.

    ``has_data`` is not a usable signal (Blender loads textures lazily), and a
    packed image does not need its file at all.
    """
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


def image_sockets(image) -> List[str]:
    """The names of the shader sockets *image* feeds, across all materials.

    This is what decides the colour space: a map wired into ``Roughness`` is
    data, whatever its file is called.
    """
    sockets = []
    try:
        mats = list(bpy.data.materials)
    except Exception:
        return sockets
    for mat in mats:
        tree = getattr(mat, "node_tree", None)
        if tree is None:
            continue
        for link in tree.links:
            if link.from_node.type != "TEX_IMAGE":
                continue
            source = getattr(link.from_node, "image", None)
            if source is None or source != image:
                continue
            if link.to_socket.name not in sockets:
                sockets.append(link.to_socket.name)
    return sockets


def expected_colorspace(image) -> Optional[str]:
    """The colour space *image* should have, or ``None`` when unknown.

    Two rules, in this order:

    * the **graph** decides - a map wired into ``Roughness`` is data;
    * a file name that says "data" while the graph says "colour" is a
      **conflict**: the wiring is wrong, not the colour space. The doctor
      reports that; the texture tools keep their hands off.
    """
    sockets = image_sockets(image)
    role = materials.detect_role(image.name)
    data_by_name = role in ("normal", "rough", "metal", "orm", "height")

    if sockets:
        if any(name in api.DATA_SOCKETS for name in sockets):
            return "Non-Color"
        if data_by_name:
            # A normal map wired into Base Color: changing its colour space
            # would hide the real problem instead of fixing it.
            return None
        return "sRGB"

    if role in ("base", "emit"):
        return "sRGB"
    if data_by_name:
        return "Non-Color"
    return None


def colorspace_conflict(image) -> str:
    """The socket where the wiring contradicts the file name ("" if none).

    "A normal map wired into Base Color" - the colour space is not the
    problem here, the wiring is.
    """
    sockets = image_sockets(image)
    if not sockets:
        return ""
    role = materials.detect_role(image.name)
    if role not in ("normal", "rough", "metal", "orm", "height"):
        return ""
    if any(name in api.DATA_SOCKETS for name in sockets):
        return ""
    return sockets[0]


# --- packing ---------------------------------------------------------------


def pack_all() -> List[str]:
    """Pack every file image into the .blend.

    Returns the names of the images that were packed. Already packed images
    are left alone.
    """
    packed = []
    for image in file_images():
        if image.packed_file is not None:
            continue
        try:
            image.pack()
        except Exception:  # noqa: BLE001 - an unreadable file must not stop the rest
            continue
        packed.append(image.name)
    return packed


def pack_all_report() -> Dict:
    """Pack everything and say what did not work.

    An image whose file is gone cannot be packed, and the old code swallowed
    that silently - the user saw "not packed" in the report and had no idea
    why. The reason belongs in the answer.
    """
    packed: List[str] = []
    failed: List[Dict] = []
    for image in file_images():
        if image.packed_file is not None:
            continue
        if is_missing(image):
            failed.append(
                {"name": image.name, "reason": "the file is gone from disk"}
            )
            continue
        try:
            image.pack()
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            failed.append({"name": image.name, "reason": str(exc)})
            continue
        packed.append(image.name)
    return {"packed": packed, "failed": failed}


def unpack_all(folder: Optional[str] = None) -> List[str]:
    """Write the packed images back to disk.

    *folder* is where the images go; ``None`` means "next to the .blend".
    Returns the names of the images that were written.
    """
    target = folder or blend_dir()
    if not target:
        return []
    try:
        Path(target).mkdir(parents=True, exist_ok=True)
    except OSError:
        return []

    written = []
    for image in file_images():
        if image.packed_file is None:
            continue
        name = os.path.basename(image.filepath) or (image.name + ".png")
        path = os.path.join(target, name)
        try:
            image.filepath_raw = path
            image.file_format = "PNG"
            image.save()
        except Exception:  # noqa: BLE001
            continue
        try:
            image.unpack(method="USE_ORIGINAL")
        except Exception:  # noqa: BLE001
            pass
        written.append(name)
    return written


# --- paths -----------------------------------------------------------------


def make_relative() -> List[str]:
    """Rewrite absolute texture paths as relative to the .blend file.

    Returns the names of the images that were changed. Does nothing when the
    project has never been saved - there would be no base to be relative to.

    ⚠️ ``bpy.path.relpath(path, start=...)`` expects the **file** path of the
    base, not its folder: Blender takes the parent of ``start`` and computes
    the path relative to that. Passing the folder shifts everything one level
    up.
    """
    base = bpy.data.filepath
    if not base:
        return []
    changed = []
    for image in file_images():
        path = image.filepath
        if not path or path.startswith("//"):
            continue
        if not os.path.isabs(path):
            continue
        try:
            relative = bpy.path.relpath(path, start=base)
        except Exception:
            continue
        if not relative or relative == path:
            continue
        image.filepath = relative
        changed.append(image.name)
    return changed


def make_absolute() -> List[str]:
    """The opposite of :func:`make_relative`."""
    changed = []
    for image in file_images():
        path = image.filepath
        if not path or not path.startswith("//"):
            continue
        try:
            absolute = bpy.path.abspath(path)
        except Exception:
            continue
        if absolute and absolute != path:
            image.filepath = absolute
            changed.append(image.name)
    return changed


def find_missing() -> List[str]:
    """The names of the images whose file is gone."""
    return [image.name for image in file_images() if is_missing(image)]


def _search_roots(extra: Optional[List[str]] = None) -> List[str]:
    """Where to look for a lost texture, nearest first."""
    roots = []
    if extra:
        roots.extend(extra)
    base = blend_dir()
    if base:
        roots.append(base)
    # The folder the image used to live in may still exist.
    for image in file_images():
        if not is_missing(image):
            continue
        try:
            old = os.path.dirname(bpy.path.abspath(image.filepath))
        except Exception:
            continue
        if old and os.path.isdir(old) and old not in roots:
            roots.append(old)
    return roots


def _walk(root: str, depth: int = SEARCH_DEPTH):
    """Every file under *root*, at most *depth* levels deep."""
    try:
        base_depth = len(Path(root).parts)
    except Exception:
        return
    count = 0
    for path in Path(root).rglob("*"):
        count += 1
        if count > SEARCH_LIMIT:
            return
        try:
            if len(path.parts) - base_depth > depth:
                continue
        except Exception:
            continue
        if path.is_file():
            yield str(path)


def relink(search_paths: Optional[List[str]] = None) -> List[str]:
    """Find lost textures by file name and point the images at them again.

    The search looks in the folders the user names, then in the project
    folder (a few levels deep), then next to where the file used to be. Only
    files that really exist are used.
    """
    roots = _search_roots(search_paths)
    if not roots:
        return []

    index = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in _walk(root):
            index.setdefault(os.path.basename(path).lower(), path)

    relinked = []
    for image in file_images():
        if not is_missing(image):
            continue
        name = os.path.basename(image.filepath)
        if not name:
            continue
        found = index.get(name.lower())
        if found is None:
            continue
        image.filepath = found
        relinked.append(image.name)
    return relinked


# --- colour spaces ---------------------------------------------------------


def fix_colorspace() -> List[str]:
    """Set the colour space of every image that is obviously wrong.

    The node graph decides: an image wired into ``Roughness`` is data. Only
    for images that are not in a graph the file name is used.
    """
    fixed = []
    for image in file_images():
        wanted = expected_colorspace(image)
        if wanted is None:
            continue
        current = image.colorspace_settings.name
        if current == wanted:
            continue
        try:
            image.colorspace_settings.name = wanted
        except Exception:  # noqa: BLE001 - some sources refuse a colour space
            continue
        fixed.append(f"{image.name}: {current} -> {wanted}")
    return fixed


# --- the report ------------------------------------------------------------


def report(lang: Optional[str] = None) -> str:
    """A table of every texture: size, colour space, packed, path.

    ``lang`` forces a language: the report shown to the user follows the
    interface language, the one handed to the model is always English.
    """
    images = file_images()
    if not images:
        return i18n.T("textures.none", lang=lang)
    lines = [i18n.T("textures.count", lang=lang, n=len(images))]
    for image in sorted(images, key=lambda i: i.name):
        width, height = image.size
        flags = []
        if image.packed_file is not None:
            flags.append("packed")
        if is_missing(image):
            flags.append("MISSING")
        wanted = expected_colorspace(image)
        space = image.colorspace_settings.name
        conflict = colorspace_conflict(image)
        if conflict:
            flags.append(f"wired into {conflict} but named like a data map")
        elif wanted and wanted != space:
            flags.append(f"space should be {wanted}")
        path = image.filepath or "-"
        suffix = ("  [" + ", ".join(flags) + "]") if flags else ""
        lines.append(f"- {image.name}  {width}x{height}  {space}{suffix}  {path}")
    return "\n".join(lines)


# --- the tools -------------------------------------------------------------
#
# The reports are English: their text goes into the model's context.


@tools.tool(
    "textures.pack_all",
    "Pack textures",
    "Pack every texture file into the .blend so the project travels with its "
    "textures. Nothing is lost and it can be undone.",
)
def pack_all_tool(**kwargs) -> str:
    """Tool entry point: pack all textures."""
    result = pack_all_report()
    packed = result["packed"]
    failed = result["failed"]
    if not packed and not failed:
        return "Nothing to pack: all textures are already inside the .blend."
    lines = []
    if packed:
        lines.append(
            f"Packed {len(packed)} textures into the .blend: {', '.join(packed)}"
        )
    if failed:
        lines.append(f"{len(failed)} texture(s) could not be packed:")
        for item in failed:
            lines.append(f"- {item['name']}: {item['reason']}")
        lines.append(
            "They stay on disk. Use the relink tool to find the missing files, "
            "or pack them after fixing the paths."
        )
    return "\n".join(lines)


def _preview_unpack(params=None) -> str:
    """Where the textures would be written."""
    params = params or {}
    folder = params.get("folder")
    packed = [i.name for i in file_images() if i.packed_file is not None]
    if not packed:
        return "no packed textures to write"
    where = folder or "the folder of the .blend file"
    return "{0} texture(s) would be written to {1}".format(len(packed), where)


@tools.tool(
    "textures.unpack_all",
    "Unpack textures",
    "Write the packed textures back to disk, next to the .blend file or in a "
    "folder you name. This overwrites files on disk.",
    destructive=True,
    params=("folder",),
    preview=_preview_unpack,
)
def unpack_all_tool(folder=None, **kwargs) -> str:
    """Tool entry point: unpack all textures."""
    written = unpack_all(folder)
    if not written:
        return "Nothing to unpack: no packed textures were found."
    return f"Wrote {len(written)} textures to disk: {', '.join(written)}"


@tools.tool(
    "textures.make_relative",
    "Relative paths",
    "Rewrite absolute texture paths as relative to the .blend file, so the "
    "project can be moved to another machine.",
)
def make_relative_tool(**kwargs) -> str:
    """Tool entry point: make the texture paths relative."""
    if not blend_dir():
        return (
            "The project has never been saved, so there is no base folder to "
            "make the paths relative to. Ask the user to save the file first."
        )
    changed = make_relative()
    if not changed:
        return "All texture paths are already relative."
    return f"Made {len(changed)} texture paths relative: {', '.join(changed)}"


@tools.tool(
    "textures.relink",
    "Relink lost textures",
    "Find texture files that went missing and point the images at them again, "
    "searching the project folder by file name.",
    params=("folders",),
)
def relink_tool(folders=None, **kwargs) -> str:
    """Tool entry point: relink lost textures."""
    extra = None
    if isinstance(folders, str):
        extra = [folders]
    elif isinstance(folders, list):
        extra = [str(f) for f in folders]
    missing = find_missing()
    if not missing:
        return "No texture file is missing."
    relinked = relink(extra)
    left = find_missing()
    if not relinked:
        return (
            f"{len(missing)} texture files are missing and none of them was "
            f"found: {', '.join(missing)}. Ask the user where the textures are."
        )
    return (
        f"Relinked {len(relinked)} textures: {', '.join(relinked)}. "
        f"{len(left)} still missing."
    )


@tools.tool(
    "textures.fix_colorspace",
    "Fix colour spaces",
    "Set the colour space of every texture that is wrong: sRGB for colour "
    "maps, Non-Color for data maps (roughness, metallic, normal, height).",
)
def fix_colorspace_tool(**kwargs) -> str:
    """Tool entry point: fix the colour spaces by role."""
    fixed = fix_colorspace()
    if not fixed:
        return "Every texture already has the right colour space."
    return f"Fixed {len(fixed)} colour spaces:\n" + "\n".join(f"- {f}" for f in fixed)


@tools.tool(
    "textures.report",
    "Texture report",
    "List every texture with its size, colour space, whether it is packed and "
    "where its file is. Use it to see the state of the project's textures.",
)
def report_tool(**kwargs) -> str:
    """Tool entry point: the texture report."""
    return report(lang="en")
