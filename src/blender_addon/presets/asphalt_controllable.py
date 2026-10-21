"""
Controllable worn asphalt for Blender 3.4+ / 4.x.

Dark bitumen with embedded stone chips, pores, cracks and potholes.
Everything is driven by the "SLIDERS" block below. Change a constant, run
the script again, and the material is rebuilt.

    ASPHALT_COLOR_HEX  main color of the asphalt            ("#RRGGBB", sRGB)
    SPECK_COLOR_HEX    color of the small stone inclusions  ("#RRGGBB", sRGB)
    HOLES_AMOUNT       0 = no holes                  1 = many potholes
    HOLE_VARIATION     0 = all holes the same size   1 = tiny and huge holes mixed
    WEAR               0 = fresh black asphalt       1 = old, cracked, faded,
                                                         stones sticking out

Quick looks to try:
    fresh road        : HOLES 0.0, VARIATION 0.0, WEAR 0.0
    normal old road   : HOLES 0.2, VARIATION 0.5, WEAR 0.5
    patched city road : HOLES 0.5, VARIATION 0.9, WEAR 0.7
    destroyed road    : HOLES 0.9, VARIATION 1.0, WEAR 1.0

Usage: open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a plane is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# SLIDERS (the values you will change most often)
# ----------------------------------------------------------------------
ASPHALT_COLOR_HEX = "#3D3D40"   # main asphalt color, sRGB hex
SPECK_COLOR_HEX = "#A8A298"     # small stone inclusions, sRGB hex
HOLES_AMOUNT = 0.35             # 0..1  amount of holes
HOLE_VARIATION = 0.5            # 0..1  spread of hole sizes
WEAR = 0.5                      # 0..1  overall degree of wear

# ----------------------------------------------------------------------
# Fine tuning
# ----------------------------------------------------------------------
MATERIAL_NAME = "Asphalt_Controllable"
PLANE_SIZE = 2.0             # meters, only used when a plane is created
GLOBAL_SCALE = 1.0           # scales every pattern at once (bigger = smaller details)

HOLE_CELL_SCALE = 2.4        # distance between holes (bigger = smaller, denser holes)
HOLE_DEPTH = 0.04            # normal-map strength of holes and cracks (Bump distance)
STONE_SCALE_SMALL = 75.0     # small chips (bigger = finer)
STONE_SCALE_LARGE = 28.0     # large chips
CRACK_NET_SCALE = 3.5        # size of the crack network cells
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


def stone_layer(b, pos, scale, coverage, mask_edge=(0.30, 0.40), dome_end=0.45):
    """
    Stone chips as Voronoi cells. Only a share of cells (coverage) holds a chip.
    Returns (hard mask, rounded dome height, random tone per chip).
    """
    vor = b.voronoi(pos, scale)
    cell = b.sepcol(vor.outputs["Color"])
    on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - coverage)
    d = vor.outputs["Distance"]
    mask = b.op("MULTIPLY", b.remap(d, mask_edge[0], mask_edge[1], 1.0, 0.0), on)
    dome = b.op("MULTIPLY", b.remap(d, 0.0, dome_end, 1.0, 0.0, smooth=True), on)
    return mask, dome, cell.outputs["Green"]


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_asphalt():
    mat, b, pos = new_material(MATERIAL_NAME)

    wear = clamp01(WEAR)
    holes_amount = clamp01(HOLES_AMOUNT)
    variation = clamp01(HOLE_VARIATION)
    base = hex_to_linear(ASPHALT_COLOR_HEX)
    speck = hex_to_linear(SPECK_COLOR_HEX)

    grain = b.noise(pos, 150.0, detail=6.0, rough=0.7).outputs["Fac"]
    mottle = b.noise(pos, 3.0, detail=6.0, rough=0.6, distortion=0.4).outputs["Fac"]

    # --- Stone chips: small and large, more of them exposed when worn --------
    exposure = 0.35 + 0.65 * wear
    mask_s, dome_s, tone_s = stone_layer(b, pos, STONE_SCALE_SMALL, 0.30 + 0.55 * wear)
    mask_l, dome_l, tone_l = stone_layer(b, pos, STONE_SCALE_LARGE, 0.12 + 0.35 * wear)
    stone_mask = b.op("MAXIMUM", mask_s, mask_l)
    stone_height = b.op("MAXIMUM", b.op("MULTIPLY", dome_s, 0.6), dome_l)
    stone_tone = b.mixf(tone_s, tone_l, mask_l)
    stone_col = b.mixc(shade(speck, 0.55), shade(speck, 1.15), stone_tone)

    # --- Pores (tiny dark pits) ------------------------------------------------
    pits, _, _ = stone_layer(b, pos, 110.0, 0.22, mask_edge=(0.12, 0.22))

    # --- Fading of the bitumen binder (grows with wear) ---------------------------
    bleach = b.op("MULTIPLY", b.remap(mottle, 0.35, 0.70, 0.0, 1.0), 0.8 * wear)

    # --- Holes (potholes) ---------------------------------------------------------
    if holes_amount <= 0.0:
        hole = depth = crumble = 0.0
        inv_hole = 1.0
        mask_h = dome_h = tone_h = 0.0
    else:
        warp = b.noise(pos, 2.0, detail=2.0).outputs["Color"]
        warped = b.vop("MULTIPLY_ADD", warp, (0.30, 0.30, 0.30), pos)
        vor = b.voronoi(warped, HOLE_CELL_SCALE)
        cell = b.sepcol(vor.outputs["Color"])
        density = 0.04 + 0.50 * holes_amount
        cell_on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - density)

        # Per-cell radius: variation 0 = every hole the same, 1 = wide size spread
        size_pick = b.mixf(0.5, cell.outputs["Green"], variation)
        radius = b.op("MULTIPLY_ADD", size_pick, 0.40, 0.10)

        # Ragged outline
        edge_noise = b.noise(pos, 14.0, detail=5.0, rough=0.6).outputs["Fac"]
        ragged = b.op("MULTIPLY", b.op("SUBTRACT", edge_noise, 0.5), 0.22)
        dist = b.op("ADD", vor.outputs["Distance"], ragged)

        hole = b.op("MULTIPLY",
                    b.remap(dist, b.op("SUBTRACT", radius, 0.04), radius, 1.0, 0.0),
                    cell_on)
        depth = b.op("MULTIPLY", b.remap(dist, 0.0, radius, 1.0, 0.0), hole)
        inv_hole = b.op("SUBTRACT", 1.0, hole)

        # Loose crumbs around the edge of every hole
        ring = b.remap(dist, radius, b.op("ADD", radius, 0.10), 1.0, 0.0)
        crumb_noise = b.noise(pos, 60.0, detail=3.0).outputs["Fac"]
        crumb_bits = b.remap(crumb_noise, 0.45, 0.60, 0.0, 1.0)
        crumble = b.op("MULTIPLY", b.op("MULTIPLY", ring, cell_on),
                       b.op("MULTIPLY", inv_hole, crumb_bits))

        # Coarse gravel base layer visible in the bottom of the holes
        mask_h, dome_h, tone_h = stone_layer(b, pos, 20.0, 0.65,
                                             mask_edge=(0.28, 0.40), dome_end=0.5)

    # --- Cracks (appear above 10% wear) ---------------------------------------------
    if wear < 0.1:
        cracks = 0.0
    else:
        crack_amount = (wear - 0.1) / 0.9
        crack_warp = b.noise(pos, 2.5, detail=2.0).outputs["Color"]
        crack_vec = b.vop("MULTIPLY_ADD", crack_warp, (0.25, 0.25, 0.25), pos)
        edge = b.voronoi(crack_vec, CRACK_NET_SCALE, feature="DISTANCE_TO_EDGE")
        width = 0.012 + 0.03 * wear
        line = b.remap(edge.outputs["Distance"], 0.0, width, 1.0, 0.0)
        cover_lo = 0.62 - 0.27 * crack_amount
        cover_noise = b.noise(pos, 2.2, detail=4.0, rough=0.55).outputs["Fac"]
        net = b.op("MULTIPLY", line, b.remap(cover_noise, cover_lo, cover_lo + 0.10, 0.0, 1.0))
        long_crack = scratch_layer(b, pos, 25.0, 6.0, 0.03 + 0.03 * wear, 1.8,
                                   cover_lo - 0.02, cover_lo + 0.10,
                                   x_scale=1.2, distortion=0.6)
        cracks = b.op("MULTIPLY", b.op("MAXIMUM", net, long_crack), inv_hole)

    # --- Color ----------------------------------------------------------------------------
    surface = b.mixc(shade(base, 0.85), shade(base, 1.25), b.remap(mottle, 0.30, 0.70, 0.0, 1.0))
    surface = b.mixc(surface, shade(base, 1.90), bleach)
    surface = b.mixc(surface, stone_col, b.op("MULTIPLY", stone_mask, exposure))
    surface = b.mixc(surface, shade(base, 0.12), b.op("MULTIPLY", pits, 0.8))
    surface = b.mixc(surface, shade(base, 0.08), b.op("MULTIPLY", cracks, 0.95))
    if holes_amount <= 0.0:
        color = surface
    else:
        surface = b.mixc(surface, shade(speck, 0.70), b.op("MULTIPLY", crumble, 0.6))
        floor_stone = b.mixc(shade(speck, 0.45), shade(speck, 0.90), tone_h)
        floor = b.mixc(shade(base, 0.55), floor_stone, b.op("MULTIPLY", mask_h, 0.85))
        floor = b.mixc(floor, shade(base, 0.20), b.op("MULTIPLY", depth, 0.5))
        color = b.mixc(surface, floor, hole)

    # --- Roughness -----------------------------------------------------------------------------
    rough = b.op("MULTIPLY_ADD", grain, 0.10, 0.80 + 0.08 * wear)
    rough = b.mixf(rough, 0.62, b.op("MULTIPLY", stone_mask, 0.6 * exposure))
    rough = b.mixf(rough, 0.97, hole)

    # --- Normals: holes and cracks first, then stones, pores and grain ----------------------------
    h_holes = b.op("ADD", b.op("MULTIPLY", hole, 0.7), b.op("MULTIPLY", depth, 0.5))
    h_big = b.op("SUBTRACT", b.op("SUBTRACT", 1.0, h_holes), b.op("MULTIPLY", cracks, 0.5))
    n1 = bump(b, h_big, 1.0, HOLE_DEPTH)

    stones = b.op("MAXIMUM", b.op("MULTIPLY", stone_height, inv_hole),
                  b.op("MULTIPLY", dome_h, hole))
    fine = b.op("ADD", b.op("MULTIPLY", stones, 0.9), b.op("MULTIPLY", grain, 0.15))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", pits, 0.4))
    fine = b.op("ADD", fine, b.op("MULTIPLY", crumble, 0.25))
    n2 = bump(b, fine, DETAIL_STRENGTH, 0.012, normal=n1)

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
    material = build_asphalt()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
