"""Mass operations on materials and objects.

The slow part of asset work is not making a material — it is making it
**everywhere**: assigning it to eighty objects, swapping the old one for the
new one, finding out who still uses the old one.

Those tasks are pure bookkeeping, and bookkeeping is exactly what code is good
at. This module does it in one call, and every function is **data API only**,
so it runs headless and inside a single undo step.

The operations come in four flavours:

* **assign** — put a material on many objects (the active slot is replaced,
  the way an artist expects when re-texturing);
* **replace** — swap one material for another across the whole scene;
* **share** — make several objects use one mesh (and therefore one material);
* **vary** — derive an independent variant of a material with a tint.

Nothing here deletes anything. The only irreversible operation
(:func:`cleanup_orphans`) is declared as destructive in the tool registry.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import bpy

from . import api, i18n, materials, tools

# Images Blender keeps around; they are never orphans worth purging.
_IMAGE_BLACKLIST = {"Render Result", "Viewer Node"}


# --- helpers ---------------------------------------------------------------


def selected_objects() -> List:
    """The objects selected in the current scene."""
    try:
        return [obj for obj in bpy.context.selected_objects]
    except Exception:
        return []


def scene_objects() -> List:
    """Every object of the current scene."""
    try:
        return list(bpy.context.scene.objects)
    except Exception:
        return []


def _resolve(name) -> Optional[bpy.types.Material]:
    """A material by object, by name, or the active object's material.

    ⚠️ ``str(material)`` is **not** the name in Blender 5.x — it is
    ``<bpy_struct, Material("Wood") at 0x...>``. So a datablock has to be
    recognised as such before it is looked up by name.
    """
    if isinstance(name, bpy.types.Material):
        return name
    if name:
        return bpy.data.materials.get(str(name))
    obj = getattr(bpy.context, "active_object", None)
    return getattr(obj, "active_material", None)


def _color(value, default=(1.0, 1.0, 1.0, 1.0)) -> Tuple:
    """A tint colour from a list, a tuple or a ``#rrggbb`` string."""
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) in (6, 8):
            try:
                parts = [int(text[i:i + 2], 16) / 255.0 for i in range(0, len(text), 2)]
            except ValueError:
                return default
            if len(parts) == 3:
                parts.append(1.0)
            return tuple(parts)
        return default
    if isinstance(value, (list, tuple)):
        parts = [float(v) for v in value[:4]]
        while len(parts) < 4:
            parts.append(1.0)
        return tuple(parts)
    return default


def _socket_by(collection, name: str, socket_type: str):
    """The socket *name* of the given *socket_type*.

    Blender 4.x gives ``ShaderNodeMix`` several sockets with the same name
    (A and B for float, vector, colour and rotation), so the name alone is
    not enough — the type has to be checked too.
    """
    for socket in collection:
        if socket.name == name and socket.type == socket_type:
            return socket
    return None


# --- assign ----------------------------------------------------------------


def assign_to_selection(material=None, objects: Optional[Sequence] = None) -> List[str]:
    """Put *material* on the selected objects (or on *objects*).

    The active material slot is replaced when the object already has
    materials, and appended otherwise — the same rule the "PBR from folder"
    button uses.
    """
    mat = _resolve(material)
    if mat is None:
        return []
    targets = list(objects) if objects is not None else selected_objects()
    changed = []
    for obj in targets:
        if materials.assign_material(obj, mat):
            changed.append(obj.name)
    return changed


# --- replace ---------------------------------------------------------------


def replace_everywhere(old, new, objects: Optional[Sequence] = None) -> List[str]:
    """Replace the material *old* with *new* everywhere.

    The replacement happens **in the slots**, so the face assignments
    (``polygon.material_index``) stay valid: an object that had the old
    material in slot 1 still has it in slot 1, only the slot now points at the
    new material.
    """
    old_mat = _resolve(old)
    new_mat = _resolve(new)
    if old_mat is None or new_mat is None or old_mat == new_mat:
        return []
    targets = list(objects) if objects is not None else scene_objects()
    changed = []
    for obj in targets:
        slots = getattr(getattr(obj, "data", None), "materials", None)
        if slots is None:
            continue
        touched = False
        for index in range(len(slots)):
            if slots[index] == old_mat:
                slots[index] = new_mat
                touched = True
        if touched:
            changed.append(obj.name)
    return changed


def find_users(material=None) -> List[Tuple[str, int, int]]:
    """Who uses a material: ``(object, slot, faces)`` for every user."""
    mat = _resolve(material)
    if mat is None:
        return []
    users = []
    for obj in scene_objects():
        data = getattr(obj, "data", None)
        slots = getattr(data, "materials", None)
        if slots is None:
            continue
        polygons = getattr(data, "polygons", None)
        for index, slot in enumerate(slots):
            if slot != mat:
                continue
            faces = 0
            if polygons is not None:
                faces = sum(1 for poly in polygons if poly.material_index == index)
            users.append((obj.name, index, faces))
    return users


