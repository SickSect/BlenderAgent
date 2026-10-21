"""
Procedural rusty painted metal for Blender 3.4+ / 4.x.

Layers (bottom to top):
  - bare steel
  - rust (large patches + fine pitting, more rust inside dents)
  - worn paint with chipped edges (chips appear mostly where rust is)
  - scratches that expose bright bare metal

Two chained Bump nodes produce a strong, readable normal map:
  1. large-scale dents
  2. fine detail (paint thickness, rust grain, scratches)

Usage: open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a plane is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# Parameters (tweak these)
# ----------------------------------------------------------------------
MATERIAL_NAME = "Procedural_Rusty_Metal"
PLANE_SIZE = 2.0          # meters, only used when a plane is created
GLOBAL_SCALE = 1.0        # scales every pattern at once

DENT_SCALE = 3.0          # smaller value = bigger dents
DENT_DENSITY = 0.35       # fraction of Voronoi cells that become dents
DENT_DEPTH = 0.05         # Bump distance for dents

RUST_SCALE = 3.5          # size of rust patches
PAINT_THRESHOLD = 0.55    # higher = more paint chipped away
CHIP_SCALE = 7.0          # size of paint chips

# (rotation in degrees, stretch along Y, line width, coverage noise scale)
SCRATCH_LAYERS = [
    (0.0, 14.0, 0.040, 3.0),
    (60.0, 20.0, 0.040, 4.0),
    (125.0, 28.0, 0.050, 5.0),
]

DETAIL_STRENGTH = 1.0
DENT_STRENGTH = 1.0

# Colors (linear RGBA)
BARE_STEEL = (0.45, 0.45, 0.47, 1.0)
SCRATCH_METAL = (0.62, 0.62, 0.64, 1.0)
PAINT = (0.08, 0.16, 0.22, 1.0)
PAINT_GRIME = (0.02, 0.04, 0.05, 1.0)
RUST_BLEED = (0.20, 0.08, 0.04, 1.0)
RUST_STOPS = [
    (0.0, (0.10, 0.03, 0.010, 1.0)),
    (0.5, (0.30, 0.09, 0.025, 1.0)),
    (1.0, (0.50, 0.18, 0.050, 1.0)),
]


# ----------------------------------------------------------------------
# Small helper wrapper around the node tree
# ----------------------------------------------------------------------
class Builder:
    """Creates nodes and returns their output sockets (layout is automatic)."""

    def __init__(self, tree):
        self.nodes = tree.nodes
        self.links = tree.links
        self.count = 0

    def _new(self, node_type):
        node = self.nodes.new(node_type)
        col, row = divmod(self.count, 6)
        node.location = (-2600 + col * 260, 900 - row * 280)
        self.count += 1
        return node

    def _feed(self, socket, value):
        """Link a socket or set a constant value."""
        if value is None:
            return
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, socket)
        else:
            socket.default_value = value

    # --- scalar math ---------------------------------------------------
    def op(self, operation, a, b=None, c=None, clamp=False):
        node = self._new("ShaderNodeMath")
        node.operation = operation
        node.use_clamp = clamp
        self._feed(node.inputs[0], a)
        self._feed(node.inputs[1], b)
        self._feed(node.inputs[2], c)
        return node.outputs[0]

    def vop(self, operation, a, b=None, c=None):
        node = self._new("ShaderNodeVectorMath")
        node.operation = operation
        self._feed(node.inputs[0], a)
        self._feed(node.inputs[1], b)
        self._feed(node.inputs[2], c)
        return node.outputs[0]

    def remap(self, value, from_min, from_max, to_min, to_max, smooth=False):
        node = self._new("ShaderNodeMapRange")
        node.interpolation_type = "SMOOTHSTEP" if smooth else "LINEAR"
        node.clamp = True
        self._feed(node.inputs["Value"], value)
        self._feed(node.inputs["From Min"], from_min)
        self._feed(node.inputs["From Max"], from_max)
        self._feed(node.inputs["To Min"], to_min)
        self._feed(node.inputs["To Max"], to_max)
        return node.outputs["Result"]

    # --- mixing ----------------------------------------------------------
    def mixf(self, a, b, fac):
        node = self._new("ShaderNodeMix")
        node.data_type = "FLOAT"
        self._feed(node.inputs[0], fac)
        self._feed(node.inputs[2], a)
        self._feed(node.inputs[3], b)
        return node.outputs[0]

    def mixc(self, a, b, fac, blend="MIX"):
        node = self._new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = blend
        self._feed(node.inputs[0], fac)
        self._feed(node.inputs[6], a)
        self._feed(node.inputs[7], b)
        return node.outputs[2]

    # --- textures --------------------------------------------------------
    def mapping(self, vector, scale=(1, 1, 1), rot_deg=(0, 0, 0)):
        node = self._new("ShaderNodeMapping")
        node.vector_type = "POINT"
        self._feed(node.inputs["Vector"], vector)
        node.inputs["Scale"].default_value = scale
        node.inputs["Rotation"].default_value = tuple(math.radians(r) for r in rot_deg)
        return node.outputs["Vector"]

    def noise(self, vector, scale, detail=5.0, rough=0.5, distortion=0.0):
        node = self._new("ShaderNodeTexNoise")
        node.noise_dimensions = "3D"
        self._feed(node.inputs["Vector"], vector)
        node.inputs["Scale"].default_value = scale
        node.inputs["Detail"].default_value = detail
        node.inputs["Roughness"].default_value = rough
        node.inputs["Distortion"].default_value = distortion
        return node

    def voronoi(self, vector, scale, randomness=1.0):
        node = self._new("ShaderNodeTexVoronoi")
        node.voronoi_dimensions = "3D"
        node.feature = "F1"
        self._feed(node.inputs["Vector"], vector)
        node.inputs["Scale"].default_value = scale
        node.inputs["Randomness"].default_value = randomness
        return node

    def sepcol(self, color):
        node = self._new("ShaderNodeSeparateColor")
        self._feed(node.inputs["Color"], color)
        return node

    def ramp(self, fac, stops):
        node = self._new("ShaderNodeValToRGB")
        els = node.color_ramp.elements
        els[0].position, els[0].color = stops[0][0], stops[0][1]
        els[1].position, els[1].color = stops[-1][0], stops[-1][1]
        for pos, col in stops[1:-1]:
            els.new(pos).color = col
        self._feed(node.inputs["Fac"], fac)
        return node.outputs["Color"]


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def scratch_layer(b, pos, rot, stretch, width, cover_scale):
    """Thin, stretched noise contours masked by a low-frequency coverage noise."""
    stretched = b.mapping(pos, scale=(1.5, stretch, 1.5), rot_deg=(0, 0, rot))
    n = b.noise(stretched, 1.0, detail=3.0, rough=0.5).outputs["Fac"]
    line = b.op("ABSOLUTE", b.op("SUBTRACT", n, 0.5))
    line = b.remap(line, 0.0, width, 1.0, 0.0)
    cover = b.noise(pos, cover_scale, detail=3.0).outputs["Fac"]
    cover = b.remap(cover, 0.50, 0.65, 0.0, 1.0)
    return b.op("MULTIPLY", line, cover)


def build_material(name=MATERIAL_NAME):
    old = bpy.data.materials.get(name)
    if old:
        bpy.data.materials.remove(old)

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    b = Builder(tree)

    # --- Coordinates -----------------------------------------------------
    tex_coord = b._new("ShaderNodeTexCoord")
    pos = b.mapping(tex_coord.outputs["Object"],
                    scale=(GLOBAL_SCALE, GLOBAL_SCALE, GLOBAL_SCALE))

    # --- Dents: sparse Voronoi bowls with warped coordinates --------------
    warp = b.noise(pos, 2.0, detail=2.0).outputs["Color"]
    warped = b.vop("MULTIPLY_ADD", warp, (0.35, 0.35, 0.35), pos)
    vor = b.voronoi(warped, DENT_SCALE)
    cell = b.sepcol(vor.outputs["Color"])
    cell_on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - DENT_DENSITY)
    size_var = b.op("MULTIPLY_ADD", cell.outputs["Green"], 0.5, 0.5)
    bowl = b.remap(vor.outputs["Distance"], 0.0, 0.55, 1.0, 0.0, smooth=True)
    dent = b.op("MULTIPLY", b.op("MULTIPLY", bowl, cell_on), size_var)
    height_large = b.op("SUBTRACT", 1.0, dent)

    # --- Rust: large patches, extra rust inside dents ----------------------
    rust_noise = b.noise(pos, RUST_SCALE, detail=8.0, rough=0.6, distortion=0.3)
    rust_base = b.remap(rust_noise.outputs["Fac"], 0.40, 0.62, 0.0, 1.0, smooth=True)
    rust_mask = b.op("ADD", rust_base, b.op("MULTIPLY", dent, 0.6), clamp=True)

    rust_var = b.noise(pos, 9.0, detail=6.0).outputs["Fac"]
    rust_col = b.ramp(rust_var, RUST_STOPS)
    rust_grain = b.noise(pos, 70.0, detail=12.0, rough=0.7).outputs["Fac"]

    # --- Paint with chipped edges -------------------------------------------
    chip_n = b.noise(pos, CHIP_SCALE, detail=10.0, rough=0.65, distortion=0.5)
    chip_val = b.op("MULTIPLY_ADD", rust_grain, 0.18, chip_n.outputs["Fac"])
    chip_val = b.op("SUBTRACT", chip_val, b.op("MULTIPLY", rust_mask, 0.12))
    paint_mask = b.remap(chip_val, PAINT_THRESHOLD, PAINT_THRESHOLD + 0.03,
                         0.0, 1.0, smooth=True)

    grime = b.noise(pos, 4.0, detail=6.0).outputs["Fac"]
    grime = b.remap(grime, 0.35, 0.70, 0.0, 0.6)
    paint_col = b.mixc(PAINT, PAINT_GRIME, grime)
    paint_col = b.mixc(paint_col, RUST_BLEED, b.op("MULTIPLY", rust_mask, 0.4))

    # --- Scratches ---------------------------------------------------------
    layers = [scratch_layer(b, pos, *params) for params in SCRATCH_LAYERS]
    scratches = layers[0]
    for extra in layers[1:]:
        scratches = b.op("MAXIMUM", scratches, extra)

    # --- Height for the fine bump -------------------------------------------
    paint_h = b.op("MULTIPLY", paint_mask, 0.30)
    rust_h = b.op("MULTIPLY", b.op("MULTIPLY", rust_grain, rust_mask), 0.15)
    rust_h = b.op("MULTIPLY", rust_h, b.op("SUBTRACT", 1.0, paint_mask))
    scratch_h = b.op("MULTIPLY", scratches, -0.10)
    grain_h = b.op("MULTIPLY", rust_grain, 0.02)
    height_detail = b.op("ADD", b.op("ADD", paint_h, rust_h),
                         b.op("ADD", scratch_h, grain_h))

    # --- Normals: two chained bumps ------------------------------------------
    bump_large = b._new("ShaderNodeBump")
    bump_large.inputs["Strength"].default_value = DENT_STRENGTH
    bump_large.inputs["Distance"].default_value = DENT_DEPTH
    b._feed(bump_large.inputs["Height"], height_large)

    bump_detail = b._new("ShaderNodeBump")
    bump_detail.inputs["Strength"].default_value = DETAIL_STRENGTH
    bump_detail.inputs["Distance"].default_value = 0.01
    b._feed(bump_detail.inputs["Height"], height_detail)
    b._feed(bump_detail.inputs["Normal"], bump_large.outputs["Normal"])

    # --- Base color ------------------------------------------------------------
    under = b.mixc(BARE_STEEL, rust_col, rust_mask)
    after_paint = b.mixc(under, paint_col, paint_mask)
    base_color = b.mixc(after_paint, SCRATCH_METAL, scratches)

    # --- Metallic ----------------------------------------------------------------
    metal_under = b.op("SUBTRACT", 1.0, b.op("MULTIPLY", rust_mask, 0.85))
    metal_paint = b.mixf(metal_under, 0.0, paint_mask)
    metallic = b.mixf(metal_paint, 1.0, scratches)

    # --- Roughness ----------------------------------------------------------------
    rust_rough = b.mixf(0.70, 0.95, rust_grain)
    rough_under = b.mixf(0.32, rust_rough, rust_mask)
    rough_paint = b.mixf(rough_under, 0.55, paint_mask)
    roughness = b.mixf(rough_paint, 0.28, scratches)

    # --- Shader and output -------------------------------------------------------
    bsdf = b._new("ShaderNodeBsdfPrincipled")
    b._feed(bsdf.inputs["Base Color"], base_color)
    b._feed(bsdf.inputs["Metallic"], metallic)
    b._feed(bsdf.inputs["Roughness"], roughness)
    b._feed(bsdf.inputs["Normal"], bump_detail.outputs["Normal"])

    out = b._new("ShaderNodeOutputMaterial")
    b._feed(out.inputs["Surface"], bsdf.outputs["BSDF"])
    return mat


def assign_material(mat):
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        bpy.ops.mesh.primitive_plane_add(size=PLANE_SIZE)
        obj = bpy.context.active_object

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


if __name__ == "__main__":
    material = build_material()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")