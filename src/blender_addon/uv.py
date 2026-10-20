"""UV analysis: what is wrong with the unwrap of an object.

This is the first stage of the UV work (chapter 19) and it is **read-only**:
it looks and reports, it changes nothing.

Why a separate analyser instead of asking Blender? Because the interesting
questions are the ones Blender does not answer:

* **texel density** — how many pixels of the texture cover one meter of
  surface. It is the one UV problem that is completely invisible in the
  editor and very visible in a game.
* **overlap** — two islands sharing the same place. Blender has
  ``uv.select_overlap``, but it only *highlights*; it does not say which
  islands and by how much.
* **out of bounds** — UV coordinates outside [0, 1]. The operator
  ``uv.select_outside`` was removed in Blender 5.x.
* **distortion** — how much a triangle is stretched compared to its real
  proportions.

Everything is computed on the **bmesh** level, so it is deterministic, works in
``--background`` and does not depend on the UI context. That matters: half of
the UV operators in Blender 5.x are gone or context-dependent (see chapter 19).

The maths
---------

Texel density
    ``density = sqrt(uv_area * texture_pixels / world_area)``

    ``uv_area`` is the island's area in UV space (0..1 of the image),
    ``world_area`` is its area in square metres, and ``texture_pixels`` is the
    pixel count of the texture. The square root turns an area ratio into a
    length ratio, which is the pixels-per-metre figure artists talk about.

    The area ratio is used rather than the bounding box because an island that
    lies diagonally in the UV space has a large box and a small area.

Distortion
    For every triangle, the ratio between its UV area and its world area.
    The island's distortion is the largest of those ratios.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import bmesh
import bpy
from mathutils import Vector
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty

from . import findings, tools

# Finding codes.
NO_UV = "uv_no_uv"
OVERLAP = "uv_overlap"
OUT_OF_BOUNDS = "uv_out_of_bounds"
DENSITY_MISMATCH = "uv_density_mismatch"
LOW_DENSITY = "uv_low_density"
HIGH_DENSITY = "uv_high_density"
STRETCH = "uv_stretch"
NO_TEXTURE = "uv_no_texture"
DENSITY_SPREAD = "uv_density_spread"
NO_SEAMS = "uv_no_seams"

# A density below this (pixels per metre) looks blurry on screen.
MIN_SENSIBLE_DENSITY = 4.0
# A density above this wastes memory without being visible.
MAX_SENSIBLE_DENSITY = 512.0
# Distortion above this means the texture is visibly stretched.
MAX_SENSIBLE_STRETCH = 2.0


@dataclass
class Island:
    """One island of the unwrap."""

    faces: int = 0
    uv_area: float = 0.0
    world_area: float = 0.0
    uv_min: Tuple[float, float] = (0.0, 0.0)
    uv_max: Tuple[float, float] = (0.0, 0.0)
    world_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    world_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    density: Optional[float] = None      # pixels per metre
    texel_size: Optional[float] = None    # metres per pixel
    distortion: float = 1.0
    overlaps: List[int] = field(default_factory=list)

    @property
    def size_uv(self) -> Tuple[float, float]:
        return (self.uv_max[0] - self.uv_min[0], self.uv_max[1] - self.uv_min[1])

    @property
    def size_world(self) -> Tuple[float, float, float]:
        return (
            self.world_max[0] - self.world_min[0],
            self.world_max[1] - self.world_min[1],
            self.world_max[2] - self.world_min[2],
        )


@dataclass
class UVReport:
    """Everything the analyser found about one object."""

    object: str = ""
    islands: List[Island] = field(default_factory=list)
    outside: int = 0              # UV coordinates outside [0, 1]
    texture: str = ""             # the texture the density refers to
    texture_size: Tuple[int, int] = (0, 0)
    has_uv: bool = False

    @property
    def densities(self) -> List[float]:
        return [i.density for i in self.islands if i.density is not None]

    @property
    def average_density(self) -> Optional[float]:
        values = self.densities
        if not values:
            return None
        return sum(values) / len(values)

    @property
    def max_distortion(self) -> float:
        return max((i.distortion for i in self.islands), default=1.0)

    @property
    def overlap_pairs(self) -> List[Tuple[int, int]]:
        pairs = []
        for index, island in enumerate(self.islands):
            for other in island.overlaps:
                pairs.append((index, other))
        return pairs


# --- helpers ---------------------------------------------------------------


def _triangle_area_2d(a, b, c) -> float:
    """The area of a triangle in the plane."""
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) * 0.5


def _triangle_area_3d(a, b, c) -> float:
    """The area of a triangle in space (the magnitude of the cross product)."""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return math.sqrt(cx * cx + cy * cy + cz * cz) * 0.5


def blend_dir() -> str:
    """The folder of the current .blend file ("" when it was never saved)."""
    path = bpy.data.filepath
    return os.path.dirname(path) if path else ""


def object_texture(obj) -> Tuple[str, Tuple[int, int]]:
    """The base-colour texture of *obj* and its pixel size.

    The density can only be expressed in pixels per metre when we know how big
    the texture is; the base colour map is the natural reference because it is
    the one that defines the layout.
    """
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if not slots:
        return ("", (0, 0))
    for slot in slots:
        mat = slot
        if mat is None:
            continue
        tree = getattr(mat, "node_tree", None)
        if tree is None:
            continue
        for node in tree.nodes:
            if node.type != "TEX_IMAGE":
                continue
            image = getattr(node, "image", None)
            if image is None:
                continue
            width, height = image.size
            if width and height:
                return (image.name, (width, height))
    return ("", (0, 0))


def _is_seam(edge) -> bool:
    """Whether the edge is marked as a seam.

    Blender 5.x renamed ``BMEdge.is_seam`` to ``BMEdge.seam``; the old name
    is kept for 4.x.
    """
    value = getattr(edge, "seam", None)
    if value is None:
        value = getattr(edge, "is_seam", False)
    return bool(value)


def _islands(bm, uv_layer, matrix) -> List[List]:
    """The connected groups of faces, split along seams.

    A seam is an edge marked as such; faces across a seam belong to different
    islands. Without seams the whole mesh is one island.
    """
    bm.faces.ensure_lookup_table()
    seen = set()
    groups = []
    for face in bm.faces:
        if face.index in seen:
            continue
        group = []
        stack = [face]
        seen.add(face.index)
        while stack:
            current = stack.pop()
            group.append(current)
            for edge in current.edges:
                if _is_seam(edge):
                    continue
                for neighbour in edge.link_faces:
                    if neighbour.index not in seen:
                        seen.add(neighbour.index)
                        stack.append(neighbour)
        groups.append(group)
    return groups


def analyze(obj, texture_size: Optional[Tuple[int, int]] = None) -> UVReport:
    """Measure the unwrap of *obj*. Changes nothing."""
    report = UVReport(object=obj.name)
    mesh = getattr(obj, "data", None)
    if mesh is None or not getattr(mesh, "uv_layers", None):
        return report
    report.has_uv = True

    if texture_size is None:
        name, size = object_texture(obj)
        report.texture = name
        report.texture_size = size
    else:
        report.texture_size = texture_size

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return report
        matrix = obj.matrix_world

        pixels = report.texture_size[0] * report.texture_size[1]

        for group in _islands(bm, uv_layer, matrix):
            island = Island(faces=len(group))
            uv_min = [1e30, 1e30]
            uv_max = [-1e30, -1e30]
            world_min = [1e30, 1e30, 1e30]
            world_max = [-1e30, -1e30, -1e30]
            ratios = []

            for face in group:
                loops = face.loops
                if len(loops) < 3:
                    continue
                uv_points = []
                world_points = []
                for loop in loops:
                    uv = loop[uv_layer].uv
                    uv_points.append((uv.x, uv.y))
                    world = matrix @ loop.vert.co
                    world_points.append((world.x, world.y, world.z))
                    if uv.x < 0.0 or uv.x > 1.0 or uv.y < 0.0 or uv.y > 1.0:
                        report.outside += 1
                    uv_min[0] = min(uv_min[0], uv.x)
                    uv_min[1] = min(uv_min[1], uv.y)
                    uv_max[0] = max(uv_max[0], uv.x)
                    uv_max[1] = max(uv_max[1], uv.y)
                    world_min[0] = min(world_min[0], world.x)
                    world_min[1] = min(world_min[1], world.y)
                    world_min[2] = min(world_min[2], world.z)
                    world_max[0] = max(world_max[0], world.x)
                    world_max[1] = max(world_max[1], world.y)
                    world_max[2] = max(world_max[2], world.z)

                # The face area: Blender's own value is in object space, so
                # scale it by the determinant of the matrix.
                area_uv = 0.0
                for i in range(1, len(uv_points) - 1):
                    area_uv += _triangle_area_2d(
                        uv_points[0], uv_points[i], uv_points[i + 1]
                    )
                area_world = 0.0
                for i in range(1, len(world_points) - 1):
                    area_world += _triangle_area_3d(
                        world_points[0], world_points[i], world_points[i + 1]
                    )
                island.uv_area += area_uv
                island.world_area += area_world
                if area_world > 1e-12 and area_uv > 1e-12:
                    ratios.append(area_uv / area_world)

            if uv_min[0] > uv_max[0]:
                continue  # no usable loops
            island.uv_min = (uv_min[0], uv_min[1])
            island.uv_max = (uv_max[0], uv_max[1])
            island.world_min = tuple(world_min)
            island.world_max = tuple(world_max)
            if ratios:
                island.distortion = max(ratios) / min(ratios)
            if pixels > 0 and island.world_area > 1e-12 and island.uv_area > 1e-12:
                island.density = math.sqrt(
                    (island.uv_area * pixels) / island.world_area
                )
                island.texel_size = 1.0 / island.density
            report.islands.append(island)
    finally:
        bm.free()

    _find_overlaps(report)
    return report


def _find_overlaps(report: UVReport) -> None:
    """Mark the islands whose UV boxes intersect.

    A box test is conservative: it can report an overlap that is not real
    (two diagonal islands whose boxes cross but whose polygons do not), and it
    never misses a real one. That is the right way round - a false alarm is
    annoying, a missed overlap is a bug in the render.
    """
    islands = report.islands
    for i, first in enumerate(islands):
        for j in range(i + 1, len(islands)):
            second = islands[j]
            if (
                first.uv_min[0] < second.uv_max[0]
                and second.uv_min[0] < first.uv_max[0]
                and first.uv_min[1] < second.uv_max[1]
                and second.uv_min[1] < first.uv_max[1]
            ):
                first.overlaps.append(j)
                second.overlaps.append(i)


def scene_density() -> Dict[str, float]:
    """The average texel density of every object of the scene, in px/m."""
    out = {}
    try:
        objects = list(bpy.context.scene.objects)
    except Exception:
        return out
    for obj in objects:
        if obj.type != "MESH":
            continue
        report = analyze(obj)
        average = report.average_density
        if average:
            out[obj.name] = average
    return out


# --- the settings ----------------------------------------------------------
#
# The unwrap is a craft, and every artist has their own numbers. The settings
# live on the **scene**, not in the add-on preferences: a game project wants
# 16 px/m and a 2048 atlas, an archviz project wants 8 px/m and no atlas at
# all. Preferences would force one project to change the settings of another.
#
# Three levels of control:
#
# 1. the scene properties (the defaults for this project);
# 2. the parameters of the tool call (one job, different numbers);
# 3. the assistant, which can read the current settings from the snapshot and
#    ask for a specific angle.


@dataclass
class UVSettings:
    """Everything the unwrap pipeline needs to know."""

    angle: float = 40.0           # degrees: the seam angle
    method: str = "ANGLE_BASED"    # ANGLE_BASED | CONFORMAL | PROJECT_BASED
    margin: float = 0.02          # the gap between the islands, in UV units
    minimize: bool = True          # run uv.minimize_stretch
    smart_fallback: bool = True    # smart_project for smooth closed meshes
    density: float = 16.0          # the target texel density, px per metre
    atlas: int = 2048              # the atlas size, in pixels
    respect_sharp: bool = True     # treat sharp edges as seams
    pack: bool = True             # pack the islands into the UV space
    use_existing_seams: bool = False  # unwrap only along the seams already there


def settings_from_scene(scene=None) -> UVSettings:
    """The settings of the current scene."""
    if scene is None:
        try:
            scene = bpy.context.scene
        except Exception:
            return UVSettings()
    return UVSettings(
        angle=float(getattr(scene, "gaa_uv_angle", 40.0)),
        method=str(getattr(scene, "gaa_uv_method", "ANGLE_BASED")),
        margin=float(getattr(scene, "gaa_uv_margin", 0.02)),
        minimize=bool(getattr(scene, "gaa_uv_minimize", True)),
        smart_fallback=bool(getattr(scene, "gaa_uv_smart_fallback", True)),
        density=float(getattr(scene, "gaa_uv_density", 16.0)),
        atlas=int(getattr(scene, "gaa_uv_atlas", 2048)),
        respect_sharp=bool(getattr(scene, "gaa_uv_respect_sharp", True)),
        pack=bool(getattr(scene, "gaa_uv_pack", True)),
        use_existing_seams=bool(getattr(scene, "gaa_uv_use_existing_seams", False)),
    )


def apply_overrides(settings: UVSettings, **overrides) -> UVSettings:
    """A copy of *settings* with the given fields replaced."""
    data = dict(settings.__dict__)
    for key, value in overrides.items():
        if value is None or key not in data:
            continue
        if key == "atlas":
            data[key] = int(value)
        elif key == "angle":
            data[key] = float(value)
        elif key == "margin":
            data[key] = float(value)
        elif key == "density":
            data[key] = float(value)
        else:
            data[key] = value
    return UVSettings(**data)


# --- the scene properties --------------------------------------------------


def register() -> None:
    """Register the per-scene UV settings."""
    bpy.types.Scene.gaa_uv_angle = FloatProperty(
        name="Швы по углу / Seam angle",
        description=(
            "Рёбра, угол между гранями которых больше этого значения, "
            "становятся швами / Edges whose face angle is larger become seams"
        ),
        default=40.0,
        min=1.0,
        max=180.0,
        soft_max=90.0,
        subtype="ANGLE",
    )
    bpy.types.Scene.gaa_uv_method = EnumProperty(
        name="Метод развёртки / Unwrap method",
        items=[
            ("ANGLE_BASED", "По углу / Angle based", ""),
            ("CONFORMAL", "Конформная / Conformal", ""),
            ("PROJECT_BASED", "Проекционная / Project based", ""),
        ],
        default="ANGLE_BASED",
    )
    bpy.types.Scene.gaa_uv_margin = FloatProperty(
        name="Зазор между островами / Island margin",
        description=(
            "Пустое расстояние между островами в атласе (для мипмапов) / "
            "The empty gap between the islands in the atlas"
        ),
        default=0.02,
        min=0.0,
        max=0.5,
        subtype="FACTOR",
    )
    bpy.types.Scene.gaa_uv_minimize = BoolProperty(
        name="Минимизировать искажения / Minimize stretch",
        default=True,
    )
    bpy.types.Scene.gaa_uv_smart_fallback = BoolProperty(
        name="Smart UV для гладких объектов / Smart UV fallback",
        description=(
            "Если швов не получилось (гладкая замкнутая поверхность), "
            "использовать автоматическую развёртку / Use the automatic "
            "unwrap when no seam could be found"
        ),
        default=True,
    )
    bpy.types.Scene.gaa_uv_respect_sharp = BoolProperty(
        name="Учитывать острые рёбра / Respect sharp edges",
        default=True,
    )
    bpy.types.Scene.gaa_uv_pack = BoolProperty(
        name="Укладывать острова / Pack the islands",
        description=(
            "Уложить острова в UV-пространство с зазором / Lay the islands "
            "out in the UV space with a gap"
        ),
        default=True,
    )
    bpy.types.Scene.gaa_uv_use_existing_seams = BoolProperty(
        name="Только по своим швам / Only along existing seams",
        description=(
            "Не ставить автоматические швы, а развернуть только по уже "
            "размеченным / Do not add automatic seams - unwrap only along the "
            "ones that are already marked"
        ),
        default=False,
    )
    bpy.types.Scene.gaa_uv_symmetry_axis = EnumProperty(
        name="Ось симметрии / Symmetry axis",
        items=[
            ("X", "X", ""),
            ("Y", "Y", ""),
            ("Z", "Z", ""),
        ],
        default="X",
    )
    bpy.types.Scene.gaa_uv_density = IntProperty(
        name="Плотность текселей, px/м / Texel density",
        description=(
            "Сколько пикселей текстуры приходится на метр поверхности / "
            "How many pixels of the texture cover one meter of surface"
        ),
        default=16,
        min=1,
        max=4096,
    )
    bpy.types.Scene.gaa_uv_atlas = EnumProperty(
        name="Размер атласа / Atlas size",
        items=[
            ("512", "512", ""),
            ("1024", "1024", ""),
            ("2048", "2048", ""),
            ("4096", "4096", ""),
        ],
        default="2048",
    )


def unregister() -> None:
    """Remove the per-scene UV settings."""
    for name in (
        "gaa_uv_angle",
        "gaa_uv_method",
        "gaa_uv_margin",
        "gaa_uv_minimize",
        "gaa_uv_smart_fallback",
        "gaa_uv_respect_sharp",
        "gaa_uv_density",
        "gaa_uv_atlas",
        "gaa_uv_pack",
        "gaa_uv_use_existing_seams",
        "gaa_uv_symmetry_axis",
    ):
        try:
            delattr(bpy.types.Scene, name)
        except (AttributeError, RuntimeError):
            pass


# --- the unwrap ------------------------------------------------------------


def _axis_index(axis: str) -> Optional[int]:
    key = (axis or "X").strip().upper()[:1]
    return {"X": 0, "Y": 1, "Z": 2}.get(key)


def _local_plane(obj, axis: str, offset: float):
    """The symmetry plane in the object's **local** space.

    The plane is given in world coordinates (an artist thinks in world space),
    but the mesh data is local. The normal transforms with the inverse
    transpose and the point with the inverse, so a rotated or scaled object
    still gets the right plane.

    Returns ``(normal, distance)``: a point is on the plane when
    ``normal.dot(point) == distance``.
    """
    index = _axis_index(axis)
    if index is None:
        return None
    world_normal = [0.0, 0.0, 0.0]
    world_normal[index] = 1.0
    world_point = [0.0, 0.0, 0.0]
    world_point[index] = float(offset)

    inverse = obj.matrix_world.inverted()
    local_normal = (inverse.transposed() @ Vector(world_normal)).normalized()
    local_point = inverse @ Vector(world_point)
    return local_normal, local_point.dot(local_normal)


def _mirror_point(co, normal, distance):
    """The mirrored position of a point."""
    factor = 2.0 * (normal.dot(co) - distance)
    return co - normal * factor


def mark_symmetry_seams(obj, axis: str = "X", offset: float = 0.0) -> int:
    """Mark the edges that lie on the symmetry plane.

    The plane splits the model in half, and the seam along it is what lets the
    unwrap treat the two halves separately - which is the whole point of a
    symmetric unwrap.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    plane = _local_plane(obj, axis, offset)
    if plane is None:
        return 0
    normal, distance = plane
    tol = 1e-4
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        count = 0
        for edge in bm.edges:
            a = normal.dot(edge.verts[0].co)
            b = normal.dot(edge.verts[1].co)
            if abs(a - distance) <= tol and abs(b - distance) <= tol:
                _set_seam(edge, True)
                count += 1
        bm.to_mesh(mesh)
        return count
    finally:
        bm.free()


