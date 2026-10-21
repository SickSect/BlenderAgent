"""
Dry wood with peeling bark: deep-furrowed bark patches over bare weathered wood.

Standalone script for Blender 3.4+ / 4.x.
Open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a preview object is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# Parameters (tweak these)
# ----------------------------------------------------------------------
PLANE_SIZE = 2.0           # meters, used only when a preview object is created
GLOBAL_SCALE = 1.0         # scales every pattern at once (the tree slice ignores it)
PEEL_RING_FREQ = 9.0
BARK_THRESHOLD = 0.50      # higher = more bark has peeled off
BARK_PLATE_STRETCH = (0.7, 5.0)  # bark plate size (along grain, across grain)
BARK_HEIGHT = 0.02         # bark relief (Bump distance)
PEEL_CRACKS = [
    (0.0, 10.0, 0.060, 2.5, 0.58, 0.68),
    (3.0, 16.0, 0.045, 3.5, 0.60, 0.70),
]


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


def scratch_set(b, pos, specs, **kwargs):
    """Combine several scratch / crack layers with a Maximum."""
    result = None
    for rot, stretch, width, cover_scale, lo, hi in specs:
        layer = scratch_layer(b, pos, rot, stretch, width, cover_scale, lo, hi, **kwargs)
        result = layer if result is None else b.op("MAXIMUM", result, layer)
    return result


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


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_peeling_bark():
    mat, b, pos = new_material("Wood_Dry_Bark")

    # --- Bare weathered wood under the bark ---
    ring, rand = wood_rings(b, pos, PEEL_RING_FREQ, tilt=3.0,
                            axis_offset=(0.0, -0.5, 0.4), warp=0.07)
    wood_col = ring_colors(b, ring, rand, [
        (0.00, (0.60, 0.50, 0.38, 1.0)),
        (0.60, (0.52, 0.42, 0.31, 1.0)),
        (1.00, (0.30, 0.23, 0.17, 1.0)),
    ], contrast=0.5)
    fib = fibers(b, pos, 1.0, 45.0, detail=10.0, rough=0.65, distortion=0.4)
    cracks = scratch_set(b, pos, PEEL_CRACKS, x_scale=0.5, distortion=0.1)

    # --- Where the bark is still attached (ragged fibrous edge) ---
    cover_noise = b.noise(pos, 2.2, detail=6.0, rough=0.6, distortion=0.4).outputs["Fac"]
    ragged = b.noise(b.mapping(pos, scale=(1.0, 25.0, 25.0)), 1.0, detail=4.0).outputs["Fac"]
    cover_val = b.op("MULTIPLY_ADD", b.op("SUBTRACT", ragged, 0.5), 0.18, cover_noise)
    bark = b.remap(cover_val, BARK_THRESHOLD, BARK_THRESHOLD + 0.03, 0.0, 1.0, smooth=True)
    stain = b.remap(cover_val, BARK_THRESHOLD - 0.12, BARK_THRESHOLD, 0.0, 1.0)
    rim = b.op("MULTIPLY", b.op("MULTIPLY", bark, b.op("SUBTRACT", 1.0, bark)), 4.0)
    inv_bark = b.op("SUBTRACT", 1.0, bark)

    # --- Bark plates: elongated Voronoi, cell edges become deep furrows ---
    warp = b.noise(pos, 2.0, detail=2.0).outputs["Color"]
    warped = b.vop("MULTIPLY_ADD", warp, (0.25, 0.25, 0.25), pos)
    plate_vec = b.mapping(warped, scale=(BARK_PLATE_STRETCH[0],
                                         BARK_PLATE_STRETCH[1],
                                         BARK_PLATE_STRETCH[1]))
    cells = b.voronoi(plate_vec, 1.0)
    plate_rand = b.sepcol(cells.outputs["Color"]).outputs["Red"]
    edge_dist = b.voronoi(plate_vec, 1.0, feature="DISTANCE_TO_EDGE").outputs["Distance"]
    furrow = b.remap(edge_dist, 0.0, 0.18, 0.0, 1.0, smooth=True)
    relief = b.op("MULTIPLY", furrow, b.op("MULTIPLY_ADD", plate_rand, 0.3, 0.7))
    bark_fine = fibers(b, pos, 1.0, 60.0, detail=8.0, rough=0.7)

    # --- Color ---
    wood_col = b.mixc(wood_col, (0.62, 0.58, 0.52, 1.0), b.remap(fib, 0.6, 0.85, 0.0, 0.4))
    wood_col = b.mixc(wood_col, (0.20, 0.12, 0.06, 1.0), b.op("MULTIPLY", stain, 0.5))
    wood_col = b.mixc(wood_col, (0.03, 0.025, 0.02, 1.0), cracks)
    bark_col = b.ramp(relief, [
        (0.0, (0.015, 0.011, 0.009, 1.0)),
        (0.5, (0.07, 0.05, 0.04, 1.0)),
        (1.0, (0.20, 0.16, 0.13, 1.0)),
    ])
    bark_col = b.mixc(bark_col, (0.22, 0.19, 0.16, 1.0), b.remap(bark_fine, 0.55, 0.80, 0.0, 0.4))
    color = b.mixc(wood_col, bark_col, bark)
    color = b.mixc(color, (0.42, 0.28, 0.15, 1.0), b.op("MULTIPLY", rim, 0.6))

    # --- Roughness ---
    wood_rough = b.op("MULTIPLY_ADD", fib, 0.12, 0.84)
    bark_rough = b.op("MULTIPLY_ADD", bark_fine, 0.05, 0.92)
    rough = b.mixf(wood_rough, bark_rough, bark)

    # --- Normals: bark thickness and furrows, then fine detail ---
    big = b.op("ADD", b.op("MULTIPLY", ring, 0.15),
               b.op("MULTIPLY", bark, b.op("MULTIPLY_ADD", relief, 0.9, 0.5)))
    n1 = bump(b, big, 1.0, BARK_HEIGHT)
    wood_detail = b.op("SUBTRACT", b.op("MULTIPLY", fib, 0.4), b.op("MULTIPLY", cracks, 1.0))
    detail = b.op("MULTIPLY", wood_detail, inv_bark)
    detail = b.op("ADD", detail, b.op("MULTIPLY", b.op("MULTIPLY", bark_fine, 0.3), bark))
    n2 = bump(b, detail, 1.0, 0.005, normal=n1)

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
    material = build_peeling_bark()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
