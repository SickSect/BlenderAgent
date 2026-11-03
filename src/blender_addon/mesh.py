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
from mathutils import Vector

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
    zero_faces: int = 0
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
            if face.calc_area() < 1e-12:
                report.zero_faces += 1
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


def _apply_voxel(work, source_mesh, voxel: float) -> int:
    """Remesh *work* with the voxel mode; return the triangle count."""
    work.data = source_mesh.copy()
    for mod in list(work.modifiers):
        work.modifiers.remove(mod)
    mod = work.modifiers.new("gaa_voxel", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = max(1e-6, float(voxel))
    _activate(work)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return _triangle_count(work.data)


def _pick_voxel(work, source_mesh, budget: int, diagonal: float):
    """The finest voxel size whose triangle count is within the budget.

    The octree depth only doubles the resolution, so the budget lands on a
    step. The voxel size is continuous: the search walks it and stops at the
    finest mesh that still fits, which is how the budget becomes a real knob.

    The face count grows roughly as 1/voxel^2, so each measurement predicts
    the next voxel instead of halving the interval blindly - three or four
    remeshes instead of nine.
    """
    voxel = diagonal / 200.0
    best = None
    last = 0
    for _ in range(5):
        count = _apply_voxel(work, source_mesh, voxel)
        last = count
        if count <= budget:
            best = (voxel, count)
            if count >= budget * 0.95:
                break
            # Finer, but stay under the budget.
            step = math.sqrt(count / budget) * 0.97
        else:
            step = math.sqrt(count / budget) * 1.03
        next_voxel = voxel * step
        if abs(next_voxel - voxel) < voxel * 0.02:
            break
        voxel = next_voxel
    if best is None:
        # Even the first try was over: the coarsest the loop reached is the
        # answer, and the caller reports the gap.
        return voxel, last
    return best


def _apply_shrinkwrap(work, target) -> None:
    """Pull the remeshed mesh onto the sculpt.

    One step, and it has to be NEAREST_SURFACEPOINT. PROJECT moves every
    vertex along its own normal, and a ray that points through the model
    comes out on the other side: measured on a head, that put vertices
    362 mm away and grew the surface from 1.74 to 7.24 m2. NEAREST pulls
    every vertex to the closest point of the sculpt instead - the whole mesh
    then sits on the source (0.02 mm on the same head, 0.00 mm on a sphere).
    """
    snap = work.modifiers.new("gaa_snap", "SHRINKWRAP")
    snap.target = target
    snap.wrap_method = "NEAREST_SURFACEPOINT"
    _activate(work)
    bpy.ops.object.modifier_apply(modifier=snap.name)


def _bbox_diagonal(bm) -> float:
    """The length of the bounding box diagonal of a bmesh."""
    if not len(bm.verts):
        return 1.0
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    return Vector(
        (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    ).length or 1.0


def _repair_mesh(mesh, fill_holes: bool = True) -> Dict:
    """Make a working copy of the source usable for a remesh.

    A scanned model is usually a shell with a hole (the neck cut) and
    thousands of coincident vertices. Remeshing that as it is gives garbage:
    the octree closes the hole with its own geometry, and the shrinkwrap then
    pulls both sides of a wall onto the same surface - the polygons come out
    smeared. So the working copy is repaired first: the zero-area faces are
    dropped, the duplicates are welded, the holes are filled and the normals
    are made consistent.

    The original mesh is never touched - this works on a copy.
    """
    out = {
        "holes": 0,
        "holes_filled": 0,
        "doubles": 0,
        "zero_faces": 0,
        "remaining_holes": 0,
        "error": "",
    }
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if not len(bm.faces):
            return out

        # 1. the faces that have no area
        degenerate = [f for f in bm.faces if f.calc_area() < 1e-12]
        out["zero_faces"] = len(degenerate)
        if degenerate:
            bmesh.ops.delete(bm, geom=degenerate, context="FACES")

        # 2. the holes, counted **before** the weld: welding can close a
        #    hole on its own, and the count must be the real one
        boundary = [e for e in bm.edges if len(e.link_faces) == 1]
        out["holes"] = len(boundary)

        # 3. the vertices in the same place
        before = len(bm.verts)
        bmesh.ops.remove_doubles(
            bm, verts=bm.verts[:], dist=_bbox_diagonal(bm) * 1e-5
        )
        out["doubles"] = before - len(bm.verts)

        # 4. the holes, again - some of them the weld has closed
        if fill_holes:
            boundary = [e for e in bm.edges if len(e.link_faces) == 1]
            if boundary:
                filled = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
                out["holes_filled"] = sum(
                    1
                    for g in filled.get("geom", [])
                    if isinstance(g, bmesh.types.BMFace)
                )
        out["remaining_holes"] = sum(
            1 for e in bm.edges if len(e.link_faces) == 1
        )

        # 4. the normals
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(mesh)
    except Exception as exc:  # noqa: BLE001 - a broken source must not kill the bake
        # Swallowing this silently hid a real bug once: the working copy was
        # not repaired and the retopology reported "nothing to repair".
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        bm.free()
    mesh.update()
    return out


def _parts(mesh) -> List[Dict]:
    """The connected parts of a mesh, the biggest first.

    A healthy model is one surface - one part. A scan or an AI-generated
    model that fell apart has dozens, and remeshing glues them together:
    that is where the smeared polygons come from. The report tells the user
    before the retopology, not after.
    """
    parts: List[Dict] = []
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        visited = set()
        for face in bm.faces:
            if face.index in visited:
                continue
            stack = [face]
            visited.add(face.index)
            faces = 0
            area = 0.0
            while stack:
                current = stack.pop()
                faces += 1
                area += current.calc_area()
                for edge in current.edges:
                    for other in edge.link_faces:
                        if other.index not in visited:
                            visited.add(other.index)
                            stack.append(other)
            parts.append({"faces": faces, "area": area})
    except Exception:  # noqa: BLE001 - an odd mesh must not kill the tool
        return []
    finally:
        bm.free()
    parts.sort(key=lambda part: part["faces"], reverse=True)
    return parts


def repair(obj, weld: float = 0.0001, fill_holes: bool = False) -> Dict:
    """Fix the mesh of an object: weld the duplicates, drop the degenerates,
    make the normals consistent.

    This is the step that has to come **before** a retopology. A model that
    fell apart into dozens of pieces and thousands of coincident vertices
    remeshes into a smeared shell; after the repair the same retopology
    gives a clean quad mesh.

    The holes are reported but not filled by default: filling them changes
    the surface (measured: the area grew from 1.01 to 1.64 m2 on a head),
    and the user decides.
    """
    report: Dict = {
        "object": getattr(obj, "name", ""),
        "ok": False,
        "welded": 0,
        "zero_faces": 0,
        "loose_verts": 0,
        "holes": 0,
        "holes_filled": 0,
        "notes": [],
    }
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "polygons"):
        report["error"] = "the object has no mesh"
        return report
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    before = analyze(obj)
    before_parts = _parts(mesh)
    report["before"] = {
        "verts": before.verts,
        "faces": before.faces,
        "doubles": before.doubles,
        "holes": before.boundary,
        "parts": len(before_parts),
    }

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        # 1. the faces that have no area
        degenerate = [f for f in bm.faces if f.calc_area() < 1e-12]
        report["zero_faces"] = len(degenerate)
        if degenerate:
            bmesh.ops.delete(bm, geom=degenerate, context="FACES")

        # 2. the vertices in the same place. This comes **before** the loose
        #    vertices: a doubled vertex has no faces of its own, and the
        #    loose sweep would throw it away before the weld sees it.
        verts_before = len(bm.verts)
        if weld > 0:
            bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=float(weld))
        report["welded"] = verts_before - len(bm.verts)

        # 3. the vertices that belong to nothing
        loose = [v for v in bm.verts if not v.link_edges]
        report["loose_verts"] = len(loose)
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")

        # 4. the holes, only when the user asked for it
        boundary = [e for e in bm.edges if len(e.link_faces) == 1]
        report["holes"] = len(boundary)
        if fill_holes and boundary:
            filled = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
            report["holes_filled"] = sum(
                1
                for g in filled.get("geom", [])
                if isinstance(g, bmesh.types.BMFace)
            )
        else:
            report["notes"].append(
                f"{report['holes']} hole edge(s) left open - closing them "
                f"changes the surface, so it is your call"
            )

        # 5. the normals
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(mesh)
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a bad mesh must not kill Blender
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        bm.free()
    mesh.update()

    after = analyze(obj)
    after_parts = _parts(mesh)
    report["after"] = {
        "verts": after.verts,
        "faces": after.faces,
        "doubles": after.doubles,
        "holes": after.boundary,
        "parts": len(after_parts),
    }
    report["area_before"] = before.area
    report["area_after"] = after.area
    return report