def mirror_uvs(obj, axis: str = "X", offset: float = 0.0) -> int:
    """Give the far half of the model the UVs of its counterpart.

    This is the **classic symmetric layout**: both halves use the same region of
    the texture, so one painting serves both sides and the effective
    resolution is doubled. The two halves therefore overlap in UV space **on
    purpose** - such a model gets its own texture, not a place in an atlas.

    The counterpart is found by position: a vertex and its mirror have the same
    two other coordinates. The UVs are copied **unchanged**, because the
    mirroring of the picture comes from the mirrored geometry.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    plane = _local_plane(obj, axis, offset)
    if plane is None:
        return 0
    normal, distance = plane

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return 0
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        # position -> the vertex there. The index is keyed by the REAL
        # position; the lookup below uses the MIRRORED position.
        by_position = {}
        for vert in bm.verts:
            key = tuple(round(c, 5) for c in vert.co)
            by_position.setdefault(key, []).append(vert)
        mirror_of = {}
        for vert in bm.verts:
            key = tuple(
                round(c, 5) for c in _mirror_point(vert.co, normal, distance)
            )
            for other in by_position.get(key, []):
                # NOTE: ``!=`` and not ``is``: bmesh hands out a fresh wrapper
                # on every access, so ``is`` would match a vertex with itself.
                if other != vert:
                    mirror_of[vert] = other
                    break

        # face signature -> face
        by_signature = {}
        for face in bm.faces:
            key = tuple(sorted(v.index for v in face.verts))
            by_signature.setdefault(key, face)

        count = 0
        for face in bm.faces:
            if normal.dot(face.verts[0].co) <= distance + 1e-4:
                continue          # this is the source half
            mirrored_verts = []
            complete = True
            for vert in face.verts:
                other = mirror_of.get(vert)
                if other is None:
                    complete = False
                    break
                mirrored_verts.append(other)
            if not complete:
                continue
            source = by_signature.get(
                tuple(sorted(v.index for v in mirrored_verts))
            )
            if source is None:
                continue
            # Match the corners by their vertex, not by their order: the
            # mirrored face has the opposite winding.
            source_loops = {}
            for loop in source.loops:
                source_loops.setdefault(loop.vert, loop)
            for loop in face.loops:
                other = mirror_of.get(loop.vert)
                if other is None:
                    continue
                source_loop = source_loops.get(other)
                if source_loop is None:
                    continue
                # In bmesh the UV of a corner is written through the
                # ``BMLoopUV`` item: ``loop[layer].uv = value``. The layer
                # itself is not subscriptable.
                loop[uv_layer].uv = source_loop[uv_layer].uv
                count += 1
        bm.to_mesh(mesh)
        return count
    finally:
        bm.free()


def make_symmetric(
    obj,
    axis: str = "X",
    offset: float = 0.0,
    unwrap: bool = True,
    settings=None,
    context=None,
) -> Dict:
    """Make the unwrap of *obj* symmetric.

    Two cases:

    * the object has a **Mirror modifier** - the base mesh is already half the
      model, so the modifier mirrors the geometry and (with "Mirror UVs" off)
      the same texture region serves both sides;
    * the object is a **full mesh** - the UVs of the far half are computed from
      the near half here.
    """
    settings = settings or settings_from_scene()
    report = {
        "object": obj.name,
        "axis": axis,
        "seams": 0,
        "mirrored": 0,
        "mode": "",
        "ok": False,
    }
    if getattr(obj, "type", "") != "MESH":
        report["error"] = "not a mesh"
        return report

    report["seams"] = mark_symmetry_seams(obj, axis, offset)

    if unwrap:
        unwrapped = unwrap_object(obj, settings, context)
        if not unwrapped.get("ok"):
            report["error"] = unwrapped.get("error", "the unwrap failed")
            return report

    modifiers = [m for m in obj.modifiers if m.type == "MIRROR"]
    if modifiers:
        # The modifier mirrors the geometry; "Mirror UVs" would flip the
        # picture, which is the opposite of the classic layout.
        for modifier in modifiers:
            modifier.use_mirror_u = False
        report["mode"] = "mirror_modifier"
        report["mirrored"] = report["seams"]
    else:
        report["mirrored"] = mirror_uvs(obj, axis, offset)
        report["mode"] = "computed"
    report["ok"] = True
    return report


def _set_seam(edge, value: bool) -> None:
    """Mark an edge as a seam, whichever spelling this Blender uses."""
    if hasattr(edge, "seam"):
        edge.seam = value
    else:
        edge.is_seam = value


def _is_closed(mesh) -> bool:
    """True when every edge of the mesh belongs to two faces."""
    counts = {}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            counts[key] = counts.get(key, 0) + 1
    return bool(counts) and all(value == 2 for value in counts.values())


def mark_seams(obj, angle: float = 40.0, respect_sharp: bool = True) -> int:
    """Mark the seams of *obj* automatically; return how many were marked.

    An edge becomes a seam when the angle between its two faces is larger than
    *angle* degrees, or when it is marked sharp and *respect_sharp* is on.

    ⚠️ Seams are only ever **added**: the ones the user marked by hand stay
    where they are. That matters - a hand-placed seam on a sleeve is a
    decision, not a default.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    threshold = math.radians(max(1.0, min(180.0, angle)))
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        count = 0
        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue
            if edge.calc_face_angle(0.0) > threshold:
                _set_seam(edge, True)
                count += 1
            elif respect_sharp and not edge.smooth:
                _set_seam(edge, True)
                count += 1
        bm.to_mesh(mesh)
        return count
    finally:
        bm.free()


