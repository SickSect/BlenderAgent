"""Baking a procedural material into textures.

A procedural material is beautiful and useless outside Blender: a game engine
cannot evaluate a noise texture. Baking is the bridge — Cycles renders the
material into a bitmap, and that bitmap travels.

The hard part is not the rendering, it is the **setup**. Blender's bake needs,
all at once:

* the **Cycles** engine (EEVEE cannot bake at all);
* a **UV layout** without overlaps, or the islands bleed into each other;
* an **image datablock** per bake target, with the right colour space and
  float buffer;
* an **image texture node** in the material that is *both active and
  selected* — that is how Blender knows where to write.

Get any of it wrong and you get a black, striped or silently wrong texture.
This module does the setup, runs the bake and cleans up after itself.

Two Blender facts that cost real time to discover are baked into the code:

* ``other is node`` is **always False** for bpy structures — every access
  hands out a fresh Python wrapper. The bake target has to be found with
  ``==``, which compares the pointers.
* The bake looks for the *active and selected* image texture node. Deselecting
  the other nodes is fine; deselecting the target is fatal, and the error
  message ("No active and selected image texture node found") does not say
  which node was meant.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import bpy

from . import findings, tools, uv

# The bake types we offer, with the settings each one needs.
BAKE_TYPES = {
    "albedo": {"type": "COMBINED", "data": False},
    "diffuse": {"type": "DIFFUSE", "data": False},
    "normal": {"type": "NORMAL", "data": True},
    "roughness": {"type": "ROUGHNESS", "data": True},
    "ao": {"type": "AO", "data": True},
    "shadow": {"type": "SHADOW", "data": True},
    "emission": {"type": "EMIT", "data": False},
}

# The ready-made sets of maps. "standard" is what a game asset usually needs.
BAKE_SETS = {
    "standard": ("albedo", "normal", "roughness", "ao"),
    "game": ("albedo", "normal", "roughness"),
    "albedo": ("albedo",),
    "normal": ("normal",),
    "ao": ("ao",),
}

# The engine the bake needs.
BAKE_ENGINE = "CYCLES"

# Finding codes.
NO_UV = "bake_no_uv"
OVERLAP = "bake_overlap"
NO_HEIGHT = "bake_no_height"
NOT_CYCLES = "bake_not_cycles"
NO_MATERIAL = "bake_no_material"


@dataclass
class BakeTarget:
    """One map to bake: the image, the node that receives it, the settings."""

    key: str
    bake_type: str
    image: object
    node: object
    data: bool = False


def _color_space(data: bool) -> str:
    """The colour space a baked map needs: data maps are not colour."""
    return "Non-Color" if data else "sRGB"


def _has_height_source(material) -> bool:
    """Whether the material has something to derive a normal map from."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return False
    for node in tree.nodes:
        if node.type == "BUMP":
            return True
        if node.type == "DISPLACEMENT":
            return True
    # A Displacement wired into the material output counts too.
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is not None:
        for link in output.inputs["Displacement"].links:
            if link.from_node.type == "DISPLACEMENT":
                return True
    return False


def check_ready(obj) -> List:
    """Is this object ready to be baked?"""
    out = []
    if getattr(obj, "type", "") != "MESH":
        return out

    mesh = getattr(obj, "data", None)
    uv_layers = getattr(mesh, "uv_layers", None)
    if not uv_layers or not len(uv_layers):
        out.append(
            findings.make(
                NO_UV,
                findings.ERROR,
                {"obj": obj.name},
                subject=obj.name,
                key_prefix="bake",
            )
        )
        return out

    report = uv.analyze(obj)
    if report.overlap_pairs:
        out.append(
            findings.make(
                OVERLAP,
                findings.ERROR,
                {"obj": obj.name, "n": len(report.overlap_pairs)},
                subject=obj.name,
                key_prefix="bake",
            )
        )

    material = getattr(obj, "active_material", None)
    if material is None:
        out.append(
            findings.make(
                NO_MATERIAL,
                findings.ERROR,
                {"obj": obj.name},
                subject=obj.name,
                key_prefix="bake",
            )
        )
        return out

    if not _has_height_source(material):
        out.append(
            findings.make(
                NO_HEIGHT,
                findings.INFO,
                {"obj": obj.name},
                subject=obj.name,
                key_prefix="bake",
            )
        )

    try:
        engine = bpy.context.scene.render.engine
    except Exception:
        engine = ""
    if engine != BAKE_ENGINE:
        out.append(
            findings.make(
                NOT_CYCLES,
                findings.INFO,
                {"obj": obj.name, "engine": engine},
                subject=obj.name,
                key_prefix="bake",
            )
        )
    return out