# --- the non-manifold repair -----------------------------------------------


def _bm_stats(bm) -> Dict:
    """Measure a bmesh without changing it."""
    non_manifold = boundary = wire = 0
    area = 0.0
    zero_faces = 0
    for edge in bm.edges:
        faces = len(edge.link_faces)
        if faces > 2:
            non_manifold += 1
        elif faces == 1:
            boundary += 1
        elif faces == 0:
            wire += 1
    for face in bm.faces:
        face_area = face.calc_area()
        area += face_area
        if face_area < 1e-12:
            zero_faces += 1
    return {
        "verts": len(bm.verts),
        "faces": len(bm.faces),
        "edges": len(bm.edges),
        "non_manifold": non_manifold,
        "boundary": boundary,
        "wire": wire,
        "area": area,
        "zero_faces": zero_faces,
    }


def _non_manifold_edges(bm) -> List:
    return [edge for edge in bm.edges if len(edge.link_faces) > 2]


def _drop_loose(bm) -> None:
    """Remove edges and vertices that no face uses after a delete."""
    edges = [edge for edge in bm.edges if not edge.link_faces]
    if edges:
        bmesh.ops.delete(bm, geom=edges, context="EDGES")
    verts = [vert for vert in bm.verts if not vert.link_edges]
    if verts:
        bmesh.ops.delete(bm, geom=verts, context="VERTS")


