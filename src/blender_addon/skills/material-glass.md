---
name: material-glass
title: Glass and transparent materials
description: Transmission Weight, IOR, roughness and the EEVEE settings a transparent surface needs
keywords: glass, transparent, transparency, transmission, ior, refraction, window, стекло, прозрачность, прозрачный, стеклянный
blender: ">=4.2"
---

# Recipe (solid glass)
1. `Base Color` stays white `(1, 1, 1, 1)`.
2. `Roughness` 0.0 for clear glass, 0.02-0.1 for frosted.
3. `IOR` 1.45 (window glass), 1.33 (water), 1.52 (crown glass).
4. `Transmission Weight` = 1.0. In Blender 4.0+ the socket was renamed from
   `Transmission`; use a fallback lookup.
5. Leave `Alpha` at 1.0 — transmission, not alpha, makes it see-through.
6. EEVEE needs `material.surface_render_method = "BLENDED"` and often
   `use_backface_culling = True`, otherwise the glass renders opaque or dark.
7. Cycles needs nothing extra.

```python
def socket(node, *names):
    for name in names:
        found = node.inputs.get(name)
        if found is not None:
            return found
    return None


mat = bpy.data.materials.new("Glass")
mat.use_nodes = True
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

socket(bsdf, "Base Color").default_value = (1.0, 1.0, 1.0, 1.0)
socket(bsdf, "Roughness").default_value = 0.02
socket(bsdf, "IOR").default_value = 1.45
# "Transmission" for Blender 2.8-3.x, "Transmission Weight" for 4.0+.
socket(bsdf, "Transmission Weight", "Transmission").default_value = 1.0

if hasattr(mat, "surface_render_method"):
    mat.surface_render_method = "BLENDED"  # EEVEE: needed for real transparency
if hasattr(mat, "use_backface_culling"):
    mat.use_backface_culling = True

obj = bpy.context.active_object
if obj is not None and obj.type == "MESH":
    obj.data.materials.append(mat)
```

# Thin glass (a window pane, a lens cover)
Enable `Thin Wall` on the Principled BSDF (socket `Thin Wall`) so a single-sided
surface refracts like a thin sheet instead of a solid block.

# Pitfalls
- In EEVEE a transmissive surface still casts a shadow; lower
  `Material > Shadow > ...` or switch the object's ray visibility off if the
  shadow looks wrong.
- Behind a glass surface you need something to look at — a flat grey world
  makes glass look like nothing at all.
- `Transmission` raises `KeyError` on Blender 4.0+; `Transmission Weight`
  raises it on 3.x. The fallback lookup above is version proof.