def _make_target(material, key: str, size: int, name: str) -> BakeTarget:
    """Create the image and the node that receives one bake.

    The image is named after the **object**, not the material: the report and
    the saved files are about the object, and "Crate_albedo.png" says more
    than "Crate_mat_albedo.png".
    """
    spec = BAKE_TYPES[key]
    tree = material.node_tree
    image = bpy.data.images.new(
        f"{name}_{key}",
        width=size,
        height=size,
        alpha=False,
        float_buffer=spec["data"],
    )
    image.colorspace_settings.name = _color_space(spec["data"])
    node = tree.nodes.new("ShaderNodeTexImage")
    node.name = f"Bake_{key}"
    node.label = key
    node.image = image
    node.location = (700, 300 - 200 * len(tree.nodes))
    return BakeTarget(
        key=key,
        bake_type=spec["type"],
        image=image,
        node=node,
        data=spec["data"],
    )


def _activate_target(material, target: BakeTarget) -> None:
    """Make *target* the node the bake writes into.

    The node has to be **active and selected**. Everything else is deselected.
    NOTE: the comparison is ``==``, not ``is`` — bpy hands out a fresh
    wrapper on every access, so ``is`` would never match and the bake would
    silently find no target.
    """
    tree = material.node_tree
    for node in tree.nodes:
        node.select = node == target.node
    tree.nodes.active = target.node


def _save_image(image, path: str) -> bool:
    """Write a baked image to disk."""
    try:
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    except Exception:  # noqa: BLE001
        return False
    return os.path.exists(path)


def inspect_image(image) -> Dict:
    """What actually ended up in a baked image.

    A bake can "succeed" and still be useless: a normal map without a height
    source comes out flat, an emission map without emission comes out black.
    The report says so, instead of leaving the user to wonder.
    """
    result = {"filled": 0.0, "flat": True, "mean": 0.0, "empty": True}
    if image is None:
        return result
    try:
        import numpy as np

        width, height = image.size
        if not width or not height:
            return result
        buffer = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        array = buffer.reshape(height, width, 4)
        rgb = array[:, :, :3]
        result["filled"] = float((rgb.max(axis=2) > 0.002).mean())
        result["mean"] = float(rgb.mean())
        result["empty"] = result["filled"] < 0.001
        result["flat"] = bool(
            np.all(np.abs(rgb[:, :, 2] - 1.0) < 0.01)
            and np.all(np.abs(rgb[:, :, 0] - 0.5) < 0.01)
            and np.all(np.abs(rgb[:, :, 1] - 0.5) < 0.01)
        )
    except Exception:  # noqa: BLE001 - a check must not break a bake
        pass
    return result


