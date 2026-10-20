---
name: material-procedural
title: Procedural materials
description: Build and tune procedural materials - the recipe catalogue, the parameters, and when to bake instead
keywords: procedural, noise, voronoi, wave, wood, marble, rust, fabric, leather, snow, lava, procedural material, процедурный, шум, процедурный материал, процедурка
blender: ">=4.2"
---

# Procedural materials

A procedural material is a **node graph**, not a picture. It is built like
this:

```text
Texture Coordinate -> Mapping (stretch) -> Texture (noise/voronoi/wave)
                                          |
                     +----------------+----------------+
                     |                                 |
                 Color Ramp                      Map Range / Bump
                     |                                 |
            Base Color                       Roughness / Normal
```

Two properties matter and they are exactly why procedural materials are worth
the trouble:

* **infinite resolution** - no pixel grid, so it stays sharp when you zoom in;
* **parametric** - change one number and the whole surface follows.

The price: a game engine cannot evaluate a noise texture. Procedural materials
have to be **baked** before they leave Blender (see `material-baking`).

## Prefer the add-on's tools over hand-built graphs

The add-on assembles the graph from a **specification** - plain numbers - so
the result is always valid and always the same:

| Tool | What it does |
|---|---|
| `procedural.list` | list the ready-made recipes with their keywords |
| `procedural.build` | build a material from a recipe plus overrides |

Always ask for the recipe catalogue first if you are unsure which material
fits, then build with `procedural.build`. Do **not** write the node graph by
hand for a standard material: you will get it subtly wrong, and it will not
match the other materials of the project.

```text
procedural.build {"recipe": "wood", "name": "DarkWood",
                  "color_a": "#1a1008", "color_b": "#6b4526",
                  "roughness": 0.6, "bump_strength": 0.45}
```

## The recipes

| Recipe | What it is | Keywords |
|---|---|---|
| `wood` | stretched grain, strong bump | wood, tree, timber, plank |
| `marble` | soft veins, polished | marble, stone |
| `stone` | chunky speckled rock | stone, rock, granite, concrete |
| `concrete` | flat rough surface | concrete, plaster, cement |
| `rust` | corroded metal | rust, corroded, oxide |
| `metal_brushed` | streaked metal | metal, brushed, steel, aluminium |
| `fabric` | soft woven cloth | fabric, cloth, textile, canvas |
| `leather` | pebbled surface | leather |
| `paper` | fine fibrous sheet | paper, cardboard |
| `snow` | soft bright drifts | snow, ice |
| `lava` | glowing cracks | lava, magma |
| `plastic` | smooth coloured plastic | plastic, toy |
| `checker` | chequerboard | checker, checkered |

A request that only says "wood" is ambiguous between `wood` and `wood_plank`:
ask which one, or pick `wood` and mention the assumption.

## The parameters

| Parameter | Meaning |
|---|---|
| `pattern` | `noise`, `voronoi`, `wave`, `checker`, `gradient` |
| `scale` | size of the pattern (bigger = finer) |
| `detail` | how much fine noise sits on top |
| `distortion` | smears the pattern - this is what makes marble veins |
| `stretch` | scales the pattern on two axes; `(1, 12)` is what turns noise into wood grain |
| `color_a` / `color_b` | the two ends of the gradient |
| `color_mid` | an optional third colour in the middle |
| `metallic` | 0 for dielectrics, 1 for metal |
| `roughness` | 0 mirror, 1 completely matte |
| `roughness_variation` | how much the roughness varies across the surface |
| `bump_strength` | how deep the relief is |
| `specular` | the gloss of the surface |
| `emission` / `emission_strength` | self-illumination |
| `transmission` / `ior` | see-through materials |

## Rules

1. **Use the tool.** `procedural.build` handles the graph; your job is to
   choose the recipe and the numbers.
2. **Two colours are not enough for most materials.** A third colour
   (`color_mid`) is what makes wood look like wood rather than like a
   gradient.
3. **Stretch is what makes a pattern read as a material.** Flat noise is
   "clouds"; stretched noise is "grain".
4. **Metal needs `metallic: 1`.** A metal with `metallic: 0` looks like grey
   plastic, and no amount of roughness tuning fixes it.
5. **Check the result in the viewport, not in your head.** Ask the user for a
   screenshot or describe what you set and let them judge.
6. **Procedural is not the end of the pipeline.** If the material is going
   into a game, it has to be baked - see the `material-baking` skill.

## What the graph looks like

The add-on builds this chain. Knowing it helps when a user asks you to change
something specific.

```python
import bpy

mat = bpy.data.materials.new("DemoProcedural")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links

bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")

coords = nodes.new("ShaderNodeTexCoord")
coords.location = (-1100, 0)

mapping = nodes.new("ShaderNodeMapping")
mapping.location = (-900, 0)
mapping.inputs["Scale"].default_value = (1.0, 12.0, 1.0)

noise = nodes.new("ShaderNodeTexNoise")
noise.location = (-700, 0)
noise.inputs["Scale"].default_value = 3.0
noise.inputs["Detail"].default_value = 6.0

ramp = nodes.new("ShaderNodeValToRGB")
ramp.location = (-450, 150)
ramp.color_ramp.elements[0].color = (0.09, 0.05, 0.02, 1.0)
ramp.color_ramp.elements[1].color = (0.42, 0.26, 0.13, 1.0)

bump = nodes.new("ShaderNodeBump")
bump.location = (-250, -350)
bump.inputs["Strength"].default_value = 0.35

links.new(coords.outputs["Object"], mapping.inputs["Vector"])
links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
links.new(noise.outputs["Fac"], bump.inputs["Height"])
links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

bsdf.inputs["Roughness"].default_value = 0.55
```

## Tuning an existing material

When the user has a material they like and wants one value changed, change the
value - do not rebuild the graph.

```python
import bpy

mat = bpy.data.materials.get("Wood_2k")
if mat is not None:
    bsdf = next(
        (n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None
    )
    if bsdf is not None:
        bsdf.inputs["Roughness"].default_value = 0.75
        # The colour space of a data map is the classic silent bug.
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                if node.image.name.lower().find("rough") >= 0:
                    node.image.colorspace_settings.name = "Non-Color"
```
