"""
Controllable brick wall for Blender 3.4+ / 4.x.

A running-bond brick wall built from math nodes (no Brick Texture node), so
every brick has its own random value and an exact distance to its edge. That
gives per-brick color, rounded or chipped edges and eroded mortar joints.
Everything is driven by the "SLIDERS" block below. Change a constant, run
the script again, and the material is rebuilt.

    BRICK_COLOR_HEX   main color of the bricks     ("#RRGGBB", sRGB)
    MORTAR_COLOR_HEX  color of the mortar joints   ("#RRGGBB", sRGB)
    WEAR              0 = new, crisp wall          1 = ruined wall:
                      chipped edges and corners, flaked-off surface, cracks,
                      eroded joints, faded color, salt stains, dirt streaks

Quick looks to try:
    new brickwork     : WEAR 0.0
    old house         : WEAR 0.4
    abandoned factory : WEAR 0.7
    ruin              : WEAR 1.0

The pattern lies in the object's XY plane, so use it on a plane (the preview
plane is 2 x 2 m, about 8 bricks wide).

Usage: open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a plane is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# SLIDERS (the values you will change most often)
# ----------------------------------------------------------------------
BRICK_COLOR_HEX = "#8C3823"    # main brick color, sRGB hex
MORTAR_COLOR_HEX = "#A8A296"   # mortar color, sRGB hex
WEAR = 0.4                     # 0..1  degree of wear

# ----------------------------------------------------------------------
# Fine tuning
# ----------------------------------------------------------------------
MATERIAL_NAME = "Brick_Controllable"
PLANE_SIZE = 2.0             # meters, only used when a plane is created
GLOBAL_SCALE = 1.0           # scales every pattern at once

BRICK_WIDTH = 0.25           # meters
BRICK_HEIGHT = 0.065         # meters
MORTAR_WIDTH = 0.010         # meters
EDGE_ROUND = 0.004           # edge rounding radius in meters (grows with wear)
BRICK_RELIEF = 0.012         # normal-map strength of joints and chips (Bump distance)
COLOR_VARIATION = 0.5        # 0..1  how different the bricks are from each other
DETAIL_STRENGTH = 1.0        # strength of the fine normal detail


# ----------------------------------------------------------------------
# Color helpers
# ----------------------------------------------------------------------
def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def hex_to_linear(hex_color):
    """'#RRGGBB' (sRGB) -> linear RGBA tuple, as Blender expects."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Color must look like '#RRGGBB', got {hex_color!r}")
    srgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def to_linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (to_linear(c) for c in srgb)
    return (r, g, b, 1.0)


def shade(color, factor):
    """Multiply the RGB of a linear color (factor < 1 darkens, > 1 lightens)."""
    return tuple(min(1.0, c * factor) for c in color[:3]) + (1.0,)


# ----------------------------------------------------------------------
# Node builder helper (layout is automatic)
# ----------------------------------------------------------------------
class Builder:
    """Creates nodes and returns their output sockets."""

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

    def mapping(self, vector, scale=(1, 1, 1), rot_deg=(0, 0, 0), loc=(0, 0, 0)):
        node = self._new("ShaderNodeMapping")
        node.vector_type = "POINT"
        self._feed(node.inputs["Vector"], vector)
        node.inputs["Scale"].default_value = scale
        node.inputs["Rotation"].default_value = tuple(math.radians(r) for r in rot_deg)
        node.inputs["Location"].default_value = loc
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

    def white1d(self, w):
        """Random value (0..1) per input value, used for per-ring variation."""
        node = self._new("ShaderNodeTexWhiteNoise")
        node.noise_dimensions = "1D"
        self._feed(node.inputs["W"], w)
        return node.outputs["Value"]

    def voronoi(self, vector, scale, randomness=1.0, feature="F1"):
        node = self._new("ShaderNodeTexVoronoi")
        node.voronoi_dimensions = "3D"
        node.feature = feature
        self._feed(node.inputs["Vector"], vector)
        node.inputs["Scale"].default_value = scale
        node.inputs["Randomness"].default_value = randomness
        return node

    def sepcol(self, color):
        node = self._new("ShaderNodeSeparateColor")
        self._feed(node.inputs["Color"], color)
        return node

    def sepxyz(self, vector):
        node = self._new("ShaderNodeSeparateXYZ")
        self._feed(node.inputs["Vector"], vector)
        return node

    def xyz(self, x, y, z):
        node = self._new("ShaderNodeCombineXYZ")
        self._feed(node.inputs["X"], x)
        self._feed(node.inputs["Y"], y)
        self._feed(node.inputs["Z"], z)
        return node.outputs["Vector"]

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
# Building blocks
# ----------------------------------------------------------------------
def new_material(name, scaled=True):
    """Create an empty material and return (material, builder, object-space position)."""
    old = bpy.data.materials.get(name)
    if old:
        bpy.data.materials.remove(old)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    b = Builder(mat.node_tree)
    tex = b._new("ShaderNodeTexCoord")
    if scaled:
        pos = b.mapping(tex.outputs["Object"], scale=(GLOBAL_SCALE,) * 3)
    else:
        pos = tex.outputs["Object"]
    return mat, b, pos