def count_seams(obj) -> int:
    """How many seams the object carries."""
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    return sum(1 for edge in mesh.edges if edge.use_seam)


def clear_seams(obj) -> int:
    """Remove every seam of *obj*; return how many were removed."""
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    count = 0
    for edge in mesh.edges:
        if edge.use_seam:
            edge.use_seam = False
            count += 1
    return count


def mark_seams_by_height(obj, height: float, tolerance: float = 0.001) -> int:
    """Cut the object at a given height and mark the new edges as seams.

    A horizontal cut is what a game artist needs for sleeves, boots, belts and
    collars - and it cannot be expressed as an angle, because the edges that
    cross the level are **vertical** ones.

    The mesh is really cut (that is what ``Ctrl+R`` does): the edges that
    appear along the cut become the seams. The cut is in **world** height, so
    it works on a moved or scaled object too.
    """
    from mathutils import Vector

    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    level = float(height)
    # The cut happens in local space: bring the level down to it.
    local = obj.matrix_world.inverted() @ Vector((0.0, 0.0, level))

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        geometry = list(bm.verts) + list(bm.edges) + list(bm.faces)
        result = bmesh.ops.bisect_plane(
            bm,
            geom=geometry,
            dist=1e-5,
            plane_co=local,
            plane_no=(0.0, 0.0, 1.0),
            clear_inner=False,
            clear_outer=False,
        )
        count = 0
        for element in result.get("geom_cut", []):
            if isinstance(element, bmesh.types.BMEdge):
                _set_seam(element, True)
                count += 1
        bm.to_mesh(mesh)
        return count
    finally:
        bm.free()


