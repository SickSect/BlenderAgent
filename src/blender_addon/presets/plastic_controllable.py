"""
Controllable molded plastic for Blender 3.4+ / 4.x.

Injection-molded plastic (device casing, toy, bucket) with a fine mold grain.
Everything is driven by the "SLIDERS" block below. Change a constant, run the
script again, and the material is rebuilt.

    PLASTIC_COLOR_HEX  main color of the plastic   ("#RRGGBB", sRGB)
    WEAR_AMOUNT        0 = new, uniform surface    1 = heavily used: pale scuffs
                       and scratches, dull patches, grime, yellowed / faded color
    CRACKS_AMOUNT      0 = intact                  1 = brittle and broken: long
                       stress cracks and star-shaped impact cracks with dents,
                       each crack surrounded by a pale "stress whitening" halo

Quick looks to try:
    new casing         : WEAR 0.0, CRACKS 0.0
    old office device  : WEAR 0.5, CRACKS 0.1
    dropped toy        : WEAR 0.6, CRACKS 0.5
    brittle old bucket : WEAR 1.0, CRACKS 1.0

Usage: open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a plane is created. It works best on flat or gently curved parts: the
star-shaped impact cracks are projected along the object's Z axis.
"""

import math

import bpy

# ----------------------------------------------------------------------
# SLIDERS (the values you will change most often)
# ----------------------------------------------------------------------
PLASTIC_COLOR_HEX = "#2F6FB0"   # main plastic color, sRGB hex
WEAR_AMOUNT = 0.4               # 0..1  scuffs, dull patches, grime, yellowing
CRACKS_AMOUNT = 0.3             # 0..1  stress cracks and impact cracks

# ----------------------------------------------------------------------
# Fine tuning
# ----------------------------------------------------------------------
MATERIAL_NAME = "Plastic_Controllable"
PLANE_SIZE = 2.0             # meters, only used when a plane is created
GLOBAL_SCALE = 1.0           # scales every pattern at once (bigger = smaller details)

PLASTIC_ROUGHNESS = 0.35     # 0.1 = glossy, 0.6 = matte
GRAIN_STRENGTH = 0.4         # 0 = perfectly smooth, 1 = strongly textured mold surface
CRACK_RELIEF = 0.004         # normal-map strength of cracks and dents (Bump distance)
DETAIL_STRENGTH = 1.0        # strength of the fine normal detail
IMPACT_GRID_SCALE = 2.0      # impact points per meter (bigger = smaller, more frequent impacts)

# Long stress cracks: (rotation in degrees, stretch along Y, line width, coverage noise scale)
# CRACKS_AMOUNT decides how many of these layers are used.
LONG_CRACKS = [
    (20.0, 5.0, 0.010, 2.5),
    (75.0, 5.5, 0.009, 3.0),
    (130.0, 4.5, 0.009, 3.5),
]

# Scuffs and scratches: same format. WEAR_AMOUNT decides how many layers are used.
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


def lighten(color, amount):
    """Move a linear color toward white (amount 0 = same, 1 = white)."""
    return tuple(c + (1.0 - c) * amount for c in color[:3]) + (1.0,)


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


def layered_lines(b, pos, specs, amount, width_scale=1.0, base_lo=0.70, spread=0.30,
                  x_scale=1.5, distortion=0.15):
    """
    Combine scratch-like layers; more amount = more layers, wider lines and
    wider coverage. Returns a 0..1 mask socket, or the constant 0.0.
    """
    a = clamp01(amount)
    if a <= 0.0:
        return 0.0
    count = max(1, round(1 + (len(specs) - 1) * a))
    cover_lo = base_lo - spread * a
    cover_hi = cover_lo + 0.10
    width_mul = (0.6 + 0.8 * a) * width_scale
    result = None
    for rot, stretch, width, cover_scale in specs[:count]:
        layer = scratch_layer(b, pos, rot, stretch, width * width_mul, cover_scale,
                              cover_lo, cover_hi, x_scale=x_scale, distortion=distortion)
        result = layer if result is None else b.op("MAXIMUM", result, layer)
    return result