def expand_maps(maps) -> List[str]:
    """The wanted map names, with the ready-made sets expanded."""
    if isinstance(maps, str):
        wanted = [maps]
    elif isinstance(maps, (list, tuple)):
        wanted = [str(m) for m in maps]
    else:
        wanted = ["albedo"]
    out = []
    for name in wanted:
        key = name.strip().lower()
        if key in BAKE_SETS:
            out.extend(BAKE_SETS[key])
        elif key in BAKE_TYPES:
            out.append(key)
    # Keep the order but drop the duplicates.
    seen = set()
    unique = []
    for name in out:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def bake(
    obj,
    targets: Sequence[str] = ("albedo",),
    size: int = 1024,
    margin: int = 16,
    samples: int = 32,
    save_to: Optional[str] = None,
) -> Dict:
    """Bake the given maps of *obj*.

    Returns a report: what was baked, how long it took and where the files
    are. The temporary nodes are removed afterwards; the images stay in the
    file so they can be packed or saved later.
    """
    report = {
        "object": obj.name,
        "maps": [],
        "results": {},
        "seconds": 0.0,
        "files": [],
        "ok": False,
    }
    material = getattr(obj, "active_material", None)
    if material is None or getattr(material, "node_tree", None) is None:
        report["error"] = "the object has no material"
        return report

    wanted = [key for key in targets if key in BAKE_TYPES]
    if not wanted:
        report["error"] = "no known bake type was asked for"
        return report

    scene = bpy.context.scene
    old_engine = scene.render.engine
    old_samples = getattr(scene.cycles, "samples", None) if hasattr(scene, "cycles") else None

    created = []
    try:
        scene.render.engine = BAKE_ENGINE
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples

        # The object has to be the only selected one, and active.
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        started = time.time()
        for key in wanted:
            target = _make_target(material, key, size, obj.name)
            created.append(target)
            _activate_target(material, target)
            result = bpy.ops.object.bake(
                type=target.bake_type,
                margin=margin,
                margin_type="EXTEND",
                use_clear=True,
            )
            if "FINISHED" not in result:
                report["error"] = f"the {key} bake was cancelled"
                return report
            report["maps"].append(key)
            report["results"][key] = inspect_image(target.image)
            if save_to:
                folder = save_to
                try:
                    os.makedirs(folder, exist_ok=True)
                except OSError:
                    folder = None
                if folder:
                    path = os.path.join(folder, f"{obj.name}_{key}.png")
                    if _save_image(target.image, path):
                        report["files"].append(path)
        report["seconds"] = time.time() - started
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a failed bake must not kill Blender
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Remove the temporary nodes: they are only a channel for the bake.
        for target in created:
            try:
                material.node_tree.nodes.remove(target.node)
            except Exception:  # noqa: BLE001
                continue
        scene.render.engine = old_engine
        if old_samples is not None and hasattr(scene, "cycles"):
            try:
                scene.cycles.samples = old_samples
            except Exception:
                pass
    return report


def baked_images(material) -> List:
    """The images left in the file by a bake."""
    out = []
    for image in bpy.data.images:
        if image.name.startswith(f"{material.name}_") and image.users == 0:
            out.append(image)
    return out


# --- the atlas bake --------------------------------------------------------
#
# Several objects into one texture. Blender bakes every selected object into
# the target that lives in **its own** material - so every object needs its own
# target node, and all of them have to point at the *same* image. Miss one and
# the bake is cancelled with "No active and selected image texture node found
# in material ...".

def _materials_of(objects) -> List:
    """The materials of the objects, without duplicates and without None."""
    out = []
    for obj in objects:
        material = getattr(obj, "active_material", None)
        if material is None:
            continue
        if getattr(material, "node_tree", None) is None:
            continue
        if material not in out:
            out.append(material)
    return out


def check_atlas_padding(atlas_size: int, gap: float, bake_margin: int) -> List:
    """Is the gap between the islands big enough for the bake margin?

    The bake margin dilates every island by that many pixels. If the islands
    are closer than that, they bleed into each other and the atlas turns into
    a smear at the borders.
    """
    gap_pixels = gap * atlas_size
    if bake_margin <= gap_pixels:
        return []
    return [
        findings.make(
            "bake_gap_too_small",
            findings.WARNING,
            {
                "gap": round(gap_pixels),
                "margin": bake_margin,
            },
            subject="atlas",
            key_prefix="bake",
        )
    ]


