"""Turn texture-coordinate chains into one UV layer per object.

A material can sample an image with object-space coordinates and a Mapping
node. Blender evaluates that graph; a game engine cannot - it only reads UVs.
This module evaluates the same affine chain per mesh loop and writes the
result into a single UV layer. Every image node of the object is then pointed
at that layer.

Why one combined layer, not one layer per material
--------------------------------------------------
FBX has a per-texture ``UVSet`` property, but Blender's exporter hard-codes it
to ``"default"`` (see ``export_fbx_bin.py``: ``uvset = None``). An engine
therefore samples the **first** UV set, whatever the material says. A second
UV layer would arrive, but the wallpaper would still be sampled with the
wood's UVs.

So the layer is built per **face**: the face knows its material, the material
knows its coordinate chain, and the loop gets the UV that chain produces.
Faces of a plain texture material keep their original UVs. One UV set is then
correct for every material on the object, and both FBX and glTF carry it.

Why this is exact
-----------------
An affine chain (Mapping: location / rotation / scale) is linear in the vertex
position, and UV interpolation is linear too. Moving the chain into the UV
layer loses nothing: no bake, no resolution loss, and tiling survives because
UVs may leave the 0..1 square. For a non-affine chain (vector math, normal
coordinates, box projection) the only honest answer is a bake, so those are
reported as skipped.

The conversion is reversible: :func:`convert` returns a session whose
:meth:`Conversion.restore` removes the layer and nodes it added and puts the
material links back. Export uses that, so pressing Export never edits the
.blend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import bpy
from mathutils import Euler, Matrix, Vector

# The Mapping node's vector type decides the transform. POINT is the plain
# affine matrix; TEXTURE is its inverse (Blender's "texture space"); VECTOR
# ignores the translation. NORMAL is an inverse-transpose and is not a point,
# so it cannot be projected into UVs - it is skipped.
_SUPPORTED_TYPES = ("POINT", "TEXTURE", "VECTOR")

# Image projections that are a plain 2D lookup. BOX/SPHERE/TUBE are not.
_FLAT = "FLAT"


@dataclass
class _Chain:
    """One coordinate chain: where it starts and how it is transformed."""

    kind: str                  # "UV", "OBJECT", "GENERATED" or "NONE"
    uv_layer: str              # only for kind == "UV" and a named layer
    matrix: Matrix
    image_nodes: List = field(default_factory=list)

    def key(self) -> tuple:
        return (
            self.kind,
            self.uv_layer,
            tuple(round(value, 7) for row in self.matrix for value in row),
        )


@dataclass
class Report:
    """What the conversion did, in a form the UI and the AI can show."""

    converted: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    layers: List[str] = field(default_factory=list)

    def text(self) -> str:
        lines = []
        if self.converted:
            lines.append("Moved into UVs: " + ", ".join(self.converted))
        if self.skipped:
            lines.append("Skipped (needs a bake):")
            lines.extend(f"- {item}" for item in self.skipped)
        if not lines:
            return "There is no texture mapping to move into UVs."
        return "\n".join(lines)


def _matrix_for(node) -> Optional[Matrix]:
    """The matrix a Mapping node applies, or ``None`` when it is not constant."""
    try:
        if node.vector_type not in _SUPPORTED_TYPES:
            return None
        for name in ("Location", "Rotation", "Scale"):
            socket = node.inputs.get(name)
            if socket is not None and socket.is_linked:
                return None
        # A VECTOR mapping has no Location socket: its translation is zero.
        location_socket = node.inputs.get("Location")
        location = Vector(location_socket.default_value) if location_socket else Vector()
        rotation = Vector(node.inputs["Rotation"].default_value)
        scale = Vector(node.inputs["Scale"].default_value)
        if any(abs(value) < 1e-9 for value in scale):
            return None
        point = (
            Matrix.Translation(location)
            @ Euler(rotation, "XYZ").to_matrix().to_4x4()
            @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
        )
        if node.vector_type == "TEXTURE":
            return point.inverted()
        if node.vector_type == "VECTOR":
            return point.to_3x3().to_4x4()  # translation is ignored
        return point
    except Exception:  # noqa: BLE001 - an odd node must not kill the export
        return None


def _trace(socket) -> Optional[_Chain]:
    """Walk back from an image's Vector input to the coordinate source."""
    mappings = []
    seen = set()
    current = socket
    while current.links:
        link = current.links[0]
        node = link.from_node
        if node in seen:  # a cycle
            return None
        seen.add(node)

        if node.type == "MAPPING":
            matrix = _matrix_for(node)
            if matrix is None:
                return None
            mappings.append(matrix)
            current = node.inputs["Vector"]
            continue

        if node.type == "TEX_COORD":
            output = link.from_socket.name
            if output == "UV":
                kind, uv_name = "UV", ""
            elif output == "Object":
                kind, uv_name = "OBJECT", ""
            elif output == "Generated":
                kind, uv_name = "GENERATED", ""
            else:
                return None
            break

        if node.type == "UVMAP":
            kind, uv_name = "UV", getattr(node, "uv_map", "")
            break

        # Vector math, noise, a constant... not an affine coordinate.
        return None
    else:
        # No link at all: Blender samples the active UV map.
        return _Chain("UV", "", Matrix.Identity(4))

    combined = Matrix.Identity(4)
    for matrix in reversed(mappings):
        combined = matrix @ combined
    return _Chain(kind=kind, uv_layer=uv_name, matrix=combined)