def impact_cracks(b, pos, crack):
    """
    Star-shaped cracks around random impact points. The plane is cut into a grid
    (IMPACT_GRID_SCALE cells per meter); a share of the cells gets an impact point.
    Returns (crack lines, wide halo around them, dent at the impact point).
    """
    s = IMPACT_GRID_SCALE
    grid = b.mapping(pos, scale=(s, s, s))
    sep = b.sepxyz(grid)
    gx, gy = sep.outputs["X"], sep.outputs["Y"]
    cell = b.xyz(b.op("FLOOR", gx), b.op("FLOOR", gy), 0.0)

    rnd = b._new("ShaderNodeTexWhiteNoise")
    rnd.noise_dimensions = "3D"
    b._feed(rnd.inputs["Vector"], cell)
    parts = b.sepcol(rnd.outputs["Color"])
    gate = b.op("GREATER_THAN", rnd.outputs["Value"], 1.0 - (0.08 + 0.55 * crack))

    # Impact point inside the cell (kept away from the cell border)
    ix = b.op("MULTIPLY_ADD", parts.outputs["Red"], 0.4, 0.3)
    iy = b.op("MULTIPLY_ADD", parts.outputs["Green"], 0.4, 0.3)
    dx = b.op("SUBTRACT", b.op("FRACT", gx), ix)
    dy = b.op("SUBTRACT", b.op("FRACT", gy), iy)
    r = b.op("SQRT", b.op("ADD", b.op("MULTIPLY", dx, dx), b.op("MULTIPLY", dy, dy)))
    inv_r = b.op("DIVIDE", 1.0, b.op("MAXIMUM", r, 0.001))
    dir_x = b.op("MULTIPLY", dx, inv_r)
    dir_y = b.op("MULTIPLY", dy, inv_r)

    # Noise sampled on the direction (angle) with a slow change along the radius
    # gives lines that run away from the impact point.
    seed = b.op("MULTIPLY", parts.outputs["Blue"], 20.0)
    vec = b.xyz(b.op("MULTIPLY", dir_x, 3.0), b.op("MULTIPLY", dir_y, 3.0),
                b.op("ADD", b.op("MULTIPLY", r, 2.0), seed))
    n = b.noise(vec, 1.0, detail=2.0, rough=0.5).outputs["Fac"]
    dist_from_mid = b.op("ABSOLUTE", b.op("SUBTRACT", n, 0.5))

    radius = 0.12 + 0.17 * crack      # max 0.29 < 0.30, so a star never crosses its cell border
    taper = b.op("MULTIPLY", b.remap(r, 0.0, radius, 1.0, 0.25), 0.045 + 0.02 * crack)
    fade = b.remap(r, radius * 0.6, radius, 1.0, 0.0)
    star = b.op("MULTIPLY", b.op("MULTIPLY", b.remap(dist_from_mid, 0.0, taper, 1.0, 0.0), fade), gate)
    halo = b.op("MULTIPLY", b.op("MULTIPLY",
                                 b.remap(dist_from_mid, 0.0, b.op("MULTIPLY", taper, 3.0), 1.0, 0.0),
                                 fade), gate)
    dent = b.op("MULTIPLY", b.remap(r, 0.0, 0.03, 1.0, 0.0, smooth=True), gate)
    return star, halo, dent


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_plastic():
    mat, b, pos = new_material(MATERIAL_NAME)

    wear = clamp01(WEAR_AMOUNT)
    crack = clamp01(CRACKS_AMOUNT)
    grain_k = clamp01(GRAIN_STRENGTH)
    base = hex_to_linear(PLASTIC_COLOR_HEX)
    pale = lighten(base, 0.5)                    # scuffs and stress whitening
    yellowed = (min(1.0, base[0] * 1.15 + 0.05), min(1.0, base[1] * 1.05 + 0.03),
                base[2] * 0.7, 1.0)              # warm shift of aged plastic

    # --- Common noises ----------------------------------------------------------------
    grain = b.noise(pos, 180.0, detail=4.0, rough=0.7).outputs["Fac"]
    stipple = b.voronoi(pos, 140.0).outputs["Distance"]
    flow = b.noise(pos, 2.5, detail=3.0, rough=0.5, distortion=0.3).outputs["Fac"]
    mottle = b.noise(pos, 5.0, detail=5.0, rough=0.6, distortion=0.2).outputs["Fac"]
    soft = b.noise(pos, 3.5, detail=6.0, rough=0.6, distortion=0.4).outputs["Fac"]

    # --- Wear (WEAR_AMOUNT) -------------------------------------------------------------------
    scuffs = layered_lines(b, pos, SCUFF_LAYERS, wear)
    if wear <= 0.0:
        dull = 0.0
    else:
        dull_lo = 0.62 - 0.30 * wear
        dull_noise = b.noise(pos, 1.8, detail=5.0, rough=0.6, distortion=0.4).outputs["Fac"]
        dull = b.remap(dull_noise, dull_lo, dull_lo + 0.12, 0.0, 1.0, smooth=True)
    dirt = b.op("MULTIPLY", b.remap(soft, 0.45, 0.80, 0.0, 1.0), 0.8 * wear)
    age_noise = b.noise(pos, 1.6, detail=4.0, rough=0.6, distortion=0.3).outputs["Fac"]
    aged = b.op("MULTIPLY", b.remap(age_noise, 0.35, 0.75, 0.0, 1.0), 0.45 * wear)

    # --- Cracks (CRACKS_AMOUNT) -----------------------------------------------------------------
    if crack <= 0.0:
        core = halo = dent = 0.0
    else:
        long_core = layered_lines(b, pos, LONG_CRACKS, crack, base_lo=0.64, spread=0.28,
                                  x_scale=3.0, distortion=0.5)
        long_halo = layered_lines(b, pos, LONG_CRACKS, crack, width_scale=3.5,
                                  base_lo=0.64, spread=0.28, x_scale=3.0, distortion=0.5)
        star, star_halo, dent = impact_cracks(b, pos, crack)
        core = b.op("MAXIMUM", long_core, star)
        halo = b.op("MAXIMUM", long_halo, star_halo)

    # --- Color ---------------------------------------------------------------------------------------
    color = b.mixc(shade(base, 0.92), shade(base, 1.08), b.remap(mottle, 0.35, 0.70, 0.0, 1.0))
    color = b.mixc(color, yellowed, aged)
    color = b.mixc(color, shade(base, 0.25), b.op("MULTIPLY", dirt, 0.5))
    color = b.mixc(color, pale, b.op("MULTIPLY", dull, 0.25))
    color = b.mixc(color, pale, b.op("MULTIPLY", scuffs, 0.6))
    color = b.mixc(color, pale, b.op("MULTIPLY", halo, 0.45))            # stress whitening
    color = b.mixc(color, shade(base, 0.06), b.op("MULTIPLY", core, 0.95))

    # --- Roughness ---------------------------------------------------------------------------------------
    rough = b.op("MULTIPLY_ADD", grain, 0.08, PLASTIC_ROUGHNESS - 0.04)
    rough = b.mixf(rough, 0.70, dull)
    rough = b.mixf(rough, 0.60, scuffs)
    rough = b.mixf(rough, 0.80, halo)
    rough = b.mixf(rough, 0.90, core)

    # --- Normals: cracks and dents first, then mold grain and scratches ------------------------------------
    big = b.op("SUBTRACT", b.op("SUBTRACT", 1.0, b.op("MULTIPLY", core, 0.5)),
               b.op("MULTIPLY", dent, 0.5))
    big = b.op("ADD", big, b.op("MULTIPLY", flow, 0.15))
    n1 = bump(b, big, 1.0, CRACK_RELIEF)

    fine = b.op("ADD", b.op("MULTIPLY", grain, 0.4 * grain_k), b.op("MULTIPLY", stipple, 0.6 * grain_k))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", scuffs, 0.35))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", core, 0.30))
    n2 = bump(b, fine, DETAIL_STRENGTH, 0.002, normal=n1)

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
    material = build_plastic()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