def _view3d_override(context):
    """A ``temp_override`` bound to a 3D viewport, when there is one."""
    window = getattr(context, "window", None)
    screen = getattr(context, "screen", None) or getattr(window, "screen", None)
    if screen is None:
        return {}
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        overrides = {
            "window": window,
            "screen": screen,
            "area": area,
            "space_data": area.spaces.active,
        }
        if region is not None:
            overrides["region"] = region
        return overrides
    return {}


def unwrap_object(obj, settings: UVSettings, context=None) -> Dict:
    """Unwrap one object; return a report of what was done."""
    report = {
        "object": obj.name,
        "seams": 0,
        "method": settings.method,
        "packed": False,
        "ok": False,
    }
    if getattr(obj, "type", "") != "MESH":
        report["error"] = "not a mesh"
        return report

    seams = 0
    if settings.use_existing_seams:
        # Only the seams that are already there: the user's decision wins.
        seams = count_seams(obj)
    else:
        seams = mark_seams(obj, settings.angle, settings.respect_sharp)
    report["seams"] = seams
    report["own_seams"] = settings.use_existing_seams

    # The operator part needs Edit Mode and an active object.
    view_layer = bpy.context.view_layer
    was_active = view_layer.objects.active
    try:
        view_layer.objects.active = obj
        obj.select_set(True)
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")

        overrides = _view3d_override(context)
        with bpy.context.temp_override(**overrides):
            if seams == 0 and settings.smart_fallback and _is_closed(obj.data):
                # A smooth closed surface has no angle to cut at, so the
                # automatic unwrap cuts it for us.
                bpy.ops.uv.smart_project(
                    angle_limit=math.radians(settings.angle),
                    island_margin=settings.margin,
                )
                report["method"] = "smart_project"
            else:
                bpy.ops.uv.unwrap(
                    method=settings.method,
                    margin_method="SCALED",
                    margin=max(0.0001, settings.margin / 10.0),
                )
                if settings.minimize:
                    bpy.ops.uv.minimize_stretch()
                if settings.pack:
                    bpy.ops.uv.pack_islands(
                        rotate=True,
                        scale=True,
                        margin_method="SCALED",
                        margin=settings.margin,
                    )
                    report["packed"] = True
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a bad mesh must not kill the UI
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        try:
            view_layer.objects.active = was_active
        except Exception:
            pass
    return report