def _material_chain(material):
    """The one chain every image of *material* uses, or ``(None, reason)``.

    A single UV set can serve a material only when all of its image nodes agree
    on the coordinate chain. Base mapped with Object and normal mapped with UV
    need two different UVs on the same face - that needs a bake, not a UV map.
    """
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return _Chain("NONE", "", Matrix.Identity(4)), ""
    images = [node for node in tree.nodes if node.type == "TEX_IMAGE"]
    if not images:
        return _Chain("NONE", "", Matrix.Identity(4)), ""

    found = {}
    for node in images:
        if getattr(node, "projection", _FLAT) != _FLAT:
            return None, f"{material.name}/{node.name}: projection is not flat"
        vector = node.inputs.get("Vector")
        if vector is None or not vector.links:
            chain = _Chain("UV", "", Matrix.Identity(4))
        else:
            chain = _trace(vector)
            if chain is None:
                return None, (
                    f"{material.name}/{node.name}: coordinate chain is not affine"
                )
        found.setdefault(chain.key(), chain).image_nodes.append(node)

    if len(found) > 1:
        return None, (
            f"{material.name}: its maps use different coordinate chains "
            f"(one UV set cannot serve them)"
        )
    chain = next(iter(found.values()))
    return chain, ""


def _active_uv(mesh):
    for layer in mesh.uv_layers:
        if getattr(layer, "active_render", False):
            return layer
    if mesh.uv_layers.active is not None:
        return mesh.uv_layers.active
    return mesh.uv_layers[0] if len(mesh.uv_layers) else None


def _bounds(mesh) -> Tuple[Vector, Vector]:
    if not len(mesh.vertices):
        return Vector((0.0, 0.0, 0.0)), Vector((1.0, 1.0, 1.0))
    xs = [vertex.co.x for vertex in mesh.vertices]
    ys = [vertex.co.y for vertex in mesh.vertices]
    zs = [vertex.co.z for vertex in mesh.vertices]
    return (
        Vector((min(xs), min(ys), min(zs))),
        Vector((max(xs), max(ys), max(zs))),
    )


def _generated(co: Vector, low: Vector, high: Vector) -> Vector:
    """The same 0..1 box coordinates Blender's Generated output gives."""
    out = []
    for axis in range(3):
        extent = high[axis] - low[axis]
        # A flat axis has no extent; Blender returns the middle of the range.
        out.append(0.5 if abs(extent) < 1e-9 else (co[axis] - low[axis]) / extent)
    return Vector(out)


def _loop_coords(mesh, chain: _Chain, original_uv) -> Optional[List[Vector]]:
    """Evaluate *chain* for every loop. ``original_uv`` is a list of pairs."""
    if chain.kind == "UV":
        if chain.uv_layer:
            layer = mesh.uv_layers.get(chain.uv_layer)
            if layer is None:
                return None
            source = [Vector((data.uv[0], data.uv[1], 0.0)) for data in layer.data]
        else:
            if original_uv is None:
                return None
            source = [Vector((u, v, 0.0)) for u, v in original_uv]
    elif chain.kind in ("OBJECT", "GENERATED"):
        if not len(mesh.vertices):
            return None
        low, high = _bounds(mesh)
        source = []
        for loop in mesh.loops:
            co = mesh.vertices[loop.vertex_index].co
            if chain.kind == "GENERATED":
                source.append(_generated(co, low, high))
            else:
                source.append(co.copy())
    else:  # "NONE" - the material does not sample an image
        if original_uv is None:
            return None
        source = [Vector((u, v, 0.0)) for u, v in original_uv]

    return [chain.matrix @ point for point in source]


