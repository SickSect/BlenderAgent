---
name: material-metal-roughness
title: Metal and roughness workflow
description: Metallic workflow values, roughness ranges and why metal renders black without an environment
keywords: metal, metallic, roughness, steel, iron, gold, chrome, brushed, polished, scratched, металл, металлический, шероховатость
blender: ">=4.2"
---

# Values
| Surface | Metallic | Roughness | Base Color |
| --- | --- | --- | --- |
| Polished steel | 1.0 | 0.05-0.15 | (0.56, 0.57, 0.58, 1.0) |
| Brushed steel | 1.0 | 0.30-0.45 | (0.56, 0.57, 0.58, 1.0) |
| Cast iron | 1.0 | 0.60-0.80 | (0.38, 0.38, 0.40, 1.0) |
| Gold | 1.0 | 0.10-0.25 | (1.00, 0.77, 0.34, 1.0) |
| Aluminium | 1.0 | 0.20-0.40 | (0.91, 0.92, 0.92, 1.0) |
| Painted plastic / wood / skin | 0.0 | 0.30-0.70 | the actual colour |

# Rules
1. `Metallic` is a **binary** property in practice: 0 for dielectrics, 1 for
   metals. Intermediate values only make sense between two metals or as a mask.
2. For a metal, only `Base Color` and `Roughness` carry the look.
   `Specular IOR Level` does nothing once `Metallic = 1`.
3. Metal has almost no diffuse reflection: in a scene without a light or an
   environment it renders **black**. If the user reports "black metal", check
   the world/lighting first — the material is usually fine.
4. Roughness maps belong in `Non-Color` colour space.

```python
mat = bpy.data.materials.new("Brushed_Steel")
mat.use_nodes = True
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def socket(node, name, *fallbacks):
    for candidate in (name,) + fallbacks:
        found = node.inputs.get(candidate)
        if found is not None:
            return found
    return None


socket(bsdf, "Base Color").default_value = (0.56, 0.57, 0.58, 1.0)
socket(bsdf, "Metallic").default_value = 1.0
socket(bsdf, "Roughness").default_value = 0.35

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# Pitfalls
- Writing values above 1.0 is silently clamped by the shader; Python will not
  warn you.
- A roughness of 0.0 on metal gives a mirror that only reflects the world
  colour — add an HDRI or a few area lights.
- Anisotropic streaks (brushed metal) need `Anisotropic` plus a
  `Tangent` input; without a tangent map the effect has no direction.
- Scratches look best as a roughness mask multiplied into the roughness map,
  not as a separate material.
