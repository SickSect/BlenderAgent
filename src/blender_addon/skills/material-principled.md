---
name: material-principled
title: Principled BSDF quick reference
description: Exact Principled BSDF input names in Blender 4.x/5.x plus a safe socket lookup
keywords: principled, bsdf, socket, base color, roughness, metallic, ior, specular, coat, sheen, subsurface, transmission, материал, сокет, шейдер
blender: ">=4.2"
---

# Goal

Give an object a material with the right inputs, using input names the running
Blender actually has.

# Input names (Blender 4.x / 5.x)

| Purpose | Input | Notes |
| --- | --- | --- |
| Diffuse colour | `Base Color` | RGBA tuple, e.g. `(0.6, 0.1, 0.1, 1.0)` |
| Metal mask | `Metallic` | 0 = dielectric, 1 = metal |
| Micro-roughness | `Roughness` | 0 = mirror, 1 = fully diffuse |
| Refraction index | `IOR` | glass 1.45, water 1.33 |
| Opacity | `Alpha` | needs a blended surface method in EEVEE |
| Light through the surface | `Transmission Weight` | was `Transmission` before 4.0 |
| Specular strength | `Specular IOR Level` | was `Specular` |
| Coat / clear coat | `Coat Weight` | was `Clearcoat` |
| Sheen | `Sheen Weight` | was `Sheen` |
| Glow colour | `Emission Color` | was `Emission` |
| Glow strength | `Emission Strength` | 0.0 by default |

Never use the removed names `Transmission`, `Specular`, `Clearcoat`, `Sheen`,
`Subsurface`, `Emission`: in Blender 4.0+ they raise `KeyError`.

# Rules
1. Find the shader node by **type**, not by label:
   `next(n for n in nodes if n.type == "BSDF_PRINCIPLED")`.
2. Look inputs up with a fallback list instead of hardcoding one name.
3. Assign the material with `obj.data.materials.append(mat)`; call
   `obj.data.materials.clear()` first when the object must end up with exactly
   one material.

```python
# Safe input access: never assume a socket exists (names changed in Blender 4.0).
def socket(node, *names):
    for name in names:
        found = node.inputs.get(name)
        if found is not None:
            return found
    return None

mat = bpy.data.materials.new("Reference_Matte")
mat.use_nodes = True
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

base = socket(bsdf, "Base Color")
if base is not None:
    base.default_value = (0.35, 0.08, 0.08, 1.0)

rough = socket(bsdf, "Roughness")
if rough is not None:
    rough.default_value = 0.45

metal = socket(bsdf, "Metallic")
if metal is not None:
    metal.default_value = 0.0

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# Pitfalls
- `bpy.data.materials["Name"]` raises `KeyError` when the name is absent — use
  `bpy.data.materials.get("Name")` and check for `None`.
- A material stays in the file as long as it has a user; an unassigned material
  disappears on save/reload.
- `mat.diffuse_color` is only the viewport/solid-shading colour; the rendered
  look comes from the Principled inputs.
