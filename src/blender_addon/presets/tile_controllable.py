"""
Controllable glazed ceramic tile for Blender 3.4+ / 4.x.

A square-tile grid built from math nodes, so every tile has its own random
values and an exact distance to its edge: per-tile color, chipped edges and
broken corners, missing fragments, grout recesses. Everything is driven by
the "SLIDERS" block below. Change a constant, run the script again, and the
material is rebuilt.

    TILE_COLOR_HEX   main color of the tiles          ("#RRGGBB", sRGB)
    GROUT_COLOR_HEX  color of the grout between tiles ("#RRGGBB", sRGB)
    SCRATCH_AMOUNT   0 = clean glaze    1 = covered in scratches and hairline cracks
    DAMAGE           0 = brand new      1 = destroyed: chipped edges, broken-off
                     corners, missing fragments and whole tiles, cracked tiles,
                     peeled glaze, eroded grout, dirt

Quick looks to try:
    new bathroom   : SCRATCH 0.0, DAMAGE 0.0
    old kitchen    : SCRATCH 0.4, DAMAGE 0.2
    public toilet  : SCRATCH 0.7, DAMAGE 0.5
    ruin           : SCRATCH 0.8, DAMAGE 1.0

The pattern lies in the object's XY plane, so use it on a plane (the preview
plane is 2 x 2 m, about 6 tiles wide).

Usage: open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a plane is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# SLIDERS (the values you will change most often)
# ----------------------------------------------------------------------
TILE_COLOR_HEX = "#DCE3E6"     # main tile color, sRGB hex
GROUT_COLOR_HEX = "#8C8A85"    # grout color, sRGB hex
SCRATCH_AMOUNT = 0.3           # 0..1  scratches and hairline cracks
DAMAGE = 0.3                   # 0..1  degree of destruction

# ----------------------------------------------------------------------
# Fine tuning
# ----------------------------------------------------------------------
MATERIAL_NAME = "Tile_Controllable"
PLANE_SIZE = 2.0             # meters, only used when a plane is created
GLOBAL_SCALE = 1.0           # scales every pattern at once

TILE_SIZE = 0.30             # meters (square tile)
GROUT_WIDTH = 0.006          # meters
EDGE_ROUND = 0.002           # rounded tile edge in meters (grows with damage)
GLAZE_ROUGHNESS = 0.12       # 0.05 = mirror-like glaze, 0.4 = satin
TILE_RELIEF = 0.004          # normal-map strength of grout and chips (Bump distance)
COLOR_VARIATION = 0.3        # 0..1  how different the tiles are from each other
DETAIL_STRENGTH = 1.0        # strength of the fine normal detail
BISCUIT_COLOR = (0.50, 0.40, 0.30, 1.0)   # clay body visible where the glaze has peeled

# (rotation in degrees, stretch along Y, line width, coverage noise scale)
# SCRATCH_AMOUNT decides how many of these layers are used.
SCRATCH_LAYERS = [
    (10.0, 18.0, 0.020, 3.0),
    (55.0, 26.0, 0.020, 4.0),
    (100.0, 34.0, 0.018, 5.0),
    (145.0, 22.0, 0.018, 6.0),
    (80.0, 40.0, 0.015, 7.0),
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


def scratches_from_amount(b, pos, amount):
    """
    SCRATCH_AMOUNT -> scratch mask (0..1 socket), or the constant 0.0.
    More amount = more layers, wider lines and wider coverage.
    """
    a = clamp01(amount)
    if a <= 0.0:
        return 0.0
    count = max(1, round(1 + 4 * a))
    cover_lo = 0.70 - 0.30 * a
    cover_hi = cover_lo + 0.10
    width_scale = 0.6 + 0.8 * a
    result = None
    for rot, stretch, width, cover_scale in SCRATCH_LAYERS[:count]:
        layer = scratch_layer(b, pos, rot, stretch, width * width_scale, cover_scale,
                              cover_lo, cover_hi, distortion=0.15)
        result = layer if result is None else b.op("MAXIMUM", result, layer)
    return result


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_tile():
    mat, b, pos = new_material(MATERIAL_NAME)

    scr = clamp01(SCRATCH_AMOUNT)
    dmg = clamp01(DAMAGE)
    variation = clamp01(COLOR_VARIATION)
    tile_base = hex_to_linear(TILE_COLOR_HEX)
    grout_base = hex_to_linear(GROUT_COLOR_HEX)

    # --- Layout: square tiles with grout lines --------------------------------
    pitch = TILE_SIZE + GROUT_WIDTH
    half = TILE_SIZE * 0.5
    sep = b.sepxyz(pos)
    xx = b.op("MULTIPLY", sep.outputs["X"], 1.0 / pitch)
    yy = b.op("MULTIPLY", sep.outputs["Y"], 1.0 / pitch)
    col = b.op("FLOOR", xx)
    row = b.op("FLOOR", yy)
    # Position inside the tile cell, in meters from the tile center
    cx = b.op("MULTIPLY", b.op("SUBTRACT", b.op("FRACT", xx), 0.5), pitch)
    cy = b.op("MULTIPLY", b.op("SUBTRACT", b.op("FRACT", yy), 0.5), pitch)
    ax = b.op("ABSOLUTE", cx)
    ay = b.op("ABSOLUTE", cy)
    edge_dist = b.op("SUBTRACT", half, b.op("MAXIMUM", ax, ay))    # > 0 inside the tile
    dxe = b.op("SUBTRACT", half, ax)
    dye = b.op("SUBTRACT", half, ay)
    corner_d = b.op("SQRT", b.op("ADD", b.op("MULTIPLY", dxe, dxe), b.op("MULTIPLY", dye, dye)))

    # --- Per-tile random values -------------------------------------------------
    tile_id = b.op("ADD", col, b.op("MULTIPLY", row, 53.0))

    def tile_random(mult, add):
        return b.white1d(b.op("MULTIPLY_ADD", tile_id, mult, add))

    rnd1 = tile_random(0.7311, 0.11)    # color
    rnd2 = tile_random(1.913, 5.7)      # broken corner chance
    rnd3 = tile_random(3.337, 11.3)     # hairline cracks
    rnd4 = tile_random(5.171, 2.9)      # missing tile
    rnd5 = tile_random(7.043, 8.3)      # broken tile crack
    rnd6 = tile_random(9.277, 4.1)      # which corner breaks off
    offset = b.xyz(b.op("MULTIPLY", rnd1, 13.0),
                   b.op("MULTIPLY", rnd3, 17.0),
                   b.op("MULTIPLY", rnd5, 11.0))
    tile_pos = b.vop("ADD", pos, offset)    # every tile gets its own noise patch

    geo_tile = b.remap(edge_dist, 0.0, 0.0004, 0.0, 1.0)

    # --- Chipped edges: noise only eats the tile, stronger near the corners -------
    chip_noise = b.noise(pos, 45.0, detail=5.0, rough=0.6, distortion=0.2).outputs["Fac"]
    chip_amount = b.remap(chip_noise, 0.35, 0.75, 0.0, 1.0)
    corner_close = b.remap(corner_d, 0.0, 0.05, 1.0, 0.0)
    chip_size = b.op("MULTIPLY", b.op("MULTIPLY_ADD", corner_close, 3.0, 1.0),
                     0.0005 + 0.010 * dmg)
    v = b.op("SUBTRACT", edge_dist, b.op("MULTIPLY", chip_amount, chip_size))
    mask_chip = b.remap(v, 0.0, 0.0004, 0.0, 1.0)
    profile = b.remap(v, 0.0, EDGE_ROUND * (1.0 + dmg), 0.0, 1.0, smooth=True)

    # --- Broken-off corner chunks (some tiles, one corner each), above 10% damage ---
    if dmg <= 0.10:
        chunk = 0.0
    else:
        gate = b.op("GREATER_THAN", rnd2, 1.0 - 0.6 * dmg)
        index = b.op("ADD", b.op("GREATER_THAN", cx, 0.0),
                     b.op("MULTIPLY", b.op("GREATER_THAN", cy, 0.0), 2.0))
        target = b.op("FLOOR", b.op("MULTIPLY", rnd6, 3.999))
        same = b.op("LESS_THAN", b.op("ABSOLUTE", b.op("SUBTRACT", index, target)), 0.5)
        radius = b.op("MULTIPLY_ADD", rnd1, 0.05 * dmg, 0.015 + 0.02 * dmg)
        ragged = b.op("MULTIPLY", b.op("SUBTRACT", chip_noise, 0.5), 0.03)
        cd = b.op("ADD", corner_d, ragged)
        chunk = b.op("MULTIPLY", b.op("MULTIPLY", gate, same),
                     b.remap(cd, b.op("SUBTRACT", radius, 0.004), radius, 1.0, 0.0))

    # --- Broken-out fragments inside tiles, above 25% damage ------------------------
    if dmg <= 0.25:
        hole = 0.0
    else:
        vor = b.voronoi(tile_pos, 4.0)
        cell = b.sepcol(vor.outputs["Color"])
        density = 0.01 + 0.15 * (dmg - 0.25) / 0.75
        on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - density)
        edge_noise = b.noise(tile_pos, 14.0, detail=5.0, rough=0.6).outputs["Fac"]
        d2 = b.op("ADD", vor.outputs["Distance"],
                  b.op("MULTIPLY", b.op("SUBTRACT", edge_noise, 0.5), 0.25))
        hole = b.op("MULTIPLY", b.remap(d2, 0.20, 0.30, 1.0, 0.0), on)

    # --- Whole tiles missing, above 35% damage ---------------------------------------
    if dmg <= 0.35:
        missing = 0.0
    else:
        missing = b.op("GREATER_THAN", rnd4, 1.0 - 0.18 * (dmg - 0.35) / 0.65)

    # --- Final masks: tile glaze, exposed adhesive underneath, grout ------------------
    keep = b.op("MULTIPLY", b.op("SUBTRACT", 1.0, chunk),
                b.op("MULTIPLY", b.op("SUBTRACT", 1.0, hole), b.op("SUBTRACT", 1.0, missing)))
    tile_mask = b.op("MULTIPLY", mask_chip, keep)
    substrate_mask = b.op("SUBTRACT", geo_tile, tile_mask, clamp=True)

    # --- Peeled glaze, above 20% damage ----------------------------------------------------
    if dmg <= 0.20:
        peel = 0.0
    else:
        peel_noise = b.noise(tile_pos, 7.0, detail=6.0, rough=0.6, distortion=0.3).outputs["Fac"]
        lo = 0.72 - 0.25 * dmg
        peel = b.remap(peel_noise, lo, lo + 0.06, 0.0, 1.0, smooth=True)

    # --- Scratches and hairline cracks (SCRATCH_AMOUNT) ---------------------------------------
    scratches = scratches_from_amount(b, pos, scr)
    if scr <= 0.0:
        hair = 0.0
    else:
        chance = 0.02 + 0.40 * scr
        cracked = b.op("GREATER_THAN", rnd3, 1.0 - chance)
        width = 0.006 + 0.008 * scr
        line_a = scratch_layer(b, tile_pos, 20.0, 5.0, width, 3.0, 0.30, 0.40,
                               x_scale=5.0, distortion=0.5)
        line_b = scratch_layer(b, tile_pos, 110.0, 5.0, width, 4.0, 0.30, 0.40,
                               x_scale=5.0, distortion=0.5)
        hair = b.op("MULTIPLY", b.op("MAXIMUM", line_a, line_b),
                    b.op("MULTIPLY", cracked, geo_tile))

    # --- Large cracks through broken tiles (above 10% damage) -------------------------------------
    if dmg <= 0.10:
        broken = 0.0
    else:
        chance2 = 0.05 + 0.45 * dmg
        cracked2 = b.op("GREATER_THAN", rnd5, 1.0 - chance2)
        width2 = 0.014 + 0.012 * dmg
        big_a = scratch_layer(b, tile_pos, 35.0, 5.0, width2, 3.0, 0.30, 0.40,
                              x_scale=4.0, distortion=0.5)
        big_b = scratch_layer(b, tile_pos, 125.0, 5.0, width2, 4.0, 0.30, 0.40,
                              x_scale=4.0, distortion=0.5)
        broken = b.op("MULTIPLY", b.op("MAXIMUM", big_a, big_b),
                      b.op("MULTIPLY", cracked2, tile_mask))
    cracks = b.op("MAXIMUM", hair, broken)

    # --- Dirt and common noises ----------------------------------------------------------------------
    grain = b.noise(pos, 90.0, detail=8.0, rough=0.7).outputs["Fac"]
    soft = b.noise(pos, 3.5, detail=6.0, rough=0.6, distortion=0.4).outputs["Fac"]
    dirt = b.op("MULTIPLY", b.remap(soft, 0.45, 0.80, 0.0, 1.0), 0.7 * dmg)

    # --- Tile color --------------------------------------------------------------------------------------
    tile_col = b.mixc(shade(tile_base, 1.0 - 0.12 * variation),
                      shade(tile_base, 1.0 + 0.12 * variation), rnd1)
    glaze = b.noise(tile_pos, 8.0, detail=5.0, rough=0.55).outputs["Fac"]
    tile_col = b.mixc(tile_col, shade(tile_base, 0.92), b.remap(glaze, 0.35, 0.70, 0.0, 0.5))
    tile_col = b.mixc(tile_col, shade(tile_base, 0.80),
                      b.op("MULTIPLY", b.remap(edge_dist, 0.0, 0.012, 1.0, 0.0), 0.45))
    tile_col = b.mixc(tile_col, shade(tile_base, 1.60), b.op("MULTIPLY", scratches, 0.5))
    tile_col = b.mixc(tile_col, shade(tile_base, 0.30), b.op("MULTIPLY", dirt, 0.8))
    tile_col = b.mixc(tile_col, BISCUIT_COLOR, b.op("MULTIPLY", peel, 0.85))
    tile_col = b.mixc(tile_col, shade(tile_base, 0.08), b.op("MULTIPLY", cracks, 0.9))

    # --- Grout and exposed adhesive color --------------------------------------------------------------------
    sand = b.noise(pos, 70.0, detail=6.0, rough=0.7).outputs["Fac"]
    grout_col = b.mixc(shade(grout_base, 0.85), shade(grout_base, 1.15),
                       b.remap(sand, 0.35, 0.70, 0.0, 1.0))
    grout_dirt = b.op("MULTIPLY_ADD", dirt, 1.2, 0.08, clamp=True)
    grout_col = b.mixc(grout_col, shade(grout_base, 0.30), grout_dirt)
    sub_noise = b.noise(pos, 25.0, detail=6.0, rough=0.6).outputs["Fac"]
    substrate_col = b.mixc(shade(grout_base, 0.75), shade(grout_base, 0.40),
                           b.remap(sub_noise, 0.35, 0.75, 0.0, 1.0))

    color = b.mixc(grout_col, substrate_col, substrate_mask)
    color = b.mixc(color, tile_col, tile_mask)

    # --- Roughness ---------------------------------------------------------------------------------------------
    tile_rough = b.mixf(GLAZE_ROUGHNESS, 0.55, scratches)
    tile_rough = b.mixf(tile_rough, 0.85, peel)
    tile_rough = b.mixf(tile_rough, 0.80, dirt)
    rough = b.mixf(b.mixf(0.90, 0.97, substrate_mask), tile_rough, tile_mask)

    # --- Normals: grout, chips, cracks first, then scratches and fine detail -------------------------------------
    grout_level = 0.60 - 0.25 * dmg
    grout_h = b.op("SUBTRACT", grout_level, b.op("MULTIPLY", sand, 0.12))
    substrate_h = b.op("MULTIPLY_ADD", sub_noise, 0.15, 0.05)
    waviness = b.noise(tile_pos, 1.2, detail=3.0).outputs["Fac"]
    tile_h = b.op("ADD", b.op("MULTIPLY_ADD", profile, 0.25, 0.75),
                  b.op("MULTIPLY", b.op("SUBTRACT", waviness, 0.5), 0.06))
    tile_h = b.op("SUBTRACT", tile_h, b.op("MULTIPLY", cracks, 0.5))
    height = b.mixf(b.mixf(grout_h, substrate_h, substrate_mask), tile_h, tile_mask)
    n1 = bump(b, height, 1.0, TILE_RELIEF)

    fine = b.op("MULTIPLY", b.op("MULTIPLY", grain, b.op("SUBTRACT", 1.0, tile_mask)), 0.6)
    fine = b.op("ADD", fine, b.op("MULTIPLY", peel, 0.15))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", scratches, 0.30))
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
    material = build_tile()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
