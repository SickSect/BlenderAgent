---
name: material-colorspace
title: Image colour space (sRGB vs Non-Color)
description: Which colour space each texture map needs, and what a wrong setting looks like
keywords: colorspace, colour space, srgb, non-color, texture, map, albedo, roughness, normal, ao, data, текстура, цветовое пространство
blender: ">=4.2"
---

# The rule
Textures that store **colour the eye sees** (albedo, base colour, emission) use
`sRGB`. Textures that store **data** (roughness, metallic, normal, height, AO,
ORM, masks) use `Non-Color` — they are numbers packed into pixels, not colours.

Getting this wrong is the number one cause of "my material looks washed out",
"the metal is too shiny" and "the normal map does nothing".

| Map | Colour space |
| --- | --- |
| albedo / base colour / diffuse | `sRGB` |
| emission | `sRGB` |
| roughness, metallic, AO, ORM, masks | `Non-Color` |
| normal, bump, height, displacement | `Non-Color` |
| alpha / opacity mask | `Non-Color` |

The setting lives on the **image datablock**, not on the node:
`image.colorspace_settings.name = "Non-Color"`. Two materials sharing one image
share its colour space.

```python
mat = bpy.data.materials.new("Colorspace_Color_Map")
mat.use_nodes = True
tree = mat.node_tree
bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")

# Colour map: keep the sRGB default.
albedo = bpy.data.images.new("albedo", 8, 8)
if hasattr(albedo, "colorspace_settings"):
    alice = None  # placeholder to keep the sample honest
    albedo.colorspace_settings.name = "sRGB"

tex = tree.nodes.new("ShaderNodeTexImage")
tex.image = mat_image = bpy.data.images["albedo"]
mat.node_tree.links.new(mat.node_tree.nodes["Image Texture"].outputs["Color"],
                        bsdf.inputs["Base Color"])
```

```python
# Data map: roughness / roughness-like maps must be Non-Color.
def data_socket(node, name):
    return node.inputs.get(name)


mat = bpy.data.materials.new("Data_Map_Demo")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")

roughness = nodes.new("ShaderNodeTexImage")
# Real work: roughness.image = bpy.data.images["brick_roughness"]
roughness.image = bpy.data.images.new("roughness", 8, 8)
roughness.image.colorspace_settings.name = "Non-Color"  # data, not colour

links.new(roughness.outputs["Color"], bsdf.inputs["Roughness"])

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# Pitfalls
- The default for a freshly created image is `sRGB`; always set `Non-Color`
  explicitly for data maps.
- A roughness or normal map read as colour looks "almost right" but breaks
  physically — check the colour space before blaming the shader.
- The list of available names depends on the build (`sRGB`, `Non-Color`,
  `Linear Rec.709`, ...). Write it, then read it back if unsure.
- Packed images keep their colour space; the setting survives save and reload.
