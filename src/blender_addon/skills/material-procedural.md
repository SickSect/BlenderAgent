---
name: material-procedural
title: Procedural materials
description: Build and tune procedural materials - the pattern choice, the recipe catalogue, the parameters, and when to bake instead
keywords: procedural, noise, voronoi, wave, brick, tile, masonry, wood, marble, rust, fabric, leather, snow, lava, procedural material, процедурный, шум, процедурный материал, процедурка, кирпич, кладка, плитка
blender: ">=4.2"
---

# Procedural materials

A procedural material is a **node graph**, not a picture:

```text
Texture Coordinate -> Mapping (stretch) -> Texture (noise/voronoi/wave/brick)
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

## Rule 1: noise is fog, a surface needs a structure

This is the mistake that makes procedural materials look wrong. A **noise
texture with a colour ramp is clouds, smoke, dust and marble veins** - never a
brick wall, a tiled floor or a wooden plank. If the user asks for a *surface*,
the pattern has to carry a **structure**: bricks, tiles, cells, waves, grain.

| The user asks for | Pattern | Why |
|---|---|---|
| brick, masonry, wall, кирпич, кладка | `brick` | rows of bricks with mortar joints |
| tile, floor, плитка, кафель | `brick` (square: `brick_width` = `row_height`) | a grid of tiles with grout |
| wood, plank, дерево, доска | `noise` + `stretch` (1, 12) | stretched noise reads as grain |
| marble, мрамор | `noise` + high `distortion` | distorted noise reads as veins |
| granite, stone, rock, камень | `voronoi` | cells read as chips of rock |
| rust, corrosion, ржавчина | `voronoi` + `metallic` | patches of oxide |
| leather, кожа | `voronoi`, small scale | pebbles |
| fabric, cloth, ткань | `wave` | threads |
| snow, fog, smoke, снег, дым | `noise` | noise is exactly right here |
| checker, шахматная доска | `checker` | squares |

So: **"make me a brick material" is `pattern=brick`, never `pattern=noise`.**
A noise-only material is white smoke, whatever the colours are.

## Rule 2: a material is structure + colour + surface

Before you finish, the material must have all three:

1. **structure** - the pattern carries the shape of the thing (bricks, cells,
   grain, threads);
2. **colour variation** - at least two tones, and for a surface like brick a
   *per-brick* variation (`color_mid` for the brick pattern);
3. **surface** - roughness and bump. Without bump a procedural material looks
   printed on, not carved into.

## Prefer the add-on's tools over hand-built graphs

The add-on assembles the graph from a **specification** - plain numbers - so
the result is always valid and always the same:

| Tool | What it does |
|---|---|
| `procedural.list` | the ready-made recipes with their pattern and keywords |
| `procedural.build` | build a material from a recipe plus overrides, or from explicit parameters |

Call `procedural.list` when you are unsure which material fits, then build with
`procedural.build`. Do **not** write the node graph by hand for a standard
material: you will get it subtly wrong, and it will not match the other
materials of the project.

```text
procedural.build {"recipe": "brick", "name": "RedBrick",
                  "color_a": "#8c3a22", "color_b": "#b9b2a6",
                  "roughness": 0.8, "bump_strength": 0.5}