def unwrap_objects(objects: Sequence, settings: UVSettings, context=None) -> List[Dict]:
    """Unwrap several objects, one after another."""
    return [unwrap_object(obj, settings, context) for obj in objects]


def scope_objects(context=None) -> List:
    """The objects an unwrap should touch: the selection, or the whole scene."""
    if context is not None:
        selected = [
            obj
            for obj in getattr(context, "selected_objects", [])
            if obj.type == "MESH"
        ]
        if selected:
            return selected
    try:
        return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    except Exception:
        return []


# --- the density ------------------------------------------------------------
#
# The density is a *relative* property: the packer scales every island by the
# same factor to fit the texture, so it preserves the ratios between them but
# sets the absolute value. The order of the operations matters:
#
#   1. pack - the islands are laid out and fill the space;
#   2. measure - now the density of every island is known;
#   3. scale - every island is multiplied by target / its own density, so they
#      all end up at the target;
#   4. pack again, this time WITHOUT rescaling - only the islands are moved,
#      so the density stays where step 3 put it.
#
# Step 4 is why the second pack passes ``scale=False``: a normal pack would
# scale everything back to fill the texture and undo the whole correction.

# The densities of the islands must match this closely to count as equal.
DENSITY_TOLERANCE = 0.05


def _island_factors(report: UVReport, target: float) -> List[float]:
    """The factor every island has to be multiplied by to reach *target*."""
    factors = []
    for island in report.islands:
        if not island.density:
            factors.append(1.0)
        else:
            factors.append(target / island.density)
    return factors


def _scale_islands(obj, factors: Sequence[float]) -> None:
    """Multiply the UV coordinates of every island by its factor.

    Each island is scaled around its own centre, so it keeps its place.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return
        groups = _islands(bm, uv_layer, None)
        for group, factor in zip(groups, factors):
            if abs(factor - 1.0) < 1e-6:
                continue
            loops = [loop for face in group for loop in face.loops]
            if not loops:
                continue
            u_min = v_min = 1e30
            u_max = v_max = -1e30
            for loop in loops:
                uv = loop[uv_layer].uv
                u_min = min(u_min, uv.x)
                u_max = max(u_max, uv.x)
                v_min = min(v_min, uv.y)
                v_max = max(v_max, uv.y)
            centre_u = (u_min + u_max) * 0.5
            centre_v = (v_min + v_max) * 0.5
            for loop in loops:
                uv = loop[uv_layer].uv
                loop[uv_layer].uv = (
                    (uv.x - centre_u) * factor + centre_u,
                    (uv.y - centre_v) * factor + centre_v,
                )
        bm.to_mesh(mesh)
    finally:
        bm.free()


def _uv_bounds(report: UVReport) -> Tuple[float, float, float, float]:
    """The bounding box of all the islands: (u_min, v_min, u_max, v_max)."""
    if not report.islands:
        return (0.0, 0.0, 0.0, 0.0)
    u_min = min(island.uv_min[0] for island in report.islands)
    v_min = min(island.uv_min[1] for island in report.islands)
    u_max = max(island.uv_max[0] for island in report.islands)
    v_max = max(island.uv_max[1] for island in report.islands)
    return (u_min, v_min, u_max, v_max)


def match_density(obj, target: Optional[float] = None, context=None) -> Dict:
    """Bring every island of *obj* to the same texel density.

    Returns a report: the density before and after, and how big the texture
    has to be for the islands to fit.
    """
    settings = settings_from_scene()
    goal = float(target) if target else settings.density
    report = {
        "object": obj.name,
        "target": goal,
        "ok": False,
        "before": [],
        "after": [],
        "needed_size": 0,
    }
    if getattr(obj, "type", "") != "MESH":
        report["error"] = "not a mesh"
        return report
    if not getattr(obj.data, "uv_layers", None):
        report["error"] = "no UV layer"
        return report

    view_layer = bpy.context.view_layer
    was_active = view_layer.objects.active
    try:
        view_layer.objects.active = obj
        obj.select_set(True)
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")

        overrides = _view3d_override(context)
        with bpy.context.temp_override(**overrides):
            # 1. lay the islands out.
            bpy.ops.uv.pack_islands(
                rotate=True, scale=True, margin_method="SCALED", margin=0.001
            )
        first = analyze(obj)
        report["before"] = [
            round(i.density, 2) for i in first.islands if i.density
        ]

        # 2 and 3: measure, then scale every island to the target.
        factors = _island_factors(first, goal)
        bpy.ops.object.mode_set(mode="OBJECT")
        _scale_islands(obj, factors)

        # 4. move the islands without rescaling them.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        with bpy.context.temp_override(**overrides):
            bpy.ops.uv.pack_islands(
                rotate=False, scale=False, margin_method="SCALED", margin=0.001
            )

        final = analyze(obj)
        report["after"] = [round(i.density, 2) for i in final.islands if i.density]

        # How big does the texture have to be for the islands to fit?
        u_min, v_min, u_max, v_max = _uv_bounds(final)
        texture = final.texture_size[0] or 1024
        width = max(1.0, u_max - u_min)
        height = max(1.0, v_max - v_min)
        report["needed_size"] = int(math.ceil(max(width, height) * texture))
        report["fits"] = u_max <= 1.0 and v_min >= 0.0 and v_max <= 1.0 and u_min >= 0.0
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a bad mesh must not kill the UI
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        try:
            view_layer.objects.active = was_active
        except Exception:
            pass
    return report


def match_density_scene(
    objects: Sequence, target: Optional[float] = None, context=None
) -> List[Dict]:
    """Bring several objects to the same density."""
    return [match_density(obj, target, context) for obj in objects]


# --- the padding ------------------------------------------------------------
#
# Blender 5.x removed ``uv.offset`` - the operator that used to inflate the
# islands. So the padding is done here, on the bmesh.
#
# The idea: every boundary edge of an island is pushed outwards along its
# normal. A corner vertex is pushed along the bisector, far enough that the
# *perpendicular* distance to both edges is exactly the padding - otherwise the
# corners would come out too thin.

# Below this angle between two boundary normals the corner is treated as flat.
_FLAT_CORNER = 2.9  # radians, about 166 degrees


def _signed_area_uv(points) -> float:
    """The signed area of a polygon in the UV plane."""
    total = 0.0
    count = len(points)
    for i in range(count):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % count]
        total += x0 * y1 - x1 * y0
    return total * 0.5


def _pad_object(obj, padding: float) -> int:
    """Inflate the UV islands of *obj* by *padding* UV units.

    Returns the number of islands that were padded.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None or padding <= 0.0:
        return 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return 0
        groups = _islands(bm, uv_layer, None)
        padded = 0
        for group in groups:
            # The outward normals of the boundary edges, per vertex.
            normals: Dict = {}
            for face in group:
                loops = list(face.loops)
                count = len(loops)
                points = [loop[uv_layer].uv for loop in loops]
                area = _signed_area_uv(points)
                if abs(area) < 1e-12:
                    continue
                winding = 1.0 if area > 0 else -1.0
                for i, loop in enumerate(loops):
                    edge = loop.edge
                    if len(edge.link_faces) != 1:
                        continue
                    a = points[i]
                    b = points[(i + 1) % count]
                    dx = b[0] - a[0]
                    dy = b[1] - a[1]
                    length = math.hypot(dx, dy)
                    if length < 1e-12:
                        continue
                    # The normal that points away from the face.
                    nx = winding * dy / length
                    ny = -winding * dx / length
                    entry = normals.setdefault(loop.vert, [])
                    entry.append((nx, ny))

            if not normals:
                continue

            for vert, entries in normals.items():
                if len(entries) == 1:
                    nx, ny = entries[0]
                    offset_x, offset_y = nx * padding, ny * padding
                else:
                    (n1x, n1y), (n2x, n2y) = entries[0], entries[1]
                    sum_x = n1x + n2x
                    sum_y = n1y + n2y
                    length = math.hypot(sum_x, sum_y)
                    if length < 1e-9:
                        # The normals cancelled: a flat boundary.
                        offset_x, offset_y = n1x * padding, n1y * padding
                        continue
                    dot = max(-1.0, min(1.0, n1x * n2x + n1y * n2y))
                    alpha = math.acos(dot)
                    if alpha > _FLAT_CORNER:
                        offset_x, offset_y = n1x * padding, n1y * padding
                        continue
                    factor = 1.0 / max(0.05, math.cos(alpha * 0.5))
                    offset_x = sum_x / length * padding * factor
                    offset_y = sum_y / length * padding * factor
                # Every loop of the vertex has to move the same way.
                # NOTE: BMVert has no ``loops`` in Blender 5.x - the loops of a
                # vertex live in ``link_loops``.
                for loop in vert.link_loops:
                    value = loop[uv_layer].uv
                    loop[uv_layer].uv = (value.x + offset_x, value.y + offset_y)
            padded += 1
        bm.to_mesh(mesh)
        return padded
    finally:
        bm.free()