def repair_non_manifold(mesh, max_area_change: float = 0.05) -> Dict:
    """Fix the edges that more than two faces share, without a global weld.

    The remesh leaves branching edges behind. A global ``remove_doubles`` is
    not the answer: measured on a real asset, it turned 165 non-manifold edges
    into 4 154, because the surface touches itself in many places and merging
    every coincident vertex folds it. This function is deliberately local and
    measured:

    1. drop the zero-area faces and the exactly duplicate faces;
    2. delete the faces that sit entirely on branching edges - those are the
       internal walls left between two glued surfaces;
    3. weld only the vertices that belong to a non-manifold edge, with a tiny
       tolerance;
    4. split whatever is still branching. This turns a three-face edge into
       open boundary edges instead of a branch;
    5. after every step, measure non-manifold, boundary, faces and area. If the
       step made the mesh worse, it is rolled back; the best state wins.

    Split branches are allowed to become open edges: a watertight mesh is not
    always possible without deleting geometry, so the report says how many
    open edges appeared. The mesh datablock is only written when at least one
    step was accepted.
    """
    report: Dict = {
        "ok": False,
        "changed": False,
        "rolled_back": False,
        "before": {},
        "after": {},
        "steps": [],
        "zero_faces": 0,
        "duplicate_faces": 0,
        "internal_faces": 0,
        "welded": 0,
        "split": 0,
        "notes": [],
        "error": "",
    }
    bm = bmesh.new()
    best = None
    try:
        bm.from_mesh(mesh)
        if not len(bm.faces):
            stats = _bm_stats(bm)
            report["before"] = stats
            report["after"] = stats
            report["non_manifold_before"] = 0
            report["non_manifold_after"] = 0
            report["boundary_before"] = stats["boundary"]
            report["boundary_after"] = stats["boundary"]
            report["area_before"] = stats["area"]
            report["area_after"] = stats["area"]
            report["ok"] = True
            report["notes"].append("the mesh has no faces")
            return report

        before = _bm_stats(bm)
        report["before"] = before
        if not before["non_manifold"]:
            report["after"] = before
            report["non_manifold_before"] = 0
            report["non_manifold_after"] = 0
            report["boundary_before"] = before["boundary"]
            report["boundary_after"] = before["boundary"]
            report["area_before"] = before["area"]
            report["area_after"] = before["area"]
            report["ok"] = True
            report["notes"].append("no non-manifold edges")
            return report

        best = bm.copy()
        current = before

        def try_step(name, apply, accept):
            nonlocal best, current
            trial = best.copy()
            fields = apply(trial)
            if fields is None:
                trial.free()
                return False
            stats = _bm_stats(trial)
            if stats["faces"] > 0 and accept(stats):
                best.free()
                best = trial
                record = {
                    "name": name,
                    "accepted": True,
                    "before": current,
                    "after": stats,
                }
                record.update(fields)
                report["steps"].append(record)
                current = stats
                return True
            trial.free()
            report["steps"].append(
                {
                    "name": name,
                    "accepted": False,
                    "before": current,
                    "after": stats,
                }
            )
            report["rolled_back"] = True
            return False

        def remove_zero_faces(trial):
            faces = [face for face in trial.faces if face.calc_area() < 1e-12]
            if not faces:
                return None
            bmesh.ops.delete(trial, geom=faces, context="FACES")
            _drop_loose(trial)
            return {"zero_faces": len(faces)}

        def accept_zero(stats):
            area_ok = abs(stats["area"] - current["area"]) <= max(
                1e-9, current["area"] * 1e-6
            )
            return (
                area_ok
                and stats["non_manifold"] <= current["non_manifold"]
                and stats["boundary"] <= current["boundary"]
            )

        def remove_duplicate_faces(trial):
            trial.verts.index_update()
            seen = set()
            duplicates = []
            for face in trial.faces:
                key = tuple(sorted(vert.index for vert in face.verts))
                if key in seen:
                    duplicates.append(face)
                else:
                    seen.add(key)
            if not duplicates:
                return None
            bmesh.ops.delete(trial, geom=duplicates, context="FACES")
            _drop_loose(trial)
            return {"duplicate_faces": len(duplicates)}

        def accept_duplicate(stats):
            return (
                stats["non_manifold"] <= current["non_manifold"]
                and stats["boundary"] <= current["boundary"]
            )

        def remove_internal_faces(trial):
            removed = 0
            for _ in range(8):
                internal = [
                    face
                    for face in trial.faces
                    if len(face.edges) >= 3
                    and all(len(edge.link_faces) > 2 for edge in face.edges)
                ]
                if not internal:
                    break
                bmesh.ops.delete(trial, geom=internal, context="FACES")
                _drop_loose(trial)
                removed += len(internal)
            if not removed:
                return None
            return {"internal_faces": removed}

        def accept_internal(stats):
            # Removing a hidden wall lowers the area; that is expected. A face
            # is only "internal" when every one of its edges is branching, so
            # the deleted surface was not part of the outer skin.
            return (
                stats["non_manifold"] < current["non_manifold"]
                and stats["boundary"] <= current["boundary"]
                and stats["area"] >= current["area"] * 0.5
            )

        def weld_branch_verts(trial):
            edges = _non_manifold_edges(trial)
            if not edges:
                return None
            verts = list({vert for edge in edges for vert in edge.verts})
            if not verts:
                return None
            before_verts = len(trial.verts)
            distance = max(_bbox_diagonal(trial) * 1e-6, 1e-9)
            bmesh.ops.remove_doubles(trial, verts=verts, dist=distance)
            welded = before_verts - len(trial.verts)
            if welded <= 0:
                return None
            return {"welded": welded, "weld_distance": distance}

        def accept_weld(stats):
            area_ok = abs(stats["area"] - current["area"]) <= max(
                1e-9, current["area"] * max_area_change
            )
            return (
                area_ok
                and stats["non_manifold"] < current["non_manifold"]
                and stats["boundary"] <= current["boundary"]
            )

        def split_branches(trial):
            total = 0
            for _ in range(3):
                edges = _non_manifold_edges(trial)
                if not edges:
                    break
                count = len(edges)
                bmesh.ops.split_edges(trial, edges=edges)
                total += count
                if len(_non_manifold_edges(trial)) >= count:
                    break
            if not total:
                return None
            return {"split": total}

        def accept_split(stats):
            area_ok = abs(stats["area"] - current["area"]) <= max(
                1e-9, current["area"] * 1e-3
            )
            return area_ok and stats["non_manifold"] < current["non_manifold"]

        try_step("zero_faces", remove_zero_faces, accept_zero)
        try_step("duplicate_faces", remove_duplicate_faces, accept_duplicate)
        try_step("internal_faces", remove_internal_faces, accept_internal)
        try_step("targeted_weld", weld_branch_verts, accept_weld)
        try_step("split_edges", split_branches, accept_split)

        report["after"] = current
        report["changed"] = any(step["accepted"] for step in report["steps"])
        report["non_manifold_before"] = before["non_manifold"]
        report["non_manifold_after"] = current["non_manifold"]
        report["boundary_before"] = before["boundary"]
        report["boundary_after"] = current["boundary"]
        report["area_before"] = before["area"]
        report["area_after"] = current["area"]
        for step in report["steps"]:
            if not step["accepted"]:
                continue
            for key in (
                "zero_faces",
                "duplicate_faces",
                "internal_faces",
                "welded",
                "split",
            ):
                report[key] += int(step.get(key, 0))

        if report["changed"]:
            bmesh.ops.recalc_face_normals(best, faces=best.faces[:])
            best.to_mesh(mesh)
            mesh.update()

        if report["changed"]:
            if not current["non_manifold"]:
                report["notes"].append("non-manifold edges fixed")
            else:
                report["notes"].append(
                    f"{current['non_manifold']} non-manifold edge(s) remain - "
                    f"likely self-intersections or thin walls"
                )
            open_edges = current["boundary"] - before["boundary"]
            if open_edges > 0:
                report["notes"].append(
                    f"{open_edges} new open edge(s) appeared - close them if the "
                    f"mesh must be watertight"
                )
            if current["area"]:
                change = abs(current["area"] - before["area"]) / current["area"]
                if change > 0.01:
                    report["notes"].append(
                        f"the surface area changed from {before['area']:.2f} to "
                        f"{current['area']:.2f} m2"
                    )
        else:
            report["notes"].append(
                "no safe repair found; the mesh was left unchanged"
            )
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a bad mesh must not kill Blender
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if best is not None:
            best.free()
        bm.free()
    return report


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
    """How far the vertices of *obj* are from the surface of *target*.

    Both objects are measured in the same space: the vertices of *obj* are
    put into the world and the target is measured in world coordinates too.
    A model with a scale of 26 would otherwise report millimetres where
    there are metres.
    """
    total = 0.0
    worst = 0.0
    count = 0
    vertices = obj.data.vertices
    step = max(1, len(vertices) // max(1, limit))
    matrix = obj.matrix_world
    target_matrix = target.matrix_world
    for index, vert in enumerate(vertices):
        if index % step:
            continue
        # The target's mesh lives in its own local space.
        local = target_matrix.inverted() @ (matrix @ vert.co)
        hit, location, _normal, _face = target.closest_point_on_mesh(local)
        if not hit:
            continue
        distance = (matrix @ vert.co - (target_matrix @ location)).length
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
    exact: bool = False,
    fix_non_manifold: bool = True,
) -> Dict:
    """Make a game mesh from a high-poly one and return a report.

    The original is never touched: the result is a **new object** next to it,
    so the sculpt stays available as the shrinkwrap target and as a fallback.

    ``exact`` picks the remesher: the octree (Blocks) follows the shape
    better but only doubles the resolution, so the budget lands on a step.
    The voxel mode is continuous and hits the budget, but its topology is
    grid-aligned - see :func:`_pick_voxel`.
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
        "non_manifold_before": 0,
        "non_manifold_fix": {},
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
    report["area_source"] = source.area
    report["holes_source"] = source.boundary
    report["doubles_source"] = source.doubles

    started = time.time()
    source_mesh = obj.data.copy()
    # The working copy is repaired first: a shell with holes remeshes into
    # nonsense, and the shrinkwrap then smears the polygons.
    repair = _repair_mesh(source_mesh)
    report["repair"] = repair
    if repair.get("error"):
        report["notes"].append(
            f"the working copy could not be repaired ({repair['error']})"
        )
    if repair["holes"]:
        report["notes"].append(
            f"the source had {repair['holes']} hole edge(s); "
            f"{repair['holes_filled']} hole(s) closed in the working copy"
        )
    if repair["doubles"]:
        report["notes"].append(
            f"{repair['doubles']} doubled vertices welded in the working copy"
        )

    # A model that fell apart is the usual reason for smeared polygons: the
    # remesh glues the pieces together and the shrinkwrap pulls them onto one
    # surface. Say it before the result, not after.
    parts = _parts(obj.data)
    report["parts"] = len(parts)
    if len(parts) > 5:
        share = 0.0
        if parts and parts[0].get("area"):
            share = parts[0]["area"] / sum(p["area"] for p in parts) * 100
        report["notes"].append(
            f"the source is {len(parts)} separate pieces (the biggest is "
            f"{share:.0f}% of the surface) - repair the mesh first, or the "
            f"pieces will be glued together and the polygons will smear"
        )
    if source.boundary > 50:
        report["notes"].append(
            f"the source has {source.boundary} open edge(s); the shrinkwrap "
            f"projects onto an open surface and can smear the polygons"
        )

    # The repaired copy is also the target the new mesh is pulled onto.
    # It must keep the transform of the source: a model that was imported
    # with a scale of 26 and a location of 300 metres lives in its own
    # coordinates, and a target at the origin is a surface somewhere else.
    # It must also stay in the depsgraph while the shrinkwrap runs: a
    # hidden target has no evaluated mesh and the modifier fails.
    target = bpy.data.objects.new(f"{obj.name}_RetopoTarget", source_mesh)
    bpy.context.scene.collection.objects.link(target)
    target.matrix_world = obj.matrix_world.copy()
    target.hide_render = True

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
        if exact:
            # The voxel size is continuous, so the budget is a real knob.
            # The cost is the topology: the quads follow the voxel grid.
            diagonal = max(
                (obj.matrix_world @ Vector(corner)).length
                for corner in obj.bound_box
            ) or 1.0
            voxel, count = _pick_voxel(work, source_mesh, budget, diagonal)
            _apply_voxel(work, source_mesh, voxel)
            report["mode"] = "voxel"
            report["voxel"] = round(voxel, 5)
            report["depth"] = 0
            report["notes"].append(
                f"voxel mode: {voxel * 1000:.1f} mm cells, {count} triangles "
                f"(the grid follows the shape, not the form)"
            )
        else:
            # Find the depth whose triangle count is closest to the budget
            # without going over it. Each attempt is a remesh, so the count is
            # measured, not guessed from a table. The budget is in triangles,
            # because that is what the renderer counts: a quad is two of them.
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
            report["mode"] = "blocks"
            report["depth"] = chosen
        report["faces"] = len(work.data.polygons)
        report["tris"] = _triangle_count(work.data)

        if shrinkwrap and repair["remaining_holes"]:
            # An open surface has no inside: projecting onto it folds the
            # mesh onto itself. Better to skip and to say so.
            report["notes"].append(
                f"the shrinkwrap was skipped: {repair['remaining_holes']} "
                f"hole edge(s) are still open, and projecting onto an open "
                f"shell smears the polygons"
            )
        elif shrinkwrap:
            _apply_shrinkwrap(work, target)
        else:
            report["notes"].append("the surface was not pulled back onto the sculpt")

        # The remesh can leave branching edges. Repair them before the final
        # cleanup, so the smoothing and the sharp edges see the final
        # topology. The repair is measured and rolls itself back on a bad
        # step; it never does a global weld.
        if fix_non_manifold:
            fix_report = repair_non_manifold(work.data)
            report["non_manifold_before"] = (
                fix_report.get("before", {}).get("non_manifold", 0)
            )
            report["non_manifold_fix"] = fix_report
        else:
            report["non_manifold_before"] = analyze(work).non_manifold

        _cleanup(work.data)
        # The distance is measured on the finished mesh, not on the raw
        # remesh: the report must describe what the user gets.
        if shrinkwrap and not repair["remaining_holes"]:
            report["deviation"] = deviation(work, target)
        result = analyze(work)
        report["object"] = work.name
        report["quads"] = result.quads
        report["tri_faces"] = result.tris
        # The number the budget is about: what the renderer will see.
        report["tris"] = result.tri_equivalent
        report["verts"] = result.verts
        report["non_manifold"] = result.non_manifold
        report["doubles"] = result.doubles
        report["area_result"] = result.area
        report["zero_faces"] = result.zero_faces
        if source.area > 0:
            report["area_ratio"] = result.area / source.area
            if report["area_ratio"] > 3.0:
                report["notes"].append(
                    f"the surface became {report['area_ratio']:.1f} times "
                    f"bigger - the mesh is probably smearing; try a bigger "
                    f"budget or turn the shrinkwrap off"
                )
        report["seconds"] = time.time() - started
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a failed retopo must not kill Blender
        report["error"] = f"{type(exc).__name__}: {exc}"
        try:
            bpy.data.objects.remove(work, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    finally:
        # The target was only a scaffold for the shrinkwrap.
        try:
            bpy.data.objects.remove(target, do_unlink=True)
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
        "weld": float(getattr(scene, "gaa_mesh_weld", 0.0001)),
        "fill_holes": bool(getattr(scene, "gaa_mesh_fill_holes", False)),
        "exact": bool(getattr(scene, "gaa_retopo_exact", False)),
        "fix_non_manifold": bool(
            getattr(scene, "gaa_mesh_fix_non_manifold", True)
        ),
    }


def register() -> None:
    """Register the per-scene retopology settings."""
    from bpy.props import BoolProperty, FloatProperty, IntProperty

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
    bpy.types.Scene.gaa_mesh_weld = FloatProperty(
        name="Порог сварки / Weld distance",
        description=(
            "На каком расстоянии вершины считаются одной / At what distance "
            "two vertices count as one"
        ),
        default=0.0001,
        min=0.0,
        max=1.0,
        precision=4,
        subtype="DISTANCE",
    )
    bpy.types.Scene.gaa_mesh_fill_holes = BoolProperty(
        name="Закрывать дыры / Fill holes",
        description=(
            "Залить открытые дыры в сетке (меняет поверхность) / Fill the open "
            "holes in the mesh (this changes the surface)"
        ),
        default=False,
    )
    bpy.types.Scene.gaa_retopo_exact = BoolProperty(
        name="Точно по бюджету / Exact budget",
        description=(
            "Подбирать воксель под заданный бюджет: попадание точное, но "
            "топология идёт по сетке, а не по форме. Выключено — октаэдра: "
            "форма лучше, бюджет попадает только на ступеньки / Search the "
            "voxel size for the budget: exact, but the topology follows the "
            "grid. Off - octree: better shape, the budget lands on a step"
        ),
        default=False,
    )
    bpy.types.Scene.gaa_mesh_fix_non_manifold = BoolProperty(
        name="Чинить non-manifold / Repair non-manifold",
        description=(
            "После ремеша убрать внутренние и дублирующиеся грани, точечно "
            "сварить вершины на развилках и разделить оставшиеся ветви. Шаг "
            "откатывается, если делает сетку хуже / After the remesh, remove "
            "internal and duplicate faces, weld only the vertices on branching "
            "edges and split the rest. A step is rolled back if it makes the "
            "mesh worse"
        ),
        default=True,
    )


def unregister() -> None:
    for name in (
        "gaa_mesh_fix_non_manifold",
        "gaa_retopo_exact",
        "gaa_mesh_fill_holes",
        "gaa_mesh_weld",
        "gaa_lod_levels",
        "gaa_retopo_shrinkwrap",
        "gaa_retopo_budget",
    ):
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
    "is a new object next to it. By default it also repairs the non-manifold "
    "edges the remesh leaves behind.",
    params=("object", "budget", "shrinkwrap", "exact", "fix_non_manifold"),
)
def retopo_tool(
    object=None,
    budget=None,
    shrinkwrap=None,
    exact=None,
    fix_non_manifold=None,
    **kwargs,
) -> str:
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
    precise = settings["exact"] if exact is None else bool(exact)
    fix = (
        settings["fix_non_manifold"]
        if fix_non_manifold is None
        else bool(fix_non_manifold)
    )
    report = retopo(obj, limit, pull, exact=precise, fix_non_manifold=fix)
    if not report.get("ok"):
        return f"The retopology did not finish: {report.get('error', 'unknown')}"

    mode = report.get("mode", "blocks")
    detail = (
        f"voxel {report.get('voxel', 0) * 1000:.1f} mm"
        if mode == "voxel"
        else f"remesh depth {report.get('depth', 0)}"
    )
    lines = [
        f"Made '{report['object']}' from '{report['source']}' in "
        f"{report['seconds']:.1f}s: {report['quads']} quads "
        f"({report['tris']} triangles, {detail}, budget {limit})."
    ]
    error = report.get("deviation") or {}
    if error.get("samples"):
        lines.append(
            f"It sits {error['mean'] * 1000:.1f} mm from the sculpt on average "
            f"(worst {error['max'] * 1000:.1f} mm)."
        )
    fix_report = report.get("non_manifold_fix") or {}
    if fix_report.get("error"):
        lines.append(
            f"The non-manifold repair did not finish: {fix_report['error']}"
        )
    elif fix_report.get("changed"):
        lines.append(
            f"Non-manifold edges: {report.get('non_manifold_before', 0)} -> "
            f"{report.get('non_manifold', 0)} "
            f"({fix_report.get('internal_faces', 0)} internal faces removed, "
            f"{fix_report.get('split', 0)} branches split)."
        )
        if report.get("non_manifold"):
            lines.append(
                f"{report['non_manifold']} non-manifold edge(s) remain - likely "
                f"self-intersections or thin walls."
            )
        opened = fix_report.get("boundary_after", 0) - fix_report.get(
            "boundary_before", 0
        )
        if opened > 0:
            lines.append(
                f"{opened} open edge(s) appeared after splitting the branches - "
                f"close them if the mesh must be watertight."
            )
    elif report.get("non_manifold"):
        lines.append(
            f"It has {report['non_manifold']} non-manifold edges - the remesh "
            f"leaves them, check the mesh before exporting."
        )
    ratio = report.get("area_ratio")
    if ratio:
        lines.append(
            f"The surface area went from {report.get('area_source', 0):.2f} to "
            f"{report.get('area_result', 0):.2f} m2 (x{ratio:.1f})."
        )
    for note in report.get("notes", []):
        lines.append(f"- {note}")
    lines.append("The original is untouched; unwrap the new mesh next.")
    return "\n".join(lines)


@tools.tool(
    "mesh.repair",
    "Repair the mesh",
    "Fix the mesh of the active object: weld the vertices that sit in the "
    "same place, drop the faces with no area and the loose vertices, and make "
    "the normals consistent. Do this before a retopology: a model that fell "
    "apart into pieces remeshes into a smeared shell.",
    params=("object", "weld", "fill_holes"),
)
def repair_tool(object=None, weld=None, fill_holes=None, **kwargs) -> str:
    """Tool entry point: repair the mesh."""
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object to repair."
    if obj.type != "MESH":
        return f"'{obj.name}' is not a mesh."

    settings = settings_from_scene()
    distance = float(weld) if weld is not None else settings["weld"]
    holes = bool(fill_holes) if fill_holes is not None else settings["fill_holes"]
    report = repair(obj, distance, holes)
    if not report.get("ok"):
        return f"The repair did not finish: {report.get('error', 'unknown')}"

    before = report["before"]
    after = report["after"]
    lines = [
        f"Repaired '{report['object']}': "
        f"{before['verts']} -> {after['verts']} verts, "
        f"{before['faces']} -> {after['faces']} faces."
    ]
    lines.append(
        f"- welded {report['welded']} doubled vertices, "
        f"removed {report['zero_faces']} empty faces and "
        f"{report['loose_verts']} loose vertices"
    )
    lines.append(
        f"- parts: {before['parts']} -> {after['parts']}, "
        f"open edges: {before['holes']} -> {after['holes']}"
    )
    if report.get("holes_filled"):
        lines.append(f"- closed {report['holes_filled']} hole(s)")
    for note in report.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines)


@tools.tool(
    "mesh.repair_non_manifold",
    "Repair non-manifold edges",
    "Find the edges that more than two faces share and repair them without a "
    "global weld: remove internal and duplicate faces, weld only the vertices "
    "on those edges, and split whatever is still branching. Every step is "
    "measured; a step that makes the mesh worse is rolled back. It is safe to "
    "run twice.",
    params=("object",),
)
def repair_non_manifold_tool(object=None, **kwargs) -> str:
    """Tool entry point: repair the non-manifold edges."""
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object to repair."
    if obj.type != "MESH":
        return f"'{obj.name}' is not a mesh."

    report = repair_non_manifold(obj.data)
    if not report.get("ok"):
        return (
            f"The non-manifold repair did not finish: "
            f"{report.get('error', 'unknown')}"
        )

    before = report["before"]
    after = report["after"]
    if not report.get("changed"):
        if not before["non_manifold"]:
            return f"'{obj.name}' has no non-manifold edges."
        return (
            f"'{obj.name}' was left unchanged: no safe non-manifold repair was "
            f"found ({before['non_manifold']} edge(s) still branching)."
        )

    lines = [
        f"Repaired non-manifold edges on '{obj.name}': "
        f"{before['non_manifold']} -> {after['non_manifold']}."
    ]
    if not after["non_manifold"]:
        lines.append("The mesh has no non-manifold edges now.")
    else:
        lines.append(
            f"{after['non_manifold']} non-manifold edge(s) remain - likely "
            f"self-intersections or thin walls."
        )
    if report.get("internal_faces"):
        lines.append(f"- internal faces removed: {report['internal_faces']}")
    if report.get("duplicate_faces"):
        lines.append(f"- duplicate faces removed: {report['duplicate_faces']}")
    if report.get("zero_faces"):
        lines.append(f"- zero-area faces removed: {report['zero_faces']}")
    if report.get("welded"):
        lines.append(f"- vertices welded locally: {report['welded']}")
    if report.get("split"):
        lines.append(f"- branches split: {report['split']}")
    opened = after["boundary"] - before["boundary"]
    lines.append(
        f"Open edges: {before['boundary']} -> {after['boundary']}"
        + (f" ({opened} new)" if opened > 0 else "")
    )
    if opened > 0:
        lines.append(
            "Close the new open edges if the mesh must be watertight."
        )
    if before["area"]:
        change = abs(after["area"] - before["area"]) / before["area"]
        if change > 0.01:
            lines.append(
                f"Surface area: {before['area']:.2f} -> {after['area']:.2f} m2"
            )
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