```

Note the JSON: a **list** `["scale", 12]`, never a Python tuple `(12,)`.

## The recipes

| Recipe | Pattern | What it is | Keywords |
|---|---|---|---|
| `wood` | noise + stretch | stretched grain, strong bump | wood, tree, timber, plank |
| `marble` | noise + distortion | soft veins, polished | marble, vein |
| `stone` | voronoi | chunky speckled rock | stone, rock, granite |
| `concrete` | noise | flat rough surface | concrete, plaster, cement |
| `rust` | voronoi | corroded metal | rust, corroded, oxide |
| `metal_brushed` | noise + stretch | streaked metal | metal, brushed, steel |
| `fabric` | wave | soft woven cloth | fabric, cloth, textile |
| `leather` | voronoi | pebbled surface | leather |
| `paper` | noise | fine fibrous sheet | paper, cardboard |
| `snow` | noise | soft bright drifts | snow, ice |
| `lava` | voronoi | glowing cracks | lava, magma |
| `plastic` | noise | smooth coloured plastic | plastic, toy |
| `checker` | checker | chequerboard | checker, checkered |
| `brick` | brick | brick wall with mortar joints | brick, кирпич, кладка, wall |
| `tile` | brick (square) | tiles with thin grout | tile, плитка, кафель |
| `cobble` | brick | rounded stones, deep joints | cobble, булыжник, мостовая |

If the recipe you need is not in the list, **do not fall back to noise**.
Build it from parameters with the right pattern.

## The brick pattern

`pattern=brick` uses Blender's brick texture: rows of bricks, every second row
shifted by half, with mortar joints between them.

**The colours mean something different here** than in the other patterns:

| Parameter | Meaning for the brick pattern |
|---|---|
| `color_a` | the brick |
| `color_mid` | the second brick tone - the per-brick variation |
| `color_b` | the mortar |
| `mortar_size` | the width of the joint (0.02 = thin, 0.06 = wide) |
| `mortar_smooth` | how soft the joint is (0 = sharp) |
| `brick_width` | how wide a brick is (0.5 = two bricks per unit) |
| `row_height` | how tall a row is (0.25 = four rows per unit) |
| `brick_bias` | shifts the pattern sideways, for a less regular wall |
| `scale` | how many times the whole wall repeats |

The graph the add-on builds for a brick: the brick texture gives the colour
(bricks with per-brick variation + mortar), its **factor** (1 in the mortar,
0 in the brick) makes the **mortar rougher** and, inverted, makes the joints
**recessed** in the bump.

```text
procedural.build {"recipe": "brick", "name": "OldBrick",
                  "color_a": "#7a3b26", "color_mid": "#8f4a30",
                  "color_b": "#c2bcb0", "mortar_size": 0.025,
                  "brick_width": 0.5, "row_height": 0.22,
                  "roughness": 0.8, "bump_strength": 0.55}
```

For a tile floor: `brick_width` = `row_height` (0.5 and 0.5), `mortar_size`
small (0.008), roughness low (0.25) - tiles are glazed.

## The parameters

| Parameter | Meaning |
|---|---|
| `pattern` | `noise`, `voronoi`, `wave`, `checker`, `gradient`, `brick` |
| `scale` | size of the pattern (bigger = finer) |
| `detail` | how much fine noise sits on top |
| `distortion` | smears the pattern - this is what makes marble veins |
| `stretch` | scales the pattern on two axes; `(1, 12)` is what turns noise into wood grain |
| `color_a` / `color_b` | the two ends of the gradient (for `brick`: the brick and the mortar) |
| `color_mid` | an optional third colour in the middle (for `brick`: the second brick tone) |
| `metallic` | 0 for dielectrics, 1 for metal |
| `roughness` | 0 mirror, 1 completely matte |
| `roughness_variation` | how much the roughness varies across the surface |
| `bump_strength` | how deep the relief is |
| `specular` | the gloss of the surface |
| `emission` / `emission_strength` | self-illumination |
| `transmission` / `ior` | see-through materials |

## Rules

1. **Use the tool.** `procedural.build` handles the graph; your job is to
   choose the recipe, the pattern and the numbers.
2. **Three colours are not enough for most materials.** A third colour
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

bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")

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

A brick wall is the same idea with the brick node instead of the noise:

```python
import bpy

mat = bpy.data.materials.new("DemoBrick")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links

bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")

coords = nodes.new("ShaderNodeTexCoord")
mapping = nodes.new("ShaderNodeMapping")
mapping.inputs["Scale"].default_value = (6.0, 6.0, 6.0)

brick = nodes.new("ShaderNodeTexBrick")
brick.inputs["Color1"].default_value = (0.42, 0.16, 0.10, 1.0)
brick.inputs["Color2"].default_value = (0.55, 0.24, 0.15, 1.0)
brick.inputs["Mortar"].default_value = (0.62, 0.60, 0.56, 1.0)
brick.inputs["Scale"].default_value = 6.0
brick.inputs["Mortar Size"].default_value = 0.02
brick.inputs["Brick Width"].default_value = 0.5
brick.inputs["Row Height"].default_value = 0.25

links.new(coords.outputs["Object"], mapping.inputs["Vector"])
links.new(mapping.outputs["Vector"], brick.inputs["Vector"])
links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])

# The factor of the brick node is 1 in the mortar: the mortar is rougher...
rough = nodes.new("ShaderNodeMapRange")
rough.inputs["To Min"].default_value = 0.70
rough.inputs["To Max"].default_value = 0.95
links.new(brick.outputs["Factor"], rough.inputs["Value"])
links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

# ...and, inverted, it is the height: the joints sit lower than the bricks.
height = nodes.new("ShaderNodeMapRange")
height.inputs["To Min"].default_value = 1.0
height.inputs["To Max"].default_value = 0.0
links.new(brick.outputs["Factor"], height.inputs["Value"])

bump = nodes.new("ShaderNodeBump")
bump.inputs["Strength"].default_value = 0.5
links.new(height.outputs["Result"], bump.inputs["Height"])
links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
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