# --- the atlas -------------------------------------------------------------
#
# A real atlas puts the islands of *several objects* into one texture. The
# packer inside Blender works inside one object, so the placement is done here:
#
#   1. measure every island and bring it to the target density (the UV size of
#      the island then follows from the world size and the density);
#   2. place the resulting rectangles into the atlas with a simple shelf
#      packer;
#   3. write the new coordinates back into each object's UV layer;
#   4. pad the islands so the mipmaps do not bleed into each other.


def _pack_rectangles(rectangles) -> Tuple[List[Tuple[float, float]], float]:
    """Place rectangles into a unit-wide strip; return the positions and the height.

    A shelf packer: the rectangles are sorted by height and laid out in rows.
    It is not the tightest possible, but it is predictable and fast.
    """
    ordered = sorted(rectangles, key=lambda r: (-r[3], r[2]))
    positions = []
    shelf_x = 0.0
    shelf_y = 0.0
    shelf_h = 0.0
    for x, y, width, height in ordered:
        if shelf_x + width > 1.0 and shelf_x > 0.0:
            shelf_y += shelf_h
            shelf_x = 0.0
            shelf_h = 0.0
        positions.append((shelf_x, shelf_y))
        shelf_x += width
        shelf_h = max(shelf_h, height)
    return positions, shelf_y + shelf_h


