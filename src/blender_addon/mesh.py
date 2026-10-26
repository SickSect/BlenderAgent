"""The mesh itself: what is wrong with it, and how to make it a game asset.

A high-poly sculpt and a game mesh are different objects. The sculpt is
millions of triangles, has no UVs and is far too heavy for a device. The game
mesh is a quad mesh with a known budget.

This module does two things:

* **tells what is wrong** — triangles instead of quads, holes, non-manifold
  edges, doubled vertices, poles, needle triangles, a missing UV layout, a
  budget that is ten times over;
* **makes the game mesh** — remesh to quads within a budget, pull the result
  back onto the sculpt so the silhouette survives, clean it up, and build a
  LOD chain.

Why the built-in tools and not a rewritten Quad Remesher
---------------------------------------------------------
A quad remesher is a research project of its own; Quad Remesher is a
commercial GPU add-on. Neither is worth re-implementing here. What is worth
building is the **conductor**: pick the depth that hits the budget, shrinkwrap
the result onto the sculpt, clean it, report what changed, and keep the
original untouched. A pluggable backend (so a user who owns Quad Remesher can
plug it in) comes later.

The numbers that matter were measured on a 211k-triangle sculpt:

===========================  ==================  ==============
step                        result               time
===========================  ==================  ==============
Remesh Blocks, depth 4      176 quads            0.3 s
Remesh Blocks, depth 6      3 632 quads          0.4 s
Remesh Blocks, depth 7      15 576 quads         0.5 s
+ shrinkwrap onto the source  mean error 11.7 -> 1.4 mm
===========================  ==================  ==============
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import bmesh
import bpy

from . import findings, tools

# Finding codes. The messages live in i18n under "mesh.<code>".
NO_UV = "mesh_no_uv"
ALL_TRIANGLES = "mesh_all_triangles"
NON_MANIFOLD = "mesh_non_manifold"
HOLES = "mesh_holes"
DOUBLES = "mesh_doubles"
POLES = "mesh_poles"
STRETCHED = "mesh_stretched"
OVER_BUDGET = "mesh_over_budget"
NO_MATERIAL = "mesh_no_material"

# A triangle whose longest side is this many times the shortest one is a
# "needle": it is almost always a mistake, and it shades badly.
_NEEDLE_RATIO = 10.0

# The default budget: a mobile character. The user sets their own.
DEFAULT_BUDGET = 20000


@dataclass
class MeshReport:
    """What a mesh is, in numbers."""

    object: str = ""
    verts: int = 0
    faces: int = 0
    quads: int = 0
    tris: int = 0
    ngons: int = 0
    ngon_tris: int = 0
    non_manifold: int = 0
    boundary: int = 0
    wire: int = 0
    loose_verts: int = 0
    doubles: int = 0
    poles: int = 0
    stretched: int = 0
    area: float = 0.0
    uv_layers: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    has_armature: bool = False

    @property
    def tri_equivalent(self) -> int:
        """The face count the renderer sees: a quad is two triangles."""
        return self.tris + self.quads * 2 + self.ngon_tris

    @property
    def quad_share(self) -> float:
        if not self.faces:
            return 0.0
        return self.quads / self.faces


def _polygon_counts(mesh) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for polygon in mesh.polygons:
        size = len(polygon.vertices)
        counts[size] = counts.get(size, 0) + 1
    return counts


def analyze(obj) -> MeshReport:
    """Measure a mesh. Changes nothing."""
    report = MeshReport(object=getattr(obj, "name", ""))
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "polygons"):
        return report

    report.verts = len(mesh.vertices)
    report.faces = len(mesh.polygons)
    counts = _polygon_counts(mesh)
    report.tris = counts.get(3, 0)
    report.quads = counts.get(4, 0)
    report.ngons = report.faces - report.tris - report.quads
    report.ngon_tris = sum(
        (size - 2) * count for size, count in counts.items() if size > 4
    )
    report.uv_layers = [layer.name for layer in getattr(mesh, "uv_layers", [])]
    report.materials = [
        slot.material.name if slot.material else ""
        for slot in getattr(obj, "material_slots", [])
    ]

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        for edge in bm.edges:
            faces = len(edge.link_faces)
            if faces > 2:
                report.non_manifold += 1
            elif faces == 1:
                report.boundary += 1
            elif faces == 0:
                report.wire += 1
        report.loose_verts = sum(1 for v in bm.verts if not v.link_edges)
        report.poles = sum(1 for v in bm.verts if len(v.link_faces) > 4)
        report.area = sum(face.calc_area() for face in bm.faces)

        # Doubled vertices: two vertices in the same place.
        seen = {}
        for vert in bm.verts:
            key = (round(vert.co.x, 5), round(vert.co.y, 5), round(vert.co.z, 5))
            seen[key] = seen.get(key, 0) + 1
        report.doubles = sum(count - 1 for count in seen.values() if count > 1)

        # Needle triangles.
        for face in bm.faces:
            if len(face.verts) != 3:
                continue
            lengths = [edge.calc_length() for edge in face.edges]
            shortest = min(lengths)
            if shortest > 1e-9 and max(lengths) / shortest > _NEEDLE_RATIO:
                report.stretched += 1
    finally:
        bm.free()

    return report


def check(obj, budget: int = DEFAULT_BUDGET) -> List:
    """The problems of one mesh, as findings."""
    out = []
    if getattr(obj, "type", "") != "MESH":
        return out
    report = analyze(obj)
    name = report.object

    if report.non_manifold:
        out.append(
            findings.make(
                NON_MANIFOLD,
                findings.ERROR,
                {"obj": name, "n": report.non_manifold},
                subject=name,
                key_prefix="mesh",
            )
        )
    if report.boundary:
        out.append(
            findings.make(
                HOLES,
                findings.ERROR,
                {"obj": name, "n": report.boundary},
                subject=name,
                key_prefix="mesh",
            )
        )
    if report.wire or report.loose_verts:
        out.append(
            findings.make(
                DOUBLES,
                findings.WARNING,
                {
                    "obj": name,
                    "wire": report.wire,
                    "loose": report.loose_verts,
                    "doubles": report.doubles,
                },
                subject=name,
                key_prefix="mesh",
            )
        )
    if report.faces and report.quad_share < 0.5:
        out.append(
            findings.make(
                ALL_TRIANGLES,
                findings.WARNING,
                {"obj": name, "tris": report.tris, "faces": report.faces},
                subject=name,
                key_prefix="mesh",
            )
        )
    if report.poles:
        out.append(
            findings.make(
                POLES,
                findings.INFO,
                {"obj": name, "n": report.poles},
                subject=name,
                key_prefix="mesh",
            )
        )
    if report.stretched:
        out.append(
            findings.make(
                STRETCHED,
                findings.INFO,
                {"obj": name, "n": report.stretched},
                subject=name,
                key_prefix="mesh",
            )
        )
    if not report.uv_layers:
        out.append(
            findings.make(
                NO_UV,
                findings.WARNING,
                {"obj": name},
                subject=name,
                key_prefix="mesh",
            )
        )
    if not any(report.materials):
        out.append(
            findings.make(
                NO_MATERIAL,
                findings.INFO,
                {"obj": name},
                subject=name,
                key_prefix="mesh",
            )
        )
    if budget and report.tri_equivalent > budget:
        out.append(
            findings.make(
                OVER_BUDGET,
                findings.ERROR,
                {"obj": name, "n": report.tri_equivalent, "budget": budget},
                subject=name,
                key_prefix="mesh",
            )
        )
    return findings.sorted_findings(out)


def format_report(report: MeshReport, budget: int = 0) -> str:
    """A human-readable summary of one mesh."""
    lines = [
        f"{report.object}: {report.verts} verts, {report.faces} faces "
        f"({report.quads} quads, {report.tris} tris, {report.ngons} n-gons)"
    ]
    lines.append(f"  triangles the renderer sees: {report.tri_equivalent}")
    if budget:
        verdict = "over" if report.tri_equivalent > budget else "within"
        lines.append(f"  budget {budget}: {verdict}")
    if report.non_manifold or report.boundary or report.wire:
        lines.append(
            f"  broken edges: {report.non_manifold} non-manifold, "
            f"{report.boundary} boundary, {report.wire} wire"
        )
    if report.doubles or report.loose_verts:
        lines.append(
            f"  doubled vertices: {report.doubles}, loose: {report.loose_verts}"
        )
    if report.poles:
        lines.append(f"  poles (5+ faces around a vertex): {report.poles}")
    if report.stretched:
        lines.append(f"  needle triangles: {report.stretched}")
    lines.append(
        f"  UV layers: {', '.join(report.uv_layers) if report.uv_layers else 'none'}"
    )
    lines.append(
        f"  materials: {', '.join(m for m in report.materials if m) or 'none'}"
    )
    return "\n".join(lines)


# --- the retopology ---------------------------------------------------------


def _activate(obj) -> None:
    """Make *obj* the only selected, active object."""
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:  # noqa: BLE001 - no view layer in some contexts
        pass
    obj.select_set(True)
    view_layer = bpy.context.view_layer
    if view_layer is not None:
        view_layer.objects.active = obj


def _max_octree_depth() -> int:
    probe = bpy.data.objects.new("gaa_probe", bpy.data.meshes.new("gaa_probe"))
    try:
        mod = probe.modifiers.new("probe", "REMESH")
        return int(mod.bl_rna.properties["octree_depth"].hard_max)
    except Exception:  # noqa: BLE001
        return 8
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)
        bpy.data.meshes.remove(bpy.data.meshes.get("gaa_probe"))


def _apply_remesh(work, source_mesh, depth: int) -> int:
    """Remesh *work* from *source_mesh* at *depth*; return the face count."""
    work.data = source_mesh.copy()
    for mod in list(work.modifiers):
        work.modifiers.remove(mod)
    mod = work.modifiers.new("gaa_remesh", "REMESH")
    mod.mode = "BLOCKS"
    mod.octree_depth = int(depth)
    mod.adaptivity = 0.0
    mod.use_remove_disconnected = False
    _activate(work)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return len(work.data.polygons)


def _apply_shrinkwrap(work, target) -> None:
    """Pull the remeshed mesh onto the sculpt.

    Two steps, and the second one is not optional. PROJECT moves every vertex
    along its own normal, but a ray that points away from the surface never
    hits it - measured on a sphere, up to 444 mm of the mesh stayed in the
    air. NEAREST_SURFACEPOINT pulls every vertex to the closest point of the
    sculpt, and after it the whole mesh sits on the source exactly.
    """
    project = work.modifiers.new("gaa_shrinkwrap", "SHRINKWRAP")
    project.target = target
    project.wrap_method = "PROJECT"
    project.use_project_x = True
    project.use_project_y = True
    project.use_project_z = True
    project.use_positive_direction = True
    project.use_negative_direction = True
    project.offset = 0.0
    _activate(work)
    bpy.ops.object.modifier_apply(modifier=project.name)

    snap = work.modifiers.new("gaa_snap", "SHRINKWRAP")
    snap.target = target
    snap.wrap_method = "NEAREST_SURFACEPOINT"
    _activate(work)
    bpy.ops.object.modifier_apply(modifier=snap.name)


def _triangle_count(mesh) -> int:
    """How many triangles the renderer will see for this mesh."""
    total = 0
    for polygon in mesh.polygons:
        size = len(polygon.vertices)
        total += 1 if size == 3 else max(0, size - 2)
    return total


def _cleanup(mesh, angle: float = 40.0) -> None:
    """Fix the normals and mark the sharp edges.

    The vertices are **not** welded, and that is deliberate. The octree remesh
    leaves thousands of exactly coincident vertices, but merging them folds
    the surface where it touches itself: measured on a real asset, welding
    turned 165 non-manifold edges into 4 154. The duplicates stay, and the
    doctor reports them.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if len(bm.faces):
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        limit = math.radians(angle)
        for face in bm.faces:
            face.smooth = True
        for edge in bm.edges:
            if len(edge.link_faces) == 2:
                edge.smooth = edge.calc_face_angle() <= limit
            else:
                edge.smooth = False
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()