def bake_atlas(
    objects: Sequence,
    targets: Sequence[str] = ("albedo",),
    size: int = 1024,
    margin: int = 16,
    samples: int = 32,
    save_to: Optional[str] = None,
) -> Dict:
    """Bake several objects into one shared atlas image.

    Every object needs its own UV layout inside the same texture - the atlas
    builder (chapter 19) lays them out so.
    """
    report = {
        "objects": len(objects),
        "maps": [],
        "seconds": 0.0,
        "files": [],
        "ok": False,
    }
    usable = [obj for obj in objects if getattr(obj, "active_material", None)]
    if not usable:
        report["error"] = "the objects have no materials"
        return report

    wanted = [key for key in targets if key in BAKE_TYPES]
    if not wanted:
        report["error"] = "no known bake type was asked for"
        return report

    scene = bpy.context.scene
    old_engine = scene.render.engine
    old_samples = getattr(scene.cycles, "samples", None) if hasattr(scene, "cycles") else None

    created_nodes = []
    try:
        scene.render.engine = BAKE_ENGINE
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples

        bpy.ops.object.select_all(action="DESELECT")
        for obj in usable:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = usable[0]
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        started = time.time()
        for key in wanted:
            spec = BAKE_TYPES[key]
            image = bpy.data.images.new(
                f"Atlas_{key}",
                width=size,
                height=size,
                alpha=False,
                float_buffer=spec["data"],
            )
            image.colorspace_settings.name = _color_space(spec["data"])

            # Every object needs a target in ITS OWN material, all pointing
            # at the same image.
            for obj in usable:
                material = obj.active_material
                node = material.node_tree.nodes.new("ShaderNodeTexImage")
                node.name = f"Bake_{key}"
                node.label = key
                node.image = image
                node.location = (700, 300 - 200 * len(material.node_tree.nodes))
                for other in material.node_tree.nodes:
                    other.select = other == node
                material.node_tree.nodes.active = node
                created_nodes.append((material, node))

            result = bpy.ops.object.bake(
                type=spec["type"],
                margin=margin,
                margin_type="EXTEND",
                use_clear=True,
            )
            if "FINISHED" not in result:
                report["error"] = f"the {key} bake was cancelled"
                return report
            report["maps"].append(key)
            if save_to:
                try:
                    os.makedirs(save_to, exist_ok=True)
                except OSError:
                    save_to = None
                if save_to:
                    path = os.path.join(save_to, f"atlas_{key}.png")
                    if _save_image(image, path):
                        report["files"].append(path)
        report["seconds"] = time.time() - started
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - a failed bake must not kill Blender
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for material, node in created_nodes:
            try:
                material.node_tree.nodes.remove(node)
            except Exception:  # noqa: BLE001
                continue
        scene.render.engine = old_engine
        if old_samples is not None and hasattr(scene, "cycles"):
            try:
                scene.cycles.samples = old_samples
            except Exception:
                pass
    return report


# --- the baked material ----------------------------------------------------


def build_baked_material(
    albedo=None,
    normal=None,
    roughness=None,
    name: str = "Baked",
):
    """A plain material built from the baked images.

    This is the point of the whole exercise: the game engine cannot evaluate
    a procedural material, so it gets a normal Principled BSDF with plain
    textures instead.

    The normal map goes through a **Normal Map node** - a normal map plugged
    straight into the Normal input is the classic mistake.
    """
    from . import api

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    bsdf = api.find_principled(tree)
    if bsdf is None:
        return material

    def image_node(image, label, location):
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = label
        node.label = label
        node.image = image
        node.location = location
        return node

    if albedo is not None:
        node = image_node(albedo, "Albedo", (-600, 300))
        tree.links.new(node.outputs["Color"], api.get_input(bsdf, "Base Color"))

    if normal is not None:
        node = image_node(normal, "Normal", (-600, -100))
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.name = "Normal Map"
        normal_map.location = (-300, -100)
        tree.links.new(node.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], api.get_input(bsdf, "Normal"))

    if roughness is not None:
        node = image_node(roughness, "Roughness", (-600, -450))
        tree.links.new(node.outputs["Color"], api.get_input(bsdf, "Roughness"))

    return material


def replace_material(obj, material) -> bool:
    """Put *material* on *obj*."""
    from . import materials as materials_module

    return bool(materials_module.assign_material(obj, material))


# --- the tools -------------------------------------------------------------


