---
name: material-pbr-maps
title: Build a PBR material from texture maps
description: Wire albedo / roughness / metallic / normal maps into Principled BSDF with correct colour spaces
keywords: pbr, texture, maps, albedo, base color, roughness, metallic, normal, image texture, uv, texturing, текстуры, карты, развёртка
blender: ">=4.2"
---

# Before writing code
If the user points at a **folder** of maps, do not build the tree by hand — the
add-on already has a tested path for it:

    bpy.ops.gaa.pbr_from_folder(filepath="/path/to/folder")

(panel "AI" -> "PBR from folder"). Use the manual recipe below only when the
images are already loaded or packed in the .blend, when the naming scheme is
unusual, or when the user asks for a specific change on top.

# Recipe
1. One `ShaderNodeTexImage` per map. Set `image`, then set
   `image.colorspace_settings.name`: `sRGB` for colour maps, `Non-Color` for
   data maps (roughness, metallic, normal).
2. Links: `Color` -> `Base Color`, `Color` -> `Roughness` (through the data
   channel), `Color` -> `Metallic`.
3. Normal map: `TexImage` -> `ShaderNodeNormalMap` -> `Normal`. Never plug a
   colour map straight into `Normal`.
4. Add a `ShaderNodeUVMap` only when the mesh has several UV layers and the
   user wants a specific one; otherwise the active UV layer is used.
5. For tiling add `ShaderNodeMapping` (Scale) between `TexCoord` and the image
   nodes.

```python
mat = bpy.data.materials.new("PBR_From_Maps")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")


def image_node(name, colorspace):
    node = nodes.new("ShaderNodeTexImage")
    node.name = node.label = name
    # Real work: node.image = bpy.data.images["albedo"]  (loaded or packed)
    node.image = bpy.data.images.new(name, 8, 8)
    node.image.colorspace_settings.name = colorspace
    return node


base = image_node("albedo", "sRGB")
rough = image_node("roughness", "Non-Color")
metal = image_node("metallic", "Non-Color")
normal = image_node("normal", "Non-Color")

links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
links.new(metal.outputs["Color"], bsdf.inputs["Metallic"])

normal_map = nodes.new("ShaderNodeNormalMap")
links.new(normal.outputs["Color"], normal_map.inputs["Color"])
links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# Pitfalls
- `Roughness` and `Metallic` expect a **single value**, so a colour socket is
  converted for you — but only the first channel of a colour map survives
  meaningfully. When a packed map is involved, split the channels first.
- A map plugged into `Roughness` while still in `sRGB` shifts every value; the
  result looks plausible but wrong.
- A normal map needs a UV layer and tangents; on a mesh without UVs the node
  does nothing.
- Greyscale maps are fine: link `Color`, not `Alpha`, unless the alpha channel
  actually carries the data.