def _snapshot_layers(mesh):
    """The UV layers as (name, data, active, active_render)."""
    active = mesh.uv_layers.active.name if mesh.uv_layers.active else None
    out = []
    for layer in mesh.uv_layers:
        out.append(
            (
                layer.name,
                [tuple(data.uv) for data in layer.data],
                layer.name == active,
                bool(getattr(layer, "active_render", False)),
            )
        )
    return out


def _restore_layers(mesh, snapshot) -> None:
    for layer in list(mesh.uv_layers):
        mesh.uv_layers.remove(layer)
    for name, data, _active, _render in snapshot:
        layer = mesh.uv_layers.new(name=name)
        for index, uv in enumerate(data):
            layer.data[index].uv = uv
    for name, _data, active, render in snapshot:
        layer = mesh.uv_layers.get(name)
        if layer is None:
            continue
        if active:
            mesh.uv_layers.active = layer
        if render:
            layer.active_render = True


def _unique_name(objects: Sequence, base: str) -> str:
    """A UV-layer name that does not exist on any of *objects*."""
    letters = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_") or "Material"
    name = base if base.startswith("GAA_") else f"GAA_{letters[:40]}"
    index = 1
    while any(obj.data.uv_layers.get(name) for obj in objects):
        name = f"GAA_{letters[:36]}_{index:03d}"
        index += 1
    return name


def _materials_of(obj) -> set:
    return {
        slot.material
        for slot in getattr(obj, "material_slots", [])
        if slot.material is not None
    }


def _users(material) -> List:
    out = []
    for obj in getattr(bpy.context.scene, "objects", []):
        if getattr(obj, "type", "") != "MESH":
            continue
        for slot in getattr(obj, "material_slots", []):
            if slot.material == material:
                out.append(obj)
                break
    return out