def finish(b, base_color, metallic, roughness, normal):
    """Principled BSDF + Material Output."""
    bsdf = b._new("ShaderNodeBsdfPrincipled")
    b._feed(bsdf.inputs["Base Color"], base_color)
    b._feed(bsdf.inputs["Metallic"], metallic)
    b._feed(bsdf.inputs["Roughness"], roughness)
    b._feed(bsdf.inputs["Normal"], normal)
    out = b._new("ShaderNodeOutputMaterial")
    b._feed(out.inputs["Surface"], bsdf.outputs["BSDF"])


def bump(b, height, strength, distance, normal=None):
    """Bump node; chain several of them through the normal input."""
    node = b._new("ShaderNodeBump")
    node.inputs["Strength"].default_value = strength
    node.inputs["Distance"].default_value = distance
    b._feed(node.inputs["Height"], height)
    b._feed(node.inputs["Normal"], normal)
    return node.outputs["Normal"]


def scratch_layer(b, pos, rot, stretch, width, cover_scale,
                  cover_lo=0.50, cover_hi=0.65, x_scale=1.5, distortion=0.0):
    """Thin stretched noise contours (cracks / scratches) masked by a coverage noise."""
    loc = (rot * 0.173, rot * 0.311, rot * 0.057)  # decorrelates layers
    stretched = b.mapping(pos, scale=(x_scale, stretch, x_scale),
                          rot_deg=(0, 0, rot), loc=loc)
    n = b.noise(stretched, 1.0, detail=3.0, rough=0.5, distortion=distortion).outputs["Fac"]
    line = b.op("ABSOLUTE", b.op("SUBTRACT", n, 0.5))
    line = b.remap(line, 0.0, width, 1.0, 0.0)
    cover_vec = b.mapping(pos, loc=loc)
    cover = b.noise(cover_vec, cover_scale, detail=3.0).outputs["Fac"]
    cover = b.remap(cover, cover_lo, cover_hi, 0.0, 1.0)
    return b.op("MULTIPLY", line, cover)


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_brick_wall():
    mat, b, pos = new_material(MATERIAL_NAME)

    wear = clamp01(WEAR)
    variation = clamp01(COLOR_VARIATION)
    brick_base = hex_to_linear(BRICK_COLOR_HEX)
    mortar_base = hex_to_linear(MORTAR_COLOR_HEX)

    # --- Layout: running bond, every second row shifted by half a brick ------
    sep = b.sepxyz(pos)
    x, y = sep.outputs["X"], sep.outputs["Y"]
    pitch_x = BRICK_WIDTH + MORTAR_WIDTH
    pitch_y = BRICK_HEIGHT + MORTAR_WIDTH

    yy = b.op("MULTIPLY", y, 1.0 / pitch_y)
    row = b.op("FLOOR", yy)
    shift = b.op("FRACT", b.op("MULTIPLY", row, 0.5))
    xx = b.op("ADD", b.op("MULTIPLY", x, 1.0 / pitch_x), shift)
    col = b.op("FLOOR", xx)
    fx = b.op("FRACT", xx)
    fy = b.op("FRACT", yy)

    def inner_distance(frac, pitch):
        """Distance (m) from the brick edge, positive inside the brick."""
        nearest = b.op("MINIMUM", frac, b.op("SUBTRACT", 1.0, frac))
        return b.op("SUBTRACT", b.op("MULTIPLY", nearest, pitch), MORTAR_WIDTH * 0.5)

    edge_dist = b.op("MINIMUM", inner_distance(fx, pitch_x), inner_distance(fy, pitch_y))

    # --- Per-brick random values ---------------------------------------------
    brick_id = b.op("ADD", col, b.op("MULTIPLY", row, 37.0))

    def brick_random(mult, add):
        return b.white1d(b.op("MULTIPLY_ADD", brick_id, mult, add))

    rnd1 = brick_random(0.7311, 0.11)
    rnd2 = brick_random(1.913, 5.7)
    rnd3 = brick_random(3.337, 11.3)
    offset = b.xyz(b.op("MULTIPLY", rnd1, 13.0),
                   b.op("MULTIPLY", rnd2, 17.0),
                   b.op("MULTIPLY", rnd3, 11.0))
    brick_pos = b.vop("ADD", pos, offset)   # every brick gets its own noise patch

    # --- Chipped edges: noise only eats the brick, it never grows into mortar --
    chip_noise = b.noise(pos, 38.0, detail=5.0, rough=0.6, distortion=0.2).outputs["Fac"]
    chip_amount = b.remap(chip_noise, 0.35, 0.75, 0.0, 1.0)
    shrink = b.op("MULTIPLY", chip_amount, 0.002 + 0.016 * wear)
    v = b.op("SUBTRACT", edge_dist, shrink)
    brick_mask = b.remap(v, 0.0, 0.0006, 0.0, 1.0)
    mortar_mask = b.op("SUBTRACT", 1.0, brick_mask)
    profile = b.remap(v, 0.0, EDGE_ROUND * (1.0 + wear), 0.0, 1.0, smooth=True)
    chip_vis = min(1.0, wear * 3.0)
    fresh_break = b.op("MULTIPLY",
                       b.op("MULTIPLY", b.remap(v, 0.0, 0.004, 1.0, 0.0),
                            b.remap(chip_amount, 0.35, 0.70, 0.0, 1.0)),
                       b.op("MULTIPLY", brick_mask, chip_vis))

    # --- Common noises ---------------------------------------------------------
    grain = b.noise(pos, 90.0, detail=8.0, rough=0.7).outputs["Fac"]
    mottle = b.noise(brick_pos, 12.0, detail=8.0, rough=0.6, distortion=0.2).outputs["Fac"]
    soft = b.noise(pos, 3.0, detail=6.0, rough=0.6, distortion=0.4).outputs["Fac"]

    # --- Surface flaking (small pits), appears above 5% wear ----------------------
    if wear <= 0.05:
        spall = 0.0
    else:
        vor = b.voronoi(brick_pos, 30.0)
        cell = b.sepcol(vor.outputs["Color"])
        density = 0.01 + 0.10 * wear
        on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - density)
        pit = b.remap(vor.outputs["Distance"], 0.22, 0.34, 1.0, 0.0)
        spall = b.op("MULTIPLY", b.op("MULTIPLY", pit, on), brick_mask)

    # --- Cracks in some bricks (appear above 15% wear) ------------------------------
    if wear <= 0.15:
        cracks = 0.0
    else:
        crack_chance = 0.05 + 0.35 * wear
        cracked = b.op("GREATER_THAN", rnd3, 1.0 - crack_chance)
        width = 0.012 + 0.010 * wear
        line_a = scratch_layer(b, brick_pos, 75.0, 6.0, width, 4.0, 0.30, 0.40,
                               x_scale=6.0, distortion=0.4)
        line_b = scratch_layer(b, brick_pos, 115.0, 6.0, width, 5.0, 0.30, 0.40,
                               x_scale=6.0, distortion=0.4)
        cracks = b.op("MULTIPLY", b.op("MAXIMUM", line_a, line_b),
                      b.op("MULTIPLY", cracked, brick_mask))

    # --- Salt stains (efflorescence), appear above 10% wear ---------------------------
    if wear <= 0.10:
        salt = 0.0
    else:
        salt_noise = b.noise(pos, 4.5, detail=6.0, rough=0.6, distortion=0.3).outputs["Fac"]
        lo = 0.72 - 0.22 * wear
        salt_cover = b.remap(salt_noise, lo, lo + 0.08, 0.0, 1.0, smooth=True)
        salt = b.op("MULTIPLY", salt_cover, b.remap(grain, 0.30, 0.70, 0.4, 1.0))

    # --- Vertical dirt streaks (grow with wear) -----------------------------------------
    streak_vec = b.mapping(pos, scale=(9.0, 0.9, 9.0))
    streak_noise = b.noise(streak_vec, 1.0, detail=5.0, rough=0.6, distortion=0.2).outputs["Fac"]
    streaks = b.op("MULTIPLY", b.remap(streak_noise, 0.52, 0.80, 0.0, 1.0), wear)

    # --- Brick color ---------------------------------------------------------------------------
    brick_col = b.mixc(shade(brick_base, 1.0 - 0.30 * variation),
                       shade(brick_base, 1.0 + 0.30 * variation), rnd1)
    burnt = b.op("MULTIPLY", b.remap(rnd2, 0.85, 0.95, 0.0, 1.0), 0.7 * variation)
    brick_col = b.mixc(brick_col, shade(brick_base, 0.50), burnt)
    brick_col = b.mixc(brick_col, shade(brick_base, 0.75), b.remap(mottle, 0.35, 0.70, 0.0, 0.5))
    brick_col = b.mixc(brick_col, shade(brick_base, 1.25), b.remap(grain, 0.55, 0.75, 0.0, 0.3))
    dust = shade(mortar_base, 0.90)
    brick_col = b.mixc(brick_col, dust, b.op("MULTIPLY", b.remap(soft, 0.30, 0.70, 0.0, 1.0),
                                             0.35 * wear))
    brick_col = b.mixc(brick_col, shade(brick_base, 1.30), b.op("MULTIPLY", fresh_break, 0.7))
    brick_col = b.mixc(brick_col, shade(brick_base, 1.25), b.op("MULTIPLY", spall, 0.6))
    brick_col = b.mixc(brick_col, shade(mortar_base, 1.25), b.op("MULTIPLY", salt, 0.55))
    brick_col = b.mixc(brick_col, shade(brick_base, 0.08), b.op("MULTIPLY", streaks, 0.55))
    brick_col = b.mixc(brick_col, shade(brick_base, 0.05), b.op("MULTIPLY", cracks, 0.95))

    # --- Mortar color ----------------------------------------------------------------------------
    sand = b.noise(pos, 45.0, detail=6.0, rough=0.7).outputs["Fac"]
    mortar_col = b.mixc(shade(mortar_base, 0.80), shade(mortar_base, 1.15),
                        b.remap(sand, 0.35, 0.70, 0.0, 1.0))
    mortar_col = b.mixc(mortar_col, shade(mortar_base, 0.40),
                        b.op("MULTIPLY", b.remap(soft, 0.30, 0.70, 0.0, 1.0), 0.6 * wear))
    mortar_col = b.mixc(mortar_col, shade(mortar_base, 0.10), b.op("MULTIPLY", streaks, 0.5))

    color = b.mixc(mortar_col, brick_col, brick_mask)

    # --- Roughness -------------------------------------------------------------------------------------
    rough = b.op("MULTIPLY_ADD", grain, 0.12, 0.82)
    rough = b.mixf(rough, 0.96, b.op("MAXIMUM", mortar_mask, fresh_break))

    # --- Normals: joints, chips, flaking and cracks first, then fine surface detail -------------------
    erode = 0.25 + 0.45 * wear
    mortar_noise = b.noise(pos, 60.0, detail=6.0, rough=0.7).outputs["Fac"]
    mortar_h = b.op("SUBTRACT", 1.0,
                    b.op("MULTIPLY", b.op("MULTIPLY_ADD", mortar_noise, 0.6, 0.7), erode))
    brick_h = b.op("MULTIPLY_ADD", profile, 0.4, 0.6)
    brick_h = b.op("ADD", brick_h, b.op("MULTIPLY", b.op("SUBTRACT", rnd1, 0.5), 0.08))
    brick_h = b.op("SUBTRACT", brick_h, b.op("MULTIPLY", spall, 0.35))
    brick_h = b.op("SUBTRACT", brick_h, b.op("MULTIPLY", cracks, 0.30))
    height = b.mixf(mortar_h, brick_h, brick_mask)
    n1 = bump(b, height, 1.0, BRICK_RELIEF)

    fine = b.op("ADD", b.op("MULTIPLY", mottle, 0.5), b.op("MULTIPLY", grain, 0.5))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", spall, 0.5))
    n2 = bump(b, fine, DETAIL_STRENGTH, 0.003, normal=n1)

    finish(b, color, 0.0, rough, n2)
    return mat


def assign_material(mat):
    """Assign to the active mesh object, or create a preview plane."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        bpy.ops.mesh.primitive_plane_add(size=PLANE_SIZE)
        obj = bpy.context.active_object

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


if __name__ == "__main__":
    material = build_brick_wall()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
