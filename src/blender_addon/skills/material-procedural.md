---
name: material-procedural
title: Procedural materials
description: Choose the pattern, the colours and the surface - the recipe catalogue and the parameters
keywords: procedural, noise, voronoi, wave, brick, tile, wood, marble, rust, fabric, leather, snow, lava, stone, concrete, paper, plastic, checker, процедурный, шум, процедурный материал, процедурка, кирпич, кладка, плитка, дерево, камень, мрамор, металл, ткань, кожа, бумага, бетон, ржавчина, пластик, снег, лава, штукатурка
blender: ">=4.2"
---

# Procedural materials

A procedural material is a **node graph**: coordinates -> mapping -> texture ->
colour ramp -> Principled BSDF, plus a bump from the same texture. It is
**infinite resolution** (no pixel grid) and **parametric** (change one number,
the whole surface follows). The price: a game engine cannot evaluate a noise
texture, so for a game the material has to be **baked** into pictures.

## Rule 1: noise is fog, a surface needs a structure

A **noise texture with a colour ramp is clouds, smoke, dust and marble veins**
- never a brick wall, a tiled floor or a plank. A surface needs a *structure*:
bricks, tiles, cells, waves, grain.

| The user asks for | Pattern | Why |
|---|---|---|
| brick, masonry, wall, кирпич, кладка | `brick` | rows of bricks with mortar joints |
| tile, floor, плитка, кафель | `brick` with `brick_width` = `row_height` | a grid of tiles with grout |
| wood, plank, дерево, доска | `noise` + `stretch` (1, 12) | stretched noise reads as grain |
| marble, мрамор | `noise` + high `distortion` | distorted noise reads as veins |
| granite, stone, rock, камень | `voronoi` | cells read as chips of rock |
| rust, corrosion, ржавчина | `voronoi` + `metallic` | patches of oxide |
| leather, кожа | `voronoi`, small scale | pebbles |
| fabric, cloth, ткань | `wave` | threads |
| snow, fog, smoke, снег, дым | `noise` | noise is exactly right here |
| checker, шахматная доска | `checker` | squares |

**"Make me a brick material" is `pattern=brick`, never `pattern=noise`.**
A noise-only material is white smoke, whatever the colours are.

## Rule 2: a material is structure + colour + surface

All three must be there before you finish: the **structure** (the pattern
carries the shape of the thing), the **colour variation** (at least two tones;
for a brick: `color_a` the brick, `color_mid` the second brick tone, `color_b`
the mortar) and the **surface** (roughness and bump - without a bump a
procedural material looks printed on, not carved into).

## Use the tools, do not write the graph

The add-on builds the graph from a **specification** - plain numbers - so the
result is always valid and always the same. `procedural.find` matches the
user's words against the recipe keywords, `procedural.list` shows the whole
catalogue with the pattern of each recipe, and `procedural.build` builds the
material. The catalogue is in the code, not in your memory: **never guess a
recipe name**.

```text
procedural.build {"recipe": "brick", "name": "RedBrick",
                  "color_a": "#8c3a22", "color_b": "#b9b2a6",
                  "roughness": 0.8, "bump_strength": 0.5}
```

The JSON is strict: a **list** `["scale", 12]`, never a Python tuple `(12,)`.

## The parameters

| Parameter | Meaning |
|---|---|
| `pattern` | `noise`, `voronoi`, `wave`, `checker`, `gradient`, `brick` |
| `scale` | size of the pattern (bigger = finer) |
| `detail` | how much fine noise sits on top |
| `distortion` | smears the pattern - this is what makes marble veins |
| `stretch` | scales the pattern on two axes; `(1, 12)` turns noise into wood grain |
| `color_a` / `color_b` | the two ends of the gradient (for `brick`: the brick and the mortar) |
| `color_mid` | an optional third colour (for `brick`: the second brick tone) |
| `metallic` | 0 for dielectrics, 1 for metal |
| `roughness` / `roughness_variation` | the gloss and how much it varies |
| `bump_strength` | how deep the relief is |
| `specular`, `emission`, `transmission`, `ior` | the rest of the surface |
| `mortar_size`, `brick_width`, `row_height`, `brick_bias` | the layout of a brick wall |

## The brick pattern

`pattern=brick` is Blender's brick texture: rows of bricks, every second row
shifted by half, with mortar joints. The node colours them itself, so there is
no colour ramp. Its **factor** is 1 in the mortar and 0 in the brick, which
makes the mortar **rougher** and, inverted, the joints **recessed**.

```python
import bpy

mat = bpy.data.materials.new("RedBrick")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links
bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")

coords = nodes.new("ShaderNodeTexCoord")
mapping = nodes.new("ShaderNodeMapping")
mapping.inputs["Scale"].default_value = (6.0, 6.0, 6.0)

brick = nodes.new("ShaderNodeTexBrick")
brick.inputs["Color1"].default_value = (0.42, 0.16, 0.10, 1.0)   # the brick
brick.inputs["Color2"].default_value = (0.55, 0.24, 0.15, 1.0)   # the second tone
brick.inputs["Mortar"].default_value = (0.62, 0.60, 0.56, 1.0)   # the mortar
brick.inputs["Scale"].default_value = 6.0
brick.inputs["Mortar Size"].default_value = 0.02
brick.inputs["Brick Width"].default_value = 0.5
brick.inputs["Row Height"].default_value = 0.25
links.new(coords.outputs["Object"], mapping.inputs["Vector"])
links.new(mapping.outputs["Vector"], brick.inputs["Vector"])
links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])

# The factor is 1 in the mortar: the mortar is rougher...
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

## Rules

1. **Use the tool.** `procedural.build` handles the graph; your job is to
   choose the recipe, the pattern and the numbers.
2. **Three colours are not enough for most materials.** A third colour
   (`color_mid`) is what makes wood look like wood rather than like a gradient.
3. **Metal needs `metallic: 1`.** A metal with `metallic: 0` looks like grey
   plastic, and no amount of roughness tuning fixes it.
4. **Check the result in the viewport, not in your head.** Ask the user for a
   screenshot or describe what you set and let them judge.
5. **Procedural is not the end of the pipeline.** For a game it has to be
   baked - see the `material-baking` skill.