def _transform_object_islands(obj, transforms) -> None:
    """Apply a per-island transform to the UV coordinates.

    Each transform is ``(scale, source_u, source_v, target_u, target_v)`` and
    maps ``(uv - source) * scale + target``.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return
        groups = _islands(bm, uv_layer, None)
        for group, transform in zip(groups, transforms):
            scale, src_u, src_v, dst_u, dst_v = transform
            for face in group:
                for loop in face.loops:
                    uv = loop[uv_layer].uv
                    loop[uv_layer].uv = (
                        (uv.x - src_u) * scale + dst_u,
                        (uv.y - src_v) * scale + dst_v,
                    )
        bm.to_mesh(mesh)
    finally:
        bm.free()


def build_atlas(objects: Sequence, settings: UVSettings, context=None) -> Dict:
    """Lay the islands of several objects into one shared atlas.

    Returns a report: how many islands, the density that was achieved and how
    big the texture has to be for the target density to hold.
    """
    atlas_pixels = int(settings.atlas)
    margin = max(settings.margin, 0.001)
    report = {
        "objects": len(objects),
        "islands": 0,
        "atlas": atlas_pixels,
        "target": settings.density,
        "ok": False,
        "needed_size": atlas_pixels,
        "achieved": 0.0,
    }

    # 1. Measure every island of every object.
    entries = []      # (obj, island index, u0, v0, width, height, density)
    per_object = []   # (obj, [rows])
    for obj in objects:
        result = analyze(obj)
        if not result.has_uv or not result.islands:
            continue
        rows = []
        for index, island in enumerate(result.islands):
            width, height = island.size_uv
            if width <= 0.0 or height <= 0.0:
                continue
            row = {
                "obj": obj,
                "index": index,
                "u0": island.uv_min[0],
                "v0": island.uv_min[1],
                "width": width,
                "height": height,
                "density": island.density or 0.0,
            }
            rows.append(row)
            entries.append(row)
        if rows:
            per_object.append((obj, rows))

    if not entries:
        report["error"] = "nothing to pack"
        return report

    # 2. The scale that brings every island to the target density, and the
    #    rectangle it occupies in the atlas.
    rectangles = []
    for row in entries:
        density = row["density"]
        scale = (settings.density / density) if density > 0 else 1.0
        row["scale"] = scale
        row["atlas_width"] = row["width"] * scale + margin * 2
        row["atlas_height"] = row["height"] * scale + margin * 2
        rectangles.append((0.0, 0.0, row["atlas_width"], row["atlas_height"]))

    # 3. Place them.
    positions, used_height = _pack_rectangles(rectangles)
    shrink = 1.0
    if used_height > 1.0:
        # The atlas is too small for the target density: everything is scaled
        # down to fit, and the report says how big it has to be.
        shrink = 1.0 / used_height

    for row, (x, y) in zip(entries, positions):
        row["place_x"] = x * shrink
        row["place_y"] = y * shrink

    total_area = sum(
        (row["atlas_width"] * shrink) * (row["atlas_height"] * shrink)
        for row in entries
    )
    report["islands"] = len(entries)
    report["used"] = round(total_area * 100, 1)
    report["needed_size"] = int(
        math.ceil(atlas_pixels * math.sqrt(max(total_area, 1e-9)))
    )

    # 4. Write the new coordinates back.
    for obj, rows in per_object:
        transforms = []
        for row in rows:
            scale = row["scale"] * shrink
            transforms.append(
                (
                    scale,
                    row["u0"],
                    row["v0"],
                    row["place_x"] + margin * shrink,
                    row["place_y"] + margin * shrink,
                )
            )
        _transform_object_islands(obj, transforms)

    # 5. Pad the islands, so the mipmaps do not bleed into each other.
    padding = margin * shrink
    if padding > 0:
        for obj, _rows in per_object:
            _pad_object(obj, padding)

    # 6. The density that was actually reached.
    densities = []
    for obj, _rows in per_object:
        result = analyze(obj)
        densities.extend(result.densities)
    if densities:
        report["achieved"] = sum(densities) / len(densities)
    report["ok"] = True
    return report


def create_atlas_image(name: str, size: int):
    """A white canvas of the given size, to paint the atlas on."""
    scratch = bpy.data.images.new(name + "_scratch", width=1, height=1)
    scratch.generated_color = (1.0, 1.0, 1.0, 1.0)
    scratch.scale(size, size)
    scratch.filepath_raw = ""
    scratch.name = name
    return scratch


# --- the findings ----------------------------------------------------------


def report_findings(
    report: UVReport,
    reference_density: Optional[float] = None,
    obj=None,
) -> List:
    """Turn the measurements into findings."""
    out = []

    if not report.has_uv:
        out.append(
            findings.make(
                NO_UV,
                findings.WARNING,
                {"obj": report.object},
                subject=report.object,
                key_prefix="uv",
            )
        )
        return out

    if report.outside:
        out.append(
            findings.make(
                OUT_OF_BOUNDS,
                findings.ERROR,
                {"obj": report.object, "n": report.outside},
                subject=report.object,
                key_prefix="uv",
            )
        )

    if report.overlap_pairs:
        pairs = ", ".join(
            f"{a + 1}-{b + 1}" for a, b in report.overlap_pairs[:6]
        )
        out.append(
            findings.make(
                OVERLAP,
                findings.ERROR,
                {"obj": report.object, "n": len(report.overlap_pairs), "pairs": pairs},
                subject=report.object,
                key_prefix="uv",
            )
        )

    if report.texture_size == (0, 0):
        out.append(
            findings.make(
                NO_TEXTURE,
                findings.INFO,
                {"obj": report.object},
                subject=report.object,
                key_prefix="uv",
            )
        )
    else:
        for index, island in enumerate(report.islands):
            if island.density is None:
                continue
            if island.density < MIN_SENSIBLE_DENSITY:
                out.append(
                    findings.make(
                        LOW_DENSITY,
                        findings.WARNING,
                        {
                            "obj": report.object,
                            "island": index + 1,
                            "density": round(island.density, 1),
                        },
                        subject=report.object,
                        key_prefix="uv",
                    )
                )
            elif island.density > MAX_SENSIBLE_DENSITY:
                out.append(
                    findings.make(
                        HIGH_DENSITY,
                        findings.INFO,
                        {
                            "obj": report.object,
                            "island": index + 1,
                            "density": round(island.density, 1),
                        },
                        subject=report.object,
                        key_prefix="uv",
                    )
                )

    if reference_density:
        for index, island in enumerate(report.islands):
            if island.density is None:
                continue
            ratio = island.density / reference_density
            if ratio < 0.5 or ratio > 2.0:
                out.append(
                    findings.make(
                        DENSITY_MISMATCH,
                        findings.WARNING,
                        {
                            "obj": report.object,
                            "island": index + 1,
                            "density": round(island.density, 1),
                            "reference": round(reference_density, 1),
                        },
                        subject=report.object,
                        key_prefix="uv",
                    )
                )

    if report.max_distortion > MAX_SENSIBLE_STRETCH:
        out.append(
            findings.make(
                STRETCH,
                findings.WARNING,
                {
                    "obj": report.object,
                    "distortion": round(report.max_distortion, 2),
                },
                subject=report.object,
                key_prefix="uv",
            )
        )

    # A closed model without seams unwraps into one piece - and the islands
    # that result are unusable for texturing.
    if obj is not None and not count_seams(obj):
        out.append(
            findings.make(
                NO_SEAMS,
                findings.WARNING,
                {"obj": report.object},
                subject=report.object,
                key_prefix="uv",
            )
        )

    # The islands of one object share one texture, so their densities have to
    # match. If they do not, the texture is sharp on one island and blurry on
    # the next - the classic "some parts are fuzzy" complaint.
    densities = report.densities
    if len(densities) > 1:
        low = min(densities)
        high = max(densities)
        if low > 0 and (high - low) / low > DENSITY_TOLERANCE:
            out.append(
                findings.make(
                    DENSITY_SPREAD,
                    findings.WARNING,
                    {
                        "obj": report.object,
                        "low": round(low, 1),
                        "high": round(high, 1),
                    },
                    subject=report.object,
                    key_prefix="uv",
                )
            )

    return out


def audit(objects: Optional[Sequence] = None) -> List:
    """Check the UVs of the given objects (default: the whole scene)."""
    # The world matrix of an object is only up to date after the dependency
    # graph caught up - otherwise a scale set a moment ago is not counted and
    # the density comes out wrong.
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

    if objects is None:
        try:
            objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        except Exception:
            objects = []

    reports = []
    for obj in objects:
        reports.append((obj, analyze(obj)))

    densities = scene_density()
    values = list(densities.values())
    reference = (sum(values) / len(values)) if values else None

    out = []
    for obj, report in reports:
        out.extend(report_findings(report, reference, obj))
    return out


def format_report(report: UVReport) -> str:
    """A human-readable summary of one object's unwrap."""
    lines = [f"{report.object}: {len(report.islands)} island(s)"]
    if report.texture:
        lines.append(
            f"  texture {report.texture} "
            f"{report.texture_size[0]}x{report.texture_size[1]}"
        )
    for index, island in enumerate(report.islands):
        bits = []
        if island.density is not None:
            bits.append(f"{island.density:.1f} px/m")
        bits.append(f"distortion {island.distortion:.2f}")
        size = island.size_world
        bits.append(
            f"size {size[0]:.2f}x{size[1]:.2f}x{size[2]:.2f} m"
        )
        lines.append(f"  island {index + 1}: " + ", ".join(bits))
    if report.outside:
        lines.append(f"  {report.outside} UV coordinates outside [0,1]")
    if report.overlap_pairs:
        lines.append(f"  {len(report.overlap_pairs)} overlapping island pair(s)")
    return "\n".join(lines)


# --- the tool --------------------------------------------------------------


@tools.tool(
    "uv.audit",
    "Audit UVs",
    "Check the unwrap of the objects: texel density in pixels per metre, "
    "overlapping islands, UVs outside [0,1] and stretched triangles. The "
    "density is the one problem that is invisible in the editor and very "
    "visible in a game.",
)
def audit_tool(**kwargs) -> str:
    """Tool entry point: audit the UVs."""
    found = audit()
    if not found:
        return "No UV problems found."
    counts = findings.summary(found)
    header = (
        f"UV check: {counts[findings.ERROR]} errors, "
        f"{counts[findings.WARNING]} warnings, {counts[findings.INFO]} notes."
    )
    body = "\n".join(f"- {f.text('en')}" for f in findings.sorted_findings(found))
    return f"{header}\n{body}"


