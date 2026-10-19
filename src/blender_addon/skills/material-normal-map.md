---
name: material-normal-map
title: Normal maps done right
description: Normal Map node, Non-Color space, strength and the usual DirectX/OpenGL trap
keywords: normal map, normal, bump, height, tangent, strength, opengl, directx, детализация, нормали, рельеф
blender: ">=4.2"
---

# The chain
`Image Texture` -> `Normal Map` node -> Principled BSDF `Normal` input.

Three things must hold:
1. the image is `Non-Color` (it stores a vector, not a colour);
2. it goes through a `ShaderNodeNormalMap` node, never straight into `Normal`;
3. the mesh has a UV layer.

# Strength
`Normal Map > Strength`: 1.0 is the authored intensity, 0.3-0.7 softens
fabric-like detail, 2.0+ exaggerates it. Strength 0.0 disables the map without
unlinking it.

```python
mat = bpy.data.materials.new("Normal_Map_Demo")
mat.use_nodes = True
tree = mat.node_tree
nodes, links = tree.nodes, tree.links
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")

tex = nodes.new("ShaderNodeTexImage")
# Real work: tex.image = bpy.data.images["brick_normal"]  (loaded or packed)
tex.image = bpy.data.images.new("normal", 8, 8)
tex.image.colorspace_settings.name = "Non-Color"

normal_map = nodes.new("ShaderNodeNormalMap")
normal_map.space = "TANGENT"  # "OBJECT" only for object-space normal maps
strength = normal_map.inputs.get("Strength")
if strength is not None:
    strength.default_value = 1.0

links.new(tex.outputs["Color"], normal_map.inputs["Color"])
links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# DirectX vs OpenGL
Most game assets (Unreal, Unity, DirectX) store the green channel flipped
relative to Blender's OpenGL convention. Symptom: lighting comes from the wrong
side — dents look like bumps. Fix it by inverting green before the Normal Map
node: `Separate Color` -> `Invert` on `Green` -> `Combine Color` -> `Normal Map`.

# Bump instead of normal
When only a greyscale height map exists, use `ShaderNodeBump`:
`Height` (from the `Non-Color` texture) + `Strength` + `Distance`.
Bump is computed from the height field and is cheaper but less precise than a
real normal map.

# Pitfalls
- A normal map in `sRGB` produces washed out, flat-looking detail.
- Blender 4.x lights tangent-space maps by default; an object-space map needs
  `normal_map.space = "OBJECT"` **and** an unused transform.
- If the surface explodes into black patches, the Blue channel was probably
  damaged (JPEG compression) — re-export as PNG.