def deviation(obj, target, limit: int = 20000) -> Dict:
    """How far the vertices of *obj* are from the surface of *target*."""
    total = 0.0
    worst = 0.0
    count = 0
    vertices = obj.data.vertices
    step = max(1, len(vertices) // max(1, limit))
    for index, vert in enumerate(vertices):
        if index % step:
            continue
        hit, location, _normal, _face = target.closest_point_on_mesh(vert.co)
        if not hit:
            continue
        distance = (vert.co - location).length
        total += distance
        worst = max(worst, distance)
        count += 1
    if not count:
        return {"mean": 0.0, "max": 0.0, "samples": 0}
    return {"mean": total / count, "max": worst, "samples": count}


def retopo(
    obj,
    budget: int = DEFAULT_BUDGET,
    shrinkwrap: bool = True,
    name: Optional[str] = None,
) -> Dict:
    """Make a game mesh from a high-poly one and return a report.

    The original is never touched: the result is a **new object** next to it,
    so the sculpt stays available as the shrinkwrap target and as a fallback.
    """
    report: Dict = {
        "ok": False,
        "source": getattr(obj, "name", ""),
        "object": "",
        "budget": int(budget),
        "depth": 0,
        "faces": 0,
        "quads": 0,
        "seconds": 0.0,
        "deviation": {},
        "notes": [],
    }
    if getattr(obj, "type", "") != "MESH":
        report["error"] = "the object is not a mesh"
        return report
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    source = analyze(obj)
    report["source_faces"] = source.faces
    report["source_quads"] = source.quads

    started = time.time()
    source_mesh = obj.data.copy()
    work = obj.copy()
    work.data = source_mesh.copy()
    work.name = name or f"{obj.name}_Retopo"
    for mod in list(work.modifiers):
        work.modifiers.remove(mod)
    bpy.context.scene.collection.objects.link(work)
    # The new mesh keeps the material slots of the source.
    work.data.materials.clear()
    for material in obj.data.materials:
        work.data.materials.append(material)

    try:
        # Find the depth whose triangle count is closest to the budget without
        # going over it. Each attempt is a remesh, so the count is measured,
        # not guessed from a table. The budget is in triangles, because that
        # is what the renderer counts: a quad is two of them.
        limit = _max_octree_depth()
        counts = {}
        for depth in range(1, limit + 1):
            _apply_remesh(work, source_mesh, depth)
            counts[depth] = _triangle_count(work.data)
            if counts[depth] >= budget:
                break
        under = [d for d in counts if counts[d] <= budget]
        if under:
            chosen = max(under, key=lambda d: counts[d])
        else:
            chosen = min(counts, key=lambda d: counts[d])
            report["notes"].append(
                f"even the simplest remesh has {counts[chosen]} triangles, "
                f"over the budget of {budget}"
            )
        if chosen == limit and counts[chosen] < budget:
            report["notes"].append(
                f"the finest remesh has {counts[chosen]} triangles, "
                f"under the budget of {budget}"
            )
        elif counts[chosen] < budget:
            over = sorted(d for d in counts if counts[d] > budget)
            if over:
                report["notes"].append(
                    f"the remesh only doubles the resolution: the next step "
                    f"would be {counts[over[0]]} triangles, over the budget of "
                    f"{budget}"
                )

        _apply_remesh(work, source_mesh, chosen)
        report["depth"] = chosen
        report["faces"] = len(work.data.polygons)
        report["tris"] = _triangle_count(work.data)

        if shrinkwrap:
            _apply_shrinkwrap(work, obj)
        else:
            report["notes"].append("the surface was not pulled back onto the sculpt")

        _cleanup(work.data)
        # The distance is measured on the finished mesh, not on the raw
        # remesh: the report must describe what the user gets.
        if shrinkwrap:
            report["deviation"] = deviation(work, obj)
        result = analyze(work)
        report["object"] = work.name
        report["quads"] = result.quads
        report["tri_faces"] = result.tris
        # The number the budget is about: what the renderer will see.
        report["tris"] = result.tri_equivalent
        report["verts"] = result.verts
        report["non_manifold"] = result.non_manifold
        report["doubles"] = result.doubles
        report["seconds"] = time.time() - started
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a failed retopo must not kill Blender
        report["error"] = f"{type(exc).__name__}: {exc}"
        try:
            bpy.data.objects.remove(work, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    return report


def lod_chain(obj, levels: int = 3) -> Dict:
    """Build a LOD chain next to *obj*: LOD1, LOD2, ... with fewer faces."""
    report: Dict = {
        "ok": False,
        "source": getattr(obj, "name", ""),
        "levels": [],
    }
    if getattr(obj, "type", "") != "MESH":
        report["error"] = "the object is not a mesh"
        return report
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    source = analyze(obj)
    quad_mesh = source.quad_share >= 0.9
    try:
        for index in range(1, max(1, int(levels)) + 1):
            copy = obj.copy()
            copy.data = obj.data.copy()
            copy.name = f"{obj.name}_LOD{index}"
            for mod in list(copy.modifiers):
                copy.modifiers.remove(mod)
            bpy.context.scene.collection.objects.link(copy)
            if quad_mesh:
                # Halving a quad mesh keeps it a quad mesh.
                for _ in range(index):
                    mod = copy.modifiers.new("gaa_decimate", "DECIMATE")
                    mod.decimate_type = "UNSUBDIV"
                    _activate(copy)
                    bpy.ops.object.modifier_apply(modifier=mod.name)
            else:
                mod = copy.modifiers.new("gaa_decimate", "DECIMATE")
                mod.decimate_type = "COLLAPSE"
                mod.ratio = 0.5 ** index
                _activate(copy)
                bpy.ops.object.modifier_apply(modifier=mod.name)
            result = analyze(copy)
            report["levels"].append(
                {
                    "name": copy.name,
                    "faces": result.faces,
                    "tris": result.tri_equivalent,
                }
            )
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


# --- the settings ------------------------------------------------------------


def settings_from_scene(scene=None) -> Dict:
    """The retopology settings of the current scene."""
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    return {
        "budget": int(getattr(scene, "gaa_retopo_budget", DEFAULT_BUDGET)),
        "shrinkwrap": bool(getattr(scene, "gaa_retopo_shrinkwrap", True)),
        "levels": int(getattr(scene, "gaa_lod_levels", 3)),
    }


def register() -> None:
    """Register the per-scene retopology settings."""
    from bpy.props import BoolProperty, IntProperty

    bpy.types.Scene.gaa_retopo_budget = IntProperty(
        name="Бюджет полигонов / Polygon budget",
        description=(
            "Сколько треугольников должен весить игровой меш / How many "
            "triangles the game mesh should weigh"
        ),
        default=DEFAULT_BUDGET,
        min=100,
        max=2000000,
    )
    bpy.types.Scene.gaa_retopo_shrinkwrap = BoolProperty(
        name="Прижать к скульпту / Shrinkwrap to the sculpt",
        description=(
            "Притянуть новую сетку к исходной, чтобы сохранить силуэт / Pull "
            "the new mesh onto the original to keep the silhouette"
        ),
        default=True,
    )
    bpy.types.Scene.gaa_lod_levels = IntProperty(
        name="Уровней LOD / LOD levels",
        description=(
            "Сколько уровней детализации построить рядом / How many levels of "
            "detail to build next to the mesh"
        ),
        default=3,
        min=1,
        max=8,
    )


def unregister() -> None:
    for name in ("gaa_lod_levels", "gaa_retopo_shrinkwrap", "gaa_retopo_budget"):
        try:
            delattr(bpy.types.Scene, name)
        except Exception:  # noqa: BLE001
            continue


# --- the tools ---------------------------------------------------------------


@tools.tool(
    "mesh.check",
    "Check the mesh",
    "Check the mesh of the active object: triangles instead of quads, holes, "
    "non-manifold edges, doubled vertices, poles, needle triangles, a missing "
    "UV layout and how far the face count is from the budget.",
    params=("object", "budget"),
)
def check_tool(object=None, budget=None, **kwargs) -> str:
    """Tool entry point: check the mesh."""
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object to check."
    if obj.type != "MESH":
        return f"'{obj.name}' is not a mesh."

    limit = int(budget) if budget else settings_from_scene()["budget"]
    report = analyze(obj)
    lines = [format_report(report, limit)]
    found = check(obj, limit)
    if found:
        counts = findings.summary(found)
        lines.append(
            f"problems: {counts[findings.ERROR]} errors, "
            f"{counts[findings.WARNING]} warnings, {counts[findings.INFO]} notes"
        )
        for finding in found:
            lines.append(f"- {finding.text('en')}")
    else:
        lines.append("No problems found.")
    return "\n".join(lines)


@tools.tool(
    "mesh.retopo",
    "Retopologize",
    "Make a game mesh from the high-poly one: remesh to quads within the "
    "polygon budget, pull the result back onto the sculpt to keep the "
    "silhouette, and clean it up. The original is left untouched; the result "
    "is a new object next to it.",
    params=("object", "budget", "shrinkwrap"),
)
def retopo_tool(object=None, budget=None, shrinkwrap=None, **kwargs) -> str:
    """Tool entry point: make a game mesh."""
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object to retopologize."
    if obj.type != "MESH":
        return f"'{obj.name}' is not a mesh."

    settings = settings_from_scene()
    limit = int(budget) if budget else settings["budget"]
    pull = settings["shrinkwrap"] if shrinkwrap is None else bool(shrinkwrap)
    report = retopo(obj, limit, pull)
    if not report.get("ok"):
        return f"The retopology did not finish: {report.get('error', 'unknown')}"

    lines = [
        f"Made '{report['object']}' from '{report['source']}' in "
        f"{report['seconds']:.1f}s: {report['quads']} quads "
        f"({report['tris']} triangles, remesh depth {report['depth']}, "
        f"budget {limit})."
    ]
    error = report.get("deviation") or {}
    if error.get("samples"):
        lines.append(
            f"It sits {error['mean'] * 1000:.1f} mm from the sculpt on average "
            f"(worst {error['max'] * 1000:.1f} mm)."
        )
    if report.get("non_manifold"):
        lines.append(
            f"It has {report['non_manifold']} non-manifold edges - the remesh "
            f"leaves them, check the mesh before exporting."
        )
    for note in report.get("notes", []):
        lines.append(f"- {note}")
    lines.append("The original is untouched; unwrap the new mesh next.")
    return "\n".join(lines)


@tools.tool(
    "mesh.lod",
    "Build a LOD chain",
    "Build levels of detail next to the mesh: LOD1, LOD2 and so on, each with "
    "fewer faces than the previous one.",
    params=("object", "levels"),
)
def lod_tool(object=None, levels=None, **kwargs) -> str:
    """Tool entry point: build the LOD chain."""
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object."
    if obj.type != "MESH":
        return f"'{obj.name}' is not a mesh."

    count = int(levels) if levels else settings_from_scene()["levels"]
    report = lod_chain(obj, count)
    if not report.get("ok"):
        return f"The LOD chain did not finish: {report.get('error', 'unknown')}"
    lines = [f"LOD chain for '{report['source']}':"]
    for level in report["levels"]:
        lines.append(
            f"- {level['name']}: {level['faces']} faces "
            f"({level['tris']} triangles)"
        )
    return "\n".join(lines)