# --- share -----------------------------------------------------------------


def make_instance(source, targets: Optional[Sequence] = None) -> List[str]:
    """Make the target objects share the mesh data of *source*.

    After this the objects are **instances**: editing the mesh edits all of
    them. The material lives on the mesh, so they share the material too.
    """
    if source is None:
        return []
    targets = list(targets) if targets is not None else selected_objects()
    changed = []
    for obj in targets:
        if obj is source or obj.type != source.type:
            continue
        if obj.data is source.data:
            continue
        obj.data = source.data
        changed.append(obj.name)
    return changed


def make_single_user(targets: Optional[Sequence] = None) -> List[str]:
    """Give every target object its own copy of the mesh data.

    The opposite of :func:`make_instance`: the objects stop sharing.
    """
    targets = list(targets) if targets is not None else selected_objects()
    changed = []
    for obj in targets:
        data = getattr(obj, "data", None)
        if data is None:
            continue
        try:
            obj.data = data.copy()
        except Exception:  # noqa: BLE001 - not every data type is copyable
            continue
        changed.append(obj.name)
    return changed


# --- vary ------------------------------------------------------------------


def insert_tint(material, tint=(1.0, 1.0, 1.0, 1.0)) -> bool:
    """Multiply the Base Color of *material* by *tint*.

    A multiply keeps the texture and shifts its colour, which is what a
    "variant" usually means: the same wood, a bit redder.
    """
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return False
    bsdf = api.find_principled(tree)
    if bsdf is None:
        return False
    base = api.get_input(bsdf, "Base Color")
    if base is None:
        return False

    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.name = "Variant Tint"
    mix.location = (bsdf.location.x - 250, bsdf.location.y - 300)

    factor = _socket_by(mix.inputs, "Factor", "VALUE")
    in_a = _socket_by(mix.inputs, "A", "RGBA")
    in_b = _socket_by(mix.inputs, "B", "RGBA")
    result = _socket_by(mix.outputs, "Result", "RGBA")
    if factor is None or in_a is None or in_b is None or result is None:
        return False
    factor.default_value = 1.0

    # Move whatever fed Base Color into A.
    links = list(base.links)
    if links:
        source_socket = links[0].from_socket
        for link in links:
            tree.links.remove(link)
        tree.links.new(source_socket, in_a)
    else:
        in_a.default_value = tuple(base.default_value)

    rgb = tree.nodes.new("ShaderNodeRGB")
    rgb.name = "Tint"
    rgb.location = (mix.location.x - 250, mix.location.y - 180)
    rgb.outputs["Color"].default_value = tint
    tree.links.new(rgb.outputs["Color"], in_b)
    tree.links.new(result, base)
    return True


def make_variant(master=None, name: str = "", tint=None):
    """Derive a new material from *master* with a colour tint.

    The variant is an **independent copy**: it has its own node tree and can
    be edited freely, while the images behind it stay shared with the master.
    For "one material on many objects" use assignment or instancing instead —
    that shares the whole material rather than copying it.
    """
    mat = _resolve(master)
    if mat is None:
        return None
    variant = mat.copy()
    if name:
        variant.name = name
    insert_tint(variant, _color(tint))
    return variant


# --- cleanup ---------------------------------------------------------------


def cleanup_orphans() -> List[str]:
    """Remove the materials and images nothing uses.

    ⚠️ Irreversible. The tool is registered as destructive, so the model
    never runs it on its own.
    """
    removed = []
    for mat in list(bpy.data.materials):
        try:
            if mat.users == 0 and not mat.use_fake_user:
                removed.append(mat.name)
                bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            continue
    for image in list(bpy.data.images):
        if image.name in _IMAGE_BLACKLIST:
            continue
        try:
            if image.users == 0 and not image.use_fake_user:
                removed.append(image.name)
                bpy.data.images.remove(image)
        except Exception:  # noqa: BLE001
            continue
    return removed


# --- the tools -------------------------------------------------------------
#
# The reports are English: their text goes into the model's context.


@tools.tool(
    "materials.assign_to_selection",
    "Assign to selection",
    "Put a material on every selected object. Without a name it uses the "
    "material of the active object.",
    params=("material",),
)
def assign_to_selection_tool(material=None, **kwargs) -> str:
    """Tool entry point: assign a material to the selection."""
    mat = _resolve(material)
    if mat is None:
        return (
            "No material to assign. Name one, or select an object that has a "
            "material and ask again."
        )
    changed = assign_to_selection(mat)
    if not changed:
        return "Nothing is selected, so no material was assigned."
    return f"Assigned '{mat.name}' to {len(changed)} objects: {', '.join(changed)}"


