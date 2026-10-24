"""
Controllable laminate floor for Blender 3.4+ / 4.x.

Long planks with a random stagger in every row. Each plank has its own wood
grain, color shade, beveled edges and random damage. The layout is built from
math nodes, so every plank has its own random values and an exact distance to
its edge. Everything is driven by the "SLIDERS" block below. Change a constant,
run the script again, and the material is rebuilt.

    LAMINATE_COLOR_HEX  main color of the planks   ("#RRGGBB", sRGB)
    CRACKS_AMOUNT       0 = intact floor    1 = cracked across the boards, chipped
                        edges and corners (bare fiberboard core shows through),
                        swollen and lifted plank edges
    WEAR_AMOUNT         0 = new, even satin finish    1 = heavily walked on:
                        scuffs and scratches, dull patches, dirt in the seams,
                        finish worn through to the core

Quick looks to try:
    new apartment   : CRACKS 0.0, WEAR 0.0
    lived-in room   : CRACKS 0.15, WEAR 0.4
    old rental flat : CRACKS 0.4, WEAR 0.7
    flooded ruin    : CRACKS 1.0, WEAR 1.0

The pattern lies in the object's XY plane with the grain along X, so use it on
a plane (the preview plane is 2 x 2 m, about 1.7 planks long).

Usage: open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a plane is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# SLIDERS (the values you will change most often)
# ----------------------------------------------------------------------
LAMINATE_COLOR_HEX = "#B88A5A"   # main plank color, sRGB hex
CRACKS_AMOUNT = 0.25             # 0..1  cracks, chips and swollen edges
WEAR_AMOUNT = 0.4                # 0..1  scuffs, dull patches, dirt

# ----------------------------------------------------------------------
# Fine tuning
# ----------------------------------------------------------------------
MATERIAL_NAME = "Laminate_Controllable"
PLANE_SIZE = 2.0             # meters, only used when a plane is created
GLOBAL_SCALE = 1.0           # scales every pattern at once

PLANK_LENGTH = 1.20          # meters
PLANK_WIDTH = 0.19           # meters
GAP_WIDTH = 0.0010           # meters, dark joint between planks
BEVEL_WIDTH = 0.0015         # meters, V-groove along the plank edge
GRAIN_FREQ = 30.0            # wood grain rings across a plank (higher = finer grain)
LAMINATE_ROUGHNESS = 0.38    # 0.15 = glossy, 0.6 = matte
PLANK_RELIEF = 0.0025        # normal-map strength of joints, chips, cracks (Bump distance)
COLOR_VARIATION = 0.5        # 0..1  how different the planks are from each other
DETAIL_STRENGTH = 1.0        # strength of the embossed grain in the normal map
CORE_COLOR = (0.30, 0.22, 0.15, 1.0)   # fiberboard core (linear RGBA)

# (rotation in degrees, stretch along Y, line width, coverage noise scale)
# WEAR_AMOUNT decides how many of these scuff layers are used.
SCUFF_LAYERS = [
    (5.0, 16.0, 0.030, 3.0),
    (50.0, 24.0, 0.030, 4.0),
    (95.0, 30.0, 0.025, 5.0),
    (140.0, 20.0, 0.025, 6.0),
    (75.0, 36.0, 0.020, 7.0),
]


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


def ring_rand(b, ring_index):
    """Random 0..1 value per growth ring."""
    return b.white1d(b.op("MULTIPLY", ring_index, 3.7311))


def ring_colors(b, ring, rand, stops, contrast=0.3):
    """Early wood -> late wood gradient with per-ring contrast variation."""
    factor = b.op("MULTIPLY_ADD", rand, contrast, 1.0 - contrast * 0.5)
    return b.ramp(b.op("MULTIPLY", ring, factor), stops)


def wood_rings(b, pos, freq, tilt=3.0, axis_offset=(0.0, -0.6, 0.35),
               warp=0.06, warp_scale=1.5, phase=None):
    """
    Growth rings around a tilted axis running along X. The flat surface cuts the
    rings at a shallow angle, which gives the cathedral (arched) grain.
    Returns (ring, rand): ring runs 0..1 inside every ring (early -> late wood),
    rand is a random value per ring.
    """
    vec = b.mapping(pos, rot_deg=(0.0, tilt, 0.0), loc=axis_offset)
    low = b.mapping(pos, scale=(0.25, 1.0, 1.0))
    wave = b.noise(low, warp_scale, detail=4.0, rough=0.55, distortion=0.2).outputs["Fac"]
    sep = b.sepxyz(vec)
    y, z = sep.outputs["Y"], sep.outputs["Z"]
    r = b.op("SQRT", b.op("ADD", b.op("MULTIPLY", y, y), b.op("MULTIPLY", z, z)))
    r = b.op("ADD", r, b.op("MULTIPLY", b.op("SUBTRACT", wave, 0.5), warp))
    t = b.op("MULTIPLY", r, freq)
    if phase is not None:
        t = b.op("ADD", t, phase)
    return b.op("FRACT", t), ring_rand(b, b.op("FLOOR", t))


def fibers(b, pos, x_scale, yz_scale, detail=8.0, rough=0.6, distortion=0.0):
    """Noise stretched along the grain (X axis): wood fiber streaks."""
    s = b.mapping(pos, scale=(x_scale, yz_scale, yz_scale))
    return b.noise(s, 1.0, detail=detail, rough=rough, distortion=distortion).outputs["Fac"]


def pores(b, pos, x_scale, yz_scale, lo, hi, mask=None):
    """Short dark pore streaks along the grain, optionally limited to early wood."""
    n = fibers(b, pos, x_scale, yz_scale, detail=2.0, rough=0.5)
    pore_mask = b.remap(n, lo, hi, 1.0, 0.0)
    if mask is not None:
        pore_mask = b.op("MULTIPLY", pore_mask, mask)
    return pore_mask


def scuffs_from_amount(b, pos, amount):
    """
    WEAR_AMOUNT -> scuff and scratch mask (0..1 socket), or the constant 0.0.
    More wear = more layers, wider lines and wider coverage.
    """
    a = clamp01(amount)
    if a <= 0.0:
        return 0.0
    count = max(1, round(1 + 4 * a))
    cover_lo = 0.70 - 0.30 * a
    cover_hi = cover_lo + 0.10
    width_scale = 0.6 + 0.8 * a
    result = None
    for rot, stretch, width, cover_scale in SCUFF_LAYERS[:count]:
        layer = scratch_layer(b, pos, rot, stretch, width * width_scale, cover_scale,
                              cover_lo, cover_hi, distortion=0.15)
        result = layer if result is None else b.op("MAXIMUM", result, layer)
    return result


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_laminate():
    mat, b, pos = new_material(MATERIAL_NAME)

    crack = clamp01(CRACKS_AMOUNT)
    wear = clamp01(WEAR_AMOUNT)
    variation = clamp01(COLOR_VARIATION)
    base = hex_to_linear(LAMINATE_COLOR_HEX)

    # --- Layout: planks along X, random stagger in every row ------------------------
    pitch_x = PLANK_LENGTH + GAP_WIDTH
    pitch_y = PLANK_WIDTH + GAP_WIDTH
    half_x = PLANK_LENGTH * 0.5
    half_y = PLANK_WIDTH * 0.5
    sep = b.sepxyz(pos)
    x, y = sep.outputs["X"], sep.outputs["Y"]

    yy = b.op("MULTIPLY", y, 1.0 / pitch_y)
    row = b.op("FLOOR", yy)
    row_shift = b.op("MULTIPLY", b.white1d(b.op("MULTIPLY_ADD", row, 0.917, 0.31)), pitch_x)
    xx = b.op("MULTIPLY", b.op("ADD", x, row_shift), 1.0 / pitch_x)
    col = b.op("FLOOR", xx)
    # Position inside the plank cell, in meters from the plank center
    cx = b.op("MULTIPLY", b.op("SUBTRACT", b.op("FRACT", xx), 0.5), pitch_x)
    cy = b.op("MULTIPLY", b.op("SUBTRACT", b.op("FRACT", yy), 0.5), pitch_y)
    dxe = b.op("SUBTRACT", half_x, b.op("ABSOLUTE", cx))
    dye = b.op("SUBTRACT", half_y, b.op("ABSOLUTE", cy))
    edge_dist = b.op("MINIMUM", dxe, dye)                      # > 0 inside the plank
    corner_d = b.op("SQRT", b.op("ADD", b.op("MULTIPLY", dxe, dxe), b.op("MULTIPLY", dye, dye)))

    # --- Per-plank random values -----------------------------------------------------------
    plank_id = b.op("ADD", col, b.op("MULTIPLY", row, 71.0))

    def plank_random(mult, add):
        return b.white1d(b.op("MULTIPLY_ADD", plank_id, mult, add))

    rnd1 = plank_random(0.7311, 0.11)    # grain position / color
    rnd2 = plank_random(1.913, 5.7)      # grain axis / brightness
    rnd3 = plank_random(3.337, 11.3)     # cracks
    rnd4 = plank_random(5.171, 2.9)      # darkness
    rnd5 = plank_random(7.043, 8.3)      # swollen edges
    offset = b.xyz(b.op("MULTIPLY", rnd1, 13.0),
                   b.op("MULTIPLY", rnd3, 17.0),
                   b.op("MULTIPLY", rnd5, 11.0))
    plank_pos = b.vop("ADD", pos, offset)    # every plank gets its own noise patch
    # Wood-grain coordinates: local to the plank, shifted differently for every plank
    wood_pos = b.xyz(b.op("ADD", cx, b.op("MULTIPLY", b.op("SUBTRACT", rnd1, 0.5), 0.8)),
                     b.op("MULTIPLY", b.op("SUBTRACT", rnd2, 0.5), 0.30),
                     0.0)

    geo_plank = b.remap(edge_dist, 0.0, 0.0003, 0.0, 1.0)
    gap_mask = b.op("SUBTRACT", 1.0, geo_plank)

    # --- Chipped edges: noise only eats the plank, stronger near the corners -------------------
    chip_noise = b.noise(pos, 70.0, detail=5.0, rough=0.6, distortion=0.2).outputs["Fac"]
    chip_amount = b.remap(chip_noise, 0.35, 0.75, 0.0, 1.0)
    corner_close = b.remap(corner_d, 0.0, 0.04, 1.0, 0.0)
    chip_size = b.op("MULTIPLY", b.op("MULTIPLY_ADD", corner_close, 3.0, 1.0),
                     0.0003 + 0.006 * crack)
    v = b.op("SUBTRACT", edge_dist, b.op("MULTIPLY", chip_amount, chip_size))
    mask_chip = b.remap(v, 0.0, 0.0003, 0.0, 1.0)              # decor layer still present
    core_zone = b.op("SUBTRACT", geo_plank, mask_chip, clamp=True)   # chipped down to the core
    bevel = b.remap(v, 0.0, BEVEL_WIDTH, 0.0, 1.0, smooth=True)

    # --- Cracks across the boards and swollen edges (CRACKS_AMOUNT) -----------------------------
    if crack <= 0.0:
        cracks = 0.0
        swell = 0.0
    else:
        chance = 0.03 + 0.40 * crack
        cracked = b.op("GREATER_THAN", rnd3, 1.0 - chance)
        width = 0.008 + 0.008 * crack
        line_a = scratch_layer(b, wood_pos, 80.0, 12.0, width, 3.0, 0.30, 0.40,
                               x_scale=4.0, distortion=0.5)
        line_b = scratch_layer(b, wood_pos, 100.0, 12.0, width, 4.0, 0.30, 0.40,
                               x_scale=4.0, distortion=0.5)
        cracks = b.op("MULTIPLY", b.op("MAXIMUM", line_a, line_b),
                      b.op("MULTIPLY", cracked, geo_plank))

        if crack <= 0.10:
            swell = 0.0
        else:
            swollen = b.op("GREATER_THAN", rnd5, 1.0 - (0.10 + 0.50 * crack))
            swell_noise = b.noise(plank_pos, 12.0, detail=4.0).outputs["Fac"]
            swell = b.op("MULTIPLY",
                         b.op("MULTIPLY", b.remap(edge_dist, 0.0, 0.015, 1.0, 0.0), swollen),
                         b.op("MULTIPLY", b.remap(swell_noise, 0.35, 0.70, 0.3, 1.0), geo_plank))

    # --- Wear (WEAR_AMOUNT): scuffs, dull patches, dirt, finish worn through -------------------------
    scuffs = scuffs_from_amount(b, pos, wear)
    traffic = b.noise(pos, 1.8, detail=5.0, rough=0.6, distortion=0.4).outputs["Fac"]
    if wear <= 0.0:
        dull = 0.0
    else:
        dull_lo = 0.62 - 0.30 * wear
        dull = b.remap(traffic, dull_lo, dull_lo + 0.12, 0.0, 1.0, smooth=True)
    if wear <= 0.5:
        through = 0.0
    else:
        through_noise = b.noise(pos, 6.0, detail=5.0, rough=0.6, distortion=0.3).outputs["Fac"]
        through_lo = 0.85 - 0.25 * (wear - 0.5) / 0.5
        through = b.op("MULTIPLY", b.remap(through_noise, through_lo, through_lo + 0.05, 0.0, 1.0),
                       b.op("MULTIPLY", dull, geo_plank))
    soft = b.noise(pos, 3.5, detail=6.0, rough=0.6, distortion=0.4).outputs["Fac"]
    dirt = b.op("MULTIPLY", b.remap(soft, 0.45, 0.80, 0.0, 1.0), 0.8 * wear)

    # --- Wood decor: grain rings, fibers, pores ---------------------------------------------------------
    ring, rand = wood_rings(b, wood_pos, GRAIN_FREQ, tilt=5.0,
                            axis_offset=(0.0, 0.0, 0.03), warp=0.02)
    wood_col = ring_colors(b, ring, rand, [
        (0.00, shade(base, 1.15)),
        (0.60, shade(base, 0.95)),
        (1.00, shade(base, 0.60)),
    ], contrast=0.35)
    fib = fibers(b, wood_pos, 5.0, 250.0, detail=6.0, rough=0.6, distortion=0.2)
    early = b.remap(ring, 0.0, 0.55, 1.0, 0.0)
    pore = pores(b, wood_pos, 30.0, 400.0, 0.36, 0.46, mask=early)

    # --- Color ---------------------------------------------------------------------------------------------
    col_p = b.mixc(wood_col, shade(base, 0.40), b.op("MULTIPLY", rnd4, 0.25 * variation))
    col_p = b.mixc(col_p, shade(base, 1.60), b.op("MULTIPLY", rnd2, 0.12 * variation))
    col_p = b.mixc(col_p, shade(base, 1.25), b.remap(fib, 0.55, 0.80, 0.0, 0.3))
    col_p = b.mixc(col_p, shade(base, 0.50), b.remap(fib, 0.45, 0.20, 0.0, 0.3))
    col_p = b.mixc(col_p, shade(base, 0.25), b.op("MULTIPLY", pore, 0.5))
    col_p = b.mixc(col_p, shade(base, 1.35), b.op("MULTIPLY", dull, 0.35))
    col_p = b.mixc(col_p, shade(base, 1.70), b.op("MULTIPLY", scuffs, 0.45))
    col_p = b.mixc(col_p, shade(base, 0.25), b.op("MULTIPLY", dirt, 0.5))
    col_p = b.mixc(col_p, shade(base, 0.35), b.op("MULTIPLY", swell, 0.6))
    col_p = b.mixc(col_p, shade(base, 0.08), b.op("MULTIPLY", cracks, 0.9))
    col_p = b.mixc(col_p, CORE_COLOR, b.op("MULTIPLY", through, 0.85))

    # Joints between planks: V-groove plus the gap, dirtier the more worn the floor is
    seam_col = b.mixc(shade(base, 0.18), shade(base, 0.05), 0.8 * wear)
    seam_dark = b.op("MAXIMUM", gap_mask, b.op("MULTIPLY", b.op("SUBTRACT", 1.0, bevel), 0.7))
    color = b.mixc(col_p, seam_col, seam_dark)
    core_noise = b.noise(pos, 80.0, detail=6.0, rough=0.7).outputs["Fac"]
    core_col = b.mixc(shade(CORE_COLOR, 0.8), shade(CORE_COLOR, 1.3),
                      b.remap(core_noise, 0.35, 0.70, 0.0, 1.0))
    color = b.mixc(color, core_col, b.op("MULTIPLY", core_zone, 0.95))

    # --- Roughness -----------------------------------------------------------------------------------------------
    rough = b.op("MULTIPLY_ADD", fib, 0.10, LAMINATE_ROUGHNESS - 0.05)
    rough = b.mixf(rough, 0.65, dull)
    rough = b.mixf(rough, 0.75, scuffs)
    rough = b.mixf(rough, 0.90, through)
    rough = b.mixf(rough, 0.92, b.op("MAXIMUM", seam_dark, core_zone))

    # --- Normals: joints, chips, cracks, swelling first, then the embossed grain ----------------------------------
    plank_h = b.op("ADD", b.op("MULTIPLY_ADD", bevel, 0.3, 0.7), b.op("MULTIPLY", swell, 0.35))
    plank_h = b.op("SUBTRACT", plank_h, b.op("MULTIPLY", cracks, 0.5))
    plank_h = b.op("SUBTRACT", plank_h, b.op("MULTIPLY", through, 0.12))
    plank_h = b.mixf(plank_h, 0.45, core_zone)
    height = b.mixf(0.0, plank_h, geo_plank)
    n1 = bump(b, height, 1.0, PLANK_RELIEF)

    fine = b.op("ADD", b.op("MULTIPLY", fib, 0.35), b.op("MULTIPLY", ring, 0.15))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", pore, 0.5))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", scuffs, 0.30))
    n2 = bump(b, fine, DETAIL_STRENGTH, 0.0012, normal=n1)

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
    material = build_laminate()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