def _object_plan(obj, chains_by_material):
    """Evaluate the chain of every face of *obj*.

    Returns ``(cache, per_face)`` where ``cache`` maps a chain key to its loop
    coordinates, or ``(None, reason)``. An empty reason means the material was
    already reported as unsupported.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "polygons"):
        return None, f"{obj.name}: not a mesh"
    if not len(mesh.uv_layers):
        return None, f"{obj.name}: no UV layer to start from"

    slots = list(getattr(obj, "material_slots", []))
    per_face = []
    for polygon in mesh.polygons:
        index = polygon.material_index
        material = slots[index].material if 0 <= index < len(slots) else None
        if material is None:
            per_face.append(None)
            continue
        chain = chains_by_material.get(material)
        if chain is None:
            return None, ""
        per_face.append(chain)

    original_layer = _active_uv(mesh)
    if original_layer is None:
        return None, f"{obj.name}: no active UV layer"
    original_uv = [tuple(data.uv) for data in original_layer.data]

    by_key = {}
    for chain in per_face:
        if chain is not None:
            by_key.setdefault(chain.key(), chain)
    cache = {}
    for key, chain in by_key.items():
        coords = _loop_coords(mesh, chain, original_uv)
        if coords is None:
            return None, f"{obj.name}: cannot evaluate {chain.kind} coordinates"
        cache[key] = coords

    return cache, per_face


def convert(
    objects: Optional[Sequence] = None,
    prefix: str = "EXPORT",
    only_layer: bool = True,
) -> "Conversion":
    """Move constant Mapping / TexCoord chains into one UV layer per object.

    ``objects`` limits the meshes to prepare; shared materials are still
    handled on every object that uses them, otherwise the material would
    point at a UV layer that only some users have.

    ``only_layer`` removes the other UV layers for the duration of the export.
    FBX textures always sample the first UV set, so the prepared layer has to
    be the only one. The session restores every layer afterwards.
    """
    report = Report()
    requested = [
        obj
        for obj in (objects if objects is not None else bpy.context.scene.objects)
        if getattr(obj, "type", "") == "MESH"
    ]

    # A material may be shared with an object outside the selection; preparing
    # all users keeps the material valid everywhere while the export runs.
    materials = []
    for obj in requested:
        for slot in getattr(obj, "material_slots", []):
            material = slot.material
            if material is not None and material not in materials:
                materials.append(material)

    users = []
    for material in materials:
        for obj in _users(material):
            if obj not in users:
                users.append(obj)

    session = Conversion(report=report)
    chains_by_material = {}
    blocked = set()
    for material in materials:
        chain, reason = _material_chain(material)
        if chain is None:
            blocked.add(material)
            report.skipped.append(reason)
            continue
        chains_by_material[material] = chain

    # Nothing to do when no material has a non-trivial chain.
    if not any(
        chain.kind in ("OBJECT", "GENERATED")
        or (chain.kind == "UV" and chain.matrix != Matrix.Identity(4))
        for chain in chains_by_material.values()
    ):
        return session

    layer_name = _unique_name(users, prefix)
    evaluated = {}
    for obj in users:
        cache, per_face = _object_plan(obj, chains_by_material)
        if cache is None:
            if per_face:
                report.skipped.append(per_face)
            blocked.update(_materials_of(obj))
        else:
            evaluated[obj] = (cache, per_face)

    # A material that is not rewired must keep its own UVs on every object it
    # sits on, so such an object is left out too. Repeat until nothing changes.
    changed = True
    while changed:
        changed = False
        for obj in list(evaluated):
            if blocked & _materials_of(obj):
                del evaluated[obj]
                blocked.update(_materials_of(obj))
                changed = True

    prepared = [(obj, *evaluated[obj]) for obj in users if obj in evaluated]
    if not prepared:
        return session

    # Build the layer on every prepared object. The name is shared: one
    # UVMap node in the material serves all objects.
    for obj, cache, per_face in prepared:
        mesh = obj.data
        session._snapshots.append((obj, _snapshot_layers(mesh)))
        layer = mesh.uv_layers.get(layer_name)
        if layer is None:
            layer = mesh.uv_layers.new(name=layer_name)
            session._created_layers.append((obj, layer_name))
        try:
            layer.active_render = True
            mesh.uv_layers.active = layer
        except Exception:
            pass
        for polygon in mesh.polygons:
            chain = per_face[polygon.index]
            if chain is None:
                continue
            coords = cache.get(chain.key())
            if coords is None:
                continue
            for loop_index in polygon.loop_indices:
                point = coords[loop_index]
                layer.data[loop_index].uv = (point.x, point.y)

    # Point every image node of the prepared materials at the new layer. A
    # material shared with a failed object is left alone: it has no layer
    # there, and changing it would break that object.
    for material in materials:
        if material in blocked:
            continue
        tree = getattr(material, "node_tree", None)
        if tree is None:
            continue
        images = [node for node in tree.nodes if node.type == "TEX_IMAGE"]
        if not images:
            continue
        uv_node = tree.nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = layer_name
        uv_node.label = f"UV: {layer_name}"
        first = images[0].location
        uv_node.location = (first[0] - 260.0, first[1] - 120.0)
        session._nodes.append((tree, uv_node))
        for node in images:
            vector = node.inputs.get("Vector")
            if vector is None:
                continue
            old = vector.links[0].from_socket if vector.links else None
            for link in list(vector.links):
                tree.links.remove(link)
            tree.links.new(uv_node.outputs["UV"], vector)
            session._links.append((tree, vector, old))
        report.converted.append(f"{material.name} -> {layer_name}")

    if only_layer:
        for obj, _cache, _per_face in prepared:
            mesh = obj.data
            for layer in list(mesh.uv_layers):
                if layer.name != layer_name:
                    mesh.uv_layers.remove(layer)

    report.layers.append(layer_name)
    return session


class Conversion:
    """The result of :func:`convert`, and the way back."""

    def __init__(self, report: Report):
        self.report = report
        self._nodes: List = []
        self._created_layers: List = []
        self._links: List = []
        self._snapshots: List = []
        self._restored = False

    def restore(self) -> None:
        """Undo everything :func:`convert` changed in the scene."""
        if self._restored:
            return
        self._restored = True
        for tree, socket, old in reversed(self._links):
            try:
                for link in list(socket.links):
                    tree.links.remove(link)
                if old is not None:
                    tree.links.new(old, socket)
            except Exception:  # noqa: BLE001
                continue
        for tree, node in reversed(self._nodes):
            try:
                tree.nodes.remove(node)
            except Exception:  # noqa: BLE001
                continue
        for obj, snapshot in reversed(self._snapshots):
            try:
                _restore_layers(obj.data, snapshot)
            except Exception:  # noqa: BLE001
                continue