@tools.tool(
    "materials.replace_everywhere",
    "Replace material everywhere",
    "Replace one material with another everywhere it is used, in its slots, so "
    "the face assignments stay valid.",
    params=("old", "new"),
)
def replace_everywhere_tool(old=None, new=None, **kwargs) -> str:
    """Tool entry point: replace a material across the scene."""
    old_mat = _resolve(old)
    new_mat = _resolve(new)
    if old_mat is None or new_mat is None:
        return "Name both materials: the one to replace and the one to use."
    changed = replace_everywhere(old_mat, new_mat)
    if not changed:
        return f"'{old_mat.name}' is not used by any object in the scene."
    return f"Replaced '{old_mat.name}' with '{new_mat.name}' on {len(changed)} objects."


@tools.tool(
    "materials.find_users",
    "Find material users",
    "List the objects that use a material, with the slot index and the number "
    "of faces in it.",
    params=("material",),
)
def find_users_tool(material=None, **kwargs) -> str:
    """Tool entry point: who uses a material."""
    mat = _resolve(material)
    if mat is None:
        return "Name a material, or select an object that has one."
    users = find_users(mat)
    if not users:
        return f"'{mat.name}' is not used by any object in the scene."
    lines = [f"'{mat.name}' is used by {len(users)} slot(s):"]
    for name, index, faces in users:
        lines.append(f"- {name}: slot {index}, {faces} faces")
    return "\n".join(lines)


@tools.tool(
    "materials.make_instance",
    "Make instances",
    "Make the selected objects share the mesh data of the active object, so "
    "they become instances of it (and share its material).",
)
def make_instance_tool(**kwargs) -> str:
    """Tool entry point: share the mesh data."""
    source = getattr(bpy.context, "active_object", None)
    if source is None:
        return "Select the object to make instances of first."
    changed = make_instance(source)
    if not changed:
        return "Nothing changed: the selection already shares that mesh."
    return f"Made {len(changed)} objects instances of '{source.name}'."


@tools.tool(
    "materials.make_single_user",
    "Make single user",
    "Give every selected object its own copy of the mesh data, so they stop "
    "sharing it.",
)
def make_single_user_tool(**kwargs) -> str:
    """Tool entry point: break the mesh sharing."""
    changed = make_single_user()
    if not changed:
        return "Nothing to do: the selection is empty."
    return f"Gave {len(changed)} objects their own copy of the mesh."


@tools.tool(
    "materials.make_variant",
    "Make a variant",
    "Derive a new material from an existing one with a colour tint: an "
    "independent copy whose Base Color is multiplied by the tint.",
    params=("master", "name", "tint"),
)
def make_variant_tool(master=None, name="", tint=None, **kwargs) -> str:
    """Tool entry point: derive a tinted variant of a material."""
    mat = _resolve(master)
    if mat is None:
        return "Name the material to derive the variant from."
    variant = make_variant(mat, name or f"{mat.name}_variant", tint)
    if variant is None:
        return "Could not create the variant."
    colour = _color(tint)
    return (
        f"Created variant '{variant.name}' from '{mat.name}' with tint "
        f"({colour[0]:.2f}, {colour[1]:.2f}, {colour[2]:.2f})."
    )


def _preview_orphans(params=None) -> str:
    """What the cleanup would remove, named one by one."""
    materials = []
    images = []
    for mat in bpy.data.materials:
        try:
            if mat.users == 0 and not mat.use_fake_user:
                materials.append(mat.name)
        except Exception:  # noqa: BLE001
            continue
    for image in bpy.data.images:
        if image.name in ("Render Result", "Viewer Node"):
            continue
        try:
            if image.users == 0 and not image.use_fake_user:
                images.append(image.name)
        except Exception:  # noqa: BLE001
            continue
    parts = []
    if materials:
        parts.append(
            "{0} material(s): {1}".format(len(materials), ", ".join(materials[:5]))
        )
    if images:
        parts.append("{0} image(s): {1}".format(len(images), ", ".join(images[:5])))
    return "; ".join(parts) or "nothing would be removed"


@tools.tool(
    "materials.cleanup_orphans",
    "Clean up orphans",
    "Delete the materials and images that nothing uses. This removes data "
    "irreversibly.",
    destructive=True,
    preview=_preview_orphans,
)
def cleanup_orphans_tool(**kwargs) -> str:
    """Tool entry point: purge the unused data."""
    removed = cleanup_orphans()
    if not removed:
        return "Nothing to clean up."
    return f"Removed {len(removed)} unused datablocks: {', '.join(removed)}"