@tools.tool(
    "bake.maps",
    "Bake textures",
    "Bake the procedural material of the active object into textures: albedo, "
    "normal, roughness, ambient occlusion. Sets everything up, runs the bake "
    "and cleans up after itself.",
    params=("maps", "size", "margin", "samples"),
)
def bake_tool(
    maps=None, size=1024, margin=16, samples=32, **kwargs
) -> str:
    """Tool entry point: bake the material."""
    obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object to bake."
    wanted = expand_maps(maps)

    problems = check_ready(obj)
    blocking = [p for p in problems if p.severity == findings.ERROR]
    if blocking:
        return "The object is not ready to bake:\n" + "\n".join(
            f"- {p.text('en')}" for p in blocking
        )

    report = bake(
        obj,
        targets=wanted,
        size=int(size),
        margin=int(margin),
        samples=int(samples),
    )
    if not report.get("ok"):
        return f"The bake did not finish: {report.get('error', 'unknown')}"
    lines = [
        f"Baked {', '.join(report['maps'])} for '{report['object']}' "
        f"in {report['seconds']:.1f}s."
    ]
    for key, info in report.get("results", {}).items():
        note = ""
        if info.get("empty"):
            note = " - the map is empty"
        elif key == "normal" and info.get("flat"):
            note = " - the normals are flat (no height source in the material)"
        lines.append(f"- {key}: {info.get('filled', 0) * 100:.0f}% filled{note}")
    lines.append("The images are in the file; pack them or save them to disk.")
    return "\n".join(lines)


@tools.tool(
    "bake.check",
    "Check if baking is possible",
    "Check whether the active object can be baked: a UV layout without "
    "overlaps, a material, a source of height for the normal map.",
)
def check_tool(**kwargs) -> str:
    """Tool entry point: check whether the bake can run."""
    obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object."
    problems = check_ready(obj)
    if not problems:
        return f"'{obj.name}' is ready to bake."
    return "\n".join(f"- {p.text('en')}" for p in problems)


@tools.tool(
    "bake.atlas",
    "Bake an atlas",
    "Bake several objects into one shared atlas texture. The objects must "
    "already share one UV layout (build it with the atlas tool first).",
    params=("maps", "size", "margin", "samples"),
)
def bake_atlas_tool(maps=None, size=1024, margin=16, samples=32, **kwargs) -> str:
    """Tool entry point: bake several objects into one atlas."""
    objects = [obj for obj in getattr(bpy.context, "selected_objects", [])]
    if not objects:
        objects = [
            obj for obj in bpy.context.scene.objects if obj.type == "MESH"
        ]
    if not objects:
        return "There are no meshes to bake."

    wanted = expand_maps(maps)
    if not wanted:
        return f"Unknown bake type: {maps}"

    settings = uv.settings_from_scene()
    problems = check_atlas_padding(
        int(settings.atlas), settings.margin, int(margin)
    )
    if problems:
        return "The atlas will bleed:\n" + "\n".join(
            f"- {p.text('en')}" for p in problems
        )

    report = bake_atlas(
        objects, targets=wanted, size=int(size), margin=int(margin),
        samples=int(samples),
    )
    if not report.get("ok"):
        return f"The bake did not finish: {report.get('error', 'unknown')}"
    return (
        f"Baked {', '.join(report['maps'])} for {report['objects']} object(s) "
        f"into one atlas, in {report['seconds']:.1f}s."
    )


@tools.tool(
    "bake.replace",
    "Replace the material with the baked one",
    "Build a plain material out of the baked textures (albedo, normal, "
    "roughness) and put it on the object, so the game engine does not have to "
    "evaluate the procedural material any more.",
    params=("object",),
)
def replace_tool(object=None, **kwargs) -> str:
    """Tool entry point: swap the procedural material for a baked one."""
    obj = None
    if object:
        obj = bpy.data.objects.get(str(object))
    if obj is None:
        obj = getattr(bpy.context, "active_object", None)
    if obj is None:
        return "There is no active object."

    old_material = getattr(obj, "active_material", None)
    if old_material is None:
        return "The object has no material."

    albedo = bpy.data.images.get(f"{obj.name}_albedo")
    normal = bpy.data.images.get(f"{obj.name}_normal")
    roughness = bpy.data.images.get(f"{obj.name}_roughness")
    if albedo is None and normal is None:
        return (
            f"There are no baked textures for '{obj.name}'. Bake the object "
            "first."
        )

    material = build_baked_material(
        albedo, normal, roughness, name=f"{obj.name}_Baked"
    )
    if not replace_material(obj, material):
        return "The material could not be replaced."
    return (
        f"The material of '{obj.name}' is now '{material.name}' (the "
        f"procedural one '{old_material.name}' is still in the file, unused)."
    )