@tools.tool(
    "uv.unwrap",
    "Unwrap",
    "Unwrap the selected objects: mark the seams automatically (by the angle "
    "and the sharp edges), unwrap them and pack the islands with a gap. The "
    "angle, the method and the gap can be given per call; otherwise the "
    "project settings are used. With ``own_seams`` the automatic seams are "
    "skipped and only the seams already marked by hand are used.",
    params=("angle", "method", "margin", "minimize", "objects", "own_seams"),
)
def unwrap_tool(
    angle=None,
    method=None,
    margin=None,
    minimize=None,
    objects=None,
    own_seams=None,
    **kwargs,
) -> str:
    """Tool entry point: unwrap."""
    settings = apply_overrides(
        settings_from_scene(),
        angle=angle,
        method=method,
        margin=margin,
        minimize=minimize,
        use_existing_seams=own_seams,
    )
    targets = []
    if isinstance(objects, str):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name == objects
        ]
    elif isinstance(objects, list):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name in objects
        ]
    if not targets:
        targets = scope_objects()

    reports = unwrap_objects(targets, settings)
    done = [r for r in reports if r.get("ok")]
    if not done:
        problems = "; ".join(
            f"{r['object']}: {r.get('error', 'failed')}" for r in reports
        )
        return f"Nothing was unwrapped: {problems}"

    lines = [
        f"Unwrapped {len(done)} object(s) with seams at {settings.angle:.0f}° "
        f"({settings.method}), gap {settings.margin:.3f}:"
    ]
    for report in done:
        if report.get("own_seams"):
            lines.append(
                f"- {report['object']}: {report['seams']} own seam(s), "
                f"method {report['method']}"
            )
        else:
            lines.append(
                f"- {report['object']}: {report['seams']} seam(s), "
                f"method {report['method']}"
            )
    return "\n".join(lines)


@tools.tool(
    "uv.symmetric",
    "Symmetric unwrap",
    "Make the unwrap symmetric: the seams are marked along the symmetry plane, "
    "the model is unwrapped, and the far half gets the UVs of its "
    "counterpart. One painting then serves both sides, so the effective "
    "resolution is doubled. Such a model needs its own texture - it cannot go "
    "into an atlas.",
    params=("object", "axis", "offset", "unwrap"),
)
def symmetric_tool(object=None, axis=None, offset=0.0, unwrap=True, **kwargs) -> str:
    """Tool entry point: make the unwrap symmetric."""
    if not axis:
        try:
            axis = bpy.context.scene.gaa_uv_symmetry_axis
        except Exception:
            axis = "X"
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object."

    report = make_symmetric(obj, str(axis), float(offset), bool(unwrap))
    if not report.get("ok"):
        return f"The symmetric unwrap failed: {report.get('error', 'unknown')}"
    return (
        f"Made the unwrap of '{report['object']}' symmetric along "
        f"{report['axis']}: {report['seams']} seam(s) on the symmetry plane, "
        f"{report['mirrored']} mirrored corner(s) "
        f"({report['mode']}). The texture now serves both halves - give this "
        "object its own texture instead of putting it in an atlas."
    )


@tools.tool(
    "uv.mark_seams",
    "Mark seams",
    "Mark the seams of the selected objects. By angle (every edge whose face "
    "angle is larger than the given one), by height (a horizontal cut - the "
    "mesh is really cut there, like Ctrl+R, which is what sleeves, boots and "
    "belts need) or by the sharp edges. Seams already marked by hand are never "
    "removed.",
    params=("angle", "height", "respect_sharp", "clear"),
)
def mark_seams_tool(
    angle=None, height=None, respect_sharp=None, clear=False, **kwargs
) -> str:
    """Tool entry point: mark the seams."""
    settings = apply_overrides(
        settings_from_scene(), angle=angle, respect_sharp=respect_sharp
    )
    targets = scope_objects()

    if clear:
        removed = sum(clear_seams(obj) for obj in targets)
        return f"Removed {removed} seam(s)."

    lines = []
    for obj in targets:
        if height is not None:
            count = mark_seams_by_height(obj, float(height))
            lines.append(f"- {obj.name}: {count} seam(s) at height {height}")
        else:
            count = mark_seams(obj, settings.angle, settings.respect_sharp)
            lines.append(f"- {obj.name}: {count} seam(s) at {settings.angle:.0f}°")
    if not lines:
        return "No meshes to work on."
    return "\n".join(lines)


@tools.tool(
    "uv.unwrap_own",
    "Unwrap without an atlas",
    "Unwrap the selected objects and leave every island where it is: no "
    "packing into a shared atlas, no padding. Use it when each object has its "
    "own texture, or for baking.",
    params=("angle", "objects"),
)
def unwrap_own_tool(angle=None, objects=None, **kwargs) -> str:
    """Tool entry point: unwrap without packing into an atlas."""
    settings = apply_overrides(settings_from_scene(), angle=angle)
    settings.margin = 0.0
    settings.pack = False
    targets = []
    if isinstance(objects, str):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name == objects
        ]
    if not targets:
        targets = scope_objects()

    reports = unwrap_objects(targets, settings)
    done = [r for r in reports if r.get("ok")]
    if not done:
        problems = "; ".join(
            f"{r['object']}: {r.get('error', 'failed')}" for r in reports
        )
        return f"Nothing was unwrapped: {problems}"
    lines = [f"Unwrapped {len(done)} object(s) without packing them"]
    for report in done:
        lines.append(
            f"- {report['object']}: {report['seams']} seam(s), "
            f"method {report['method']}"
        )
    return "\n".join(lines)


@tools.tool(
    "uv.match_density",
    "Match texel density",
    "Bring every island of the selected objects to the same texel density "
    "(pixels per metre), so the texture is equally sharp everywhere. The "
    "target density can be given per call.",
    params=("target", "objects"),
)
def match_density_tool(target=None, objects=None, **kwargs) -> str:
    """Tool entry point: match the texel density."""
    settings = settings_from_scene()
    goal = float(target) if target else settings.density
    targets = []
    if isinstance(objects, str):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name == objects
        ]
    elif isinstance(objects, list):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name in objects
        ]
    if not targets:
        targets = scope_objects()

    reports = match_density_scene(targets, goal)
    done = [r for r in reports if r.get("ok")]
    if not done:
        problems = "; ".join(
            f"{r['object']}: {r.get('error', 'failed')}" for r in reports
        )
        return f"Nothing was changed: {problems}"

    lines = [f"Texel density target: {goal:.0f} px/m"]
    for report in done:
        after = ", ".join(f"{d}" for d in report["after"]) or "-"
        line = f"- {report['object']}: {after} px/m"
        if not report.get("fits", True):
            line += (
                f" (the texture must be {report['needed_size']}px to fit them)"
            )
        lines.append(line)
    return "\n".join(lines)


@tools.tool(
    "uv.build_atlas",
    "Build an atlas",
    "Lay the islands of the selected objects into one shared atlas texture "
    "with a gap between them and a padding, so the atlas can be painted and "
    "used in a game.",
    params=("objects", "density", "atlas"),
)
def build_atlas_tool(objects=None, density=None, atlas=None, **kwargs) -> str:
    """Tool entry point: build a shared atlas."""
    settings = apply_overrides(
        settings_from_scene(), density=density, atlas=atlas
    )
    targets = []
    if isinstance(objects, str):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name == objects
        ]
    elif isinstance(objects, list):
        targets = [
            obj
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name in objects
        ]
    if not targets:
        targets = scope_objects()

    report = build_atlas(targets, settings)
    if not report.get("ok"):
        return f"Could not build the atlas: {report.get('error', 'unknown')}"

    lines = [
        f"Atlas: {report['islands']} island(s) from {report['objects']} object(s), "
        f"{report['used']}% of a {report['atlas']}px texture"
    ]
    if report["achieved"]:
        lines.append(
            f"Texel density: {report['achieved']:.1f} px/m "
            f"(target {report['target']:.0f})"
        )
    if report["needed_size"] > report["atlas"]:
        lines.append(
            f"The texture has to be {report['needed_size']}px to keep that density"
        )
    return "\n".join(lines)
