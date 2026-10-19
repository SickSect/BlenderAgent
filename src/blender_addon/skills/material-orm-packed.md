---
name: material-orm-packed
title: Unpack ORM / packed texture maps
description: Split R=AO, G=Roughness, B=Metallic and wire each channel into the shader
keywords: orm, packed, ao, ambient occlusion, occlusion, rma, mrao, separate color, channels, распаковка, каналы, затенение
blender: ">=4.2"
---

# What a packed map is
One image holding three greyscale maps to save memory and draw calls. The
common packing is **ORM**:

| Channel | Data |
| --- | --- |
| Red | Ambient Occlusion |
| Green | Roughness |
| Blue | Metallic |

Other engines swap it (RMA, MRAO, "cathedral" packs). **Check the file name or
ask** — the wrong order gives a material that looks plausible and is wrong.

# How to split
`ShaderNodeSeparateColor` gives `Red`, `Green`, `Blue` outputs. The image must
be `Non-Color` first, otherwise Blender converts the packed values as if they
were colour.

```python
mat = bpy.data.materials.new("ORM_Demo")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")

orm = nodes.new("ShaderNodeTexImage")
# Real work: orm.image = bpy.data.images["brick_orm"]
orm.image = bpy.data.images.new("orm", 8, 8)
orm.image.colorspace_settings.name = "Non-Color"

split = nodes.new("ShaderNodeSeparateColor")
links.new(orm.outputs["Color"], split.inputs["Color"])
links.new(split.outputs["Green"], bsdf.inputs["Roughness"])
links.new(split.outputs["Blue"], bsdf.inputs["Metallic"])

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# AO is not a socket
The Principled BSDF has no "Ambient Occlusion" input. AO is normally multiplied
into the base colour, which darkens crevices in the texture itself:

```python
mat = bpy.data.materials.new("ORM_AO_Demo")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")

orm = nodes.new("ShaderNodeTexImage")
orm.image = bpy.data.images.new("orm", 8, 8)
orm.image.colorspace_settings.name = "Non-Color"
split = nodes.new("ShaderNodeSeparateColor")
links.new(orm.outputs["Color"], split.inputs["Color"])

albedo = nodes.new("ShaderNodeRGB")
albedo.outputs[0].default_value = (0.80, 0.60, 0.40, 1.0)

# ShaderNodeMix is typed: several sockets share the name "A"/"B". Filter by
# socket type instead of by name — the index order changes between versions.
mix = nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
mix.blend_type = "MULTIPLY"
factor = next(s for s in mix.inputs if s.name == "Factor" and s.type == "VALUE")
factor.default_value = 1.0
color_inputs = [s for s in mix.inputs if s.type == "RGBA"]
color_output = next(s for s in mix.outputs if s.type == "RGBA")

links.new(split.outputs["Red"], color_inputs[0])   # AO
links.new(nodes.new("ShaderNodeRGB").outputs[0], color_inputs[1])
links.new(color_output, bsdf.inputs["Base Color"])
```

(For a plain albedo texture, link the texture's `Color` output into
`color_inputs[1]` instead of the `RGB` node.)

# Pitfalls
- Splitting a map that is still `sRGB` shifts every channel.
- Metallic values above 0.0 on a scratch or dirt mask leak metal into a
  dielectric surface; keep the blue channel clean.
- `ShaderNodeSeparateRGB` (Blender 3.x) no longer exists — use
  `ShaderNodeSeparateColor`, which is not the same node with a new name but the
  same sockets: `Red`, `Green`, `Blue`.
- Reading a channel with `ShaderNodeMath > Multiply` is only needed when the AO
  has to be scaled; a plain multiply with 1.0 does nothing.
