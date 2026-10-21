"""
Tree cross-section: growth rings, heartwood/sapwood, radial cracks, ragged bark.
The preview object is a cylinder (radius PLANE_SIZE / 2); the pattern assumes
an object radius of 1, so keep it unscaled when you use your own mesh.

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
SLICE_RING_FREQ = 16.0     # growth rings per unit of radius
SLICE_WARP = 0.05          # ring irregularity
SLICE_BARK_START = 0.90    # radius where the bark begins (object radius is 1)
SLICE_CRACK_DIR = 1.3      # number of radial cracks (~6x this value)
SLICE_CRACK_WIDTH = 0.035
SLICE_RAY_DIR = 26.0       # fineness of radial rays
SLICE_HEART_STOPS = [
    (0.0, (0.42, 0.23, 0.09, 1.0)),
    (0.65, (0.36, 0.18, 0.07, 1.0)),
    (1.0, (0.20, 0.09, 0.035, 1.0)),
]
SLICE_SAP_STOPS = [
    (0.0, (0.74, 0.54, 0.31, 1.0)),
    (0.65, (0.66, 0.46, 0.25, 1.0)),
    (1.0, (0.46, 0.28, 0.12, 1.0)),
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


def ring_rand(b, ring_index):
    """Random 0..1 value per growth ring."""
    return b.white1d(b.op("MULTIPLY", ring_index, 3.7311))


def ring_colors(b, ring, rand, stops, contrast=0.3):
    """Early wood -> late wood gradient with per-ring contrast variation."""
    factor = b.op("MULTIPLY_ADD", rand, contrast, 1.0 - contrast * 0.5)
    return b.ramp(b.op("MULTIPLY", ring, factor), stops)


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


def radial_lines(b, dir_x, dir_y, r, k_dir, k_r, width,
                 cover_lo=None, cover_hi=None, seed=0.0):
    """Lines running radially from the centre (cracks, medullary rays)."""
    vec = b.xyz(b.op("MULTIPLY", dir_x, k_dir),
                b.op("MULTIPLY", dir_y, k_dir),
                b.op("MULTIPLY_ADD", r, k_r, seed))
    n = b.noise(vec, 1.0, detail=2.0, rough=0.5, distortion=0.2).outputs["Fac"]
    line = b.remap(b.op("ABSOLUTE", b.op("SUBTRACT", n, 0.5)), 0.0, width, 1.0, 0.0)
    if cover_lo is None:
        return line
    cover = b.noise(vec, 0.45, detail=3.0).outputs["Fac"]
    return b.op("MULTIPLY", line, b.remap(cover, cover_lo, cover_hi, 0.0, 1.0))


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_slice():
    mat, b, pos = new_material("Wood_Slice", scaled=False)

    sep = b.sepxyz(pos)
    x, y = sep.outputs["X"], sep.outputs["Y"]
    r_raw = b.op("SQRT", b.op("ADD", b.op("MULTIPLY", x, x), b.op("MULTIPLY", y, y)))
    safe_r = b.op("MAXIMUM", r_raw, 0.001)
    dir_x = b.op("DIVIDE", x, safe_r)
    dir_y = b.op("DIVIDE", y, safe_r)

    # --- Off-centre, slightly irregular growth rings ---
    shifted = b.mapping(pos, loc=(0.06, -0.04, 0.0))
    sep_c = b.sepxyz(shifted)
    cx, cy = sep_c.outputs["X"], sep_c.outputs["Y"]
    r_c = b.op("SQRT", b.op("ADD", b.op("MULTIPLY", cx, cx), b.op("MULTIPLY", cy, cy)))
    wobble = b.noise(pos, 1.6, detail=4.0, rough=0.55, distortion=0.3).outputs["Fac"]
    r_ring = b.op("ADD", r_c, b.op("MULTIPLY", b.op("SUBTRACT", wobble, 0.5), SLICE_WARP))
    t = b.op("MULTIPLY", r_ring, SLICE_RING_FREQ)
    ring = b.op("FRACT", t)
    rand = ring_rand(b, b.op("FLOOR", t))

    # --- Heartwood (dark) to sapwood (light) ---
    heart = ring_colors(b, ring, rand, SLICE_HEART_STOPS, contrast=0.45)
    sap = ring_colors(b, ring, rand, SLICE_SAP_STOPS, contrast=0.45)
    heart_to_sap = b.remap(r_raw, 0.45, 0.80, 0.0, 1.0, smooth=True)
    wood_col = b.mixc(heart, sap, heart_to_sap)

    # --- Bark ring with ragged edge and furrows ---
    edge_noise = b.noise(pos, 28.0, detail=4.0).outputs["Fac"]
    bark_r = b.op("ADD", r_raw, b.op("MULTIPLY", b.op("SUBTRACT", edge_noise, 0.5), 0.03))
    bark = b.remap(bark_r, SLICE_BARK_START, SLICE_BARK_START + 0.012, 0.0, 1.0, smooth=True)
    inv_bark = b.op("SUBTRACT", 1.0, bark)
    plate_vec = b.mapping(pos, scale=(6.0, 6.0, 1.2))
    plate_edge = b.voronoi(plate_vec, 1.0, feature="DISTANCE_TO_EDGE").outputs["Distance"]
    furrow = b.remap(plate_edge, 0.0, 0.16, 0.0, 1.0, smooth=True)
    bark_col = b.ramp(furrow, [
        (0.0, (0.012, 0.009, 0.007, 1.0)),
        (0.5, (0.06, 0.043, 0.032, 1.0)),
        (1.0, (0.18, 0.14, 0.11, 1.0)),
    ])

    # --- Radial cracks, radial rays, pith, saw marks ---
    cracks = radial_lines(b, dir_x, dir_y, r_raw, SLICE_CRACK_DIR, 0.5, SLICE_CRACK_WIDTH,
                          0.50, 0.66, seed=3.0)
    cracks = b.op("MULTIPLY", cracks, b.remap(r_raw, 0.05, 0.25, 0.0, 1.0))
    cracks = b.op("MULTIPLY", cracks, inv_bark)
    rays = radial_lines(b, dir_x, dir_y, r_raw, SLICE_RAY_DIR, 0.8, 0.10, seed=7.0)
    pith = b.remap(r_c, 0.0, 0.025, 1.0, 0.0, smooth=True)
    saw_vec = b.mapping(pos, scale=(1.0, 100.0, 1.0), rot_deg=(0, 0, 25))
    saw = b.noise(saw_vec, 1.0, detail=3.0, rough=0.5).outputs["Fac"]
    speck = b.noise(pos, 140.0, detail=4.0).outputs["Fac"]

    # --- Color ---
    color = b.mixc(wood_col, (0.12, 0.06, 0.03, 1.0), b.remap(speck, 0.50, 0.30, 0.0, 0.35))
    color = b.mixc(color, (0.85, 0.62, 0.38, 1.0), b.op("MULTIPLY", rays, 0.18))
    color = b.mixc(color, (0.10, 0.045, 0.02, 1.0), b.op("MULTIPLY", pith, 0.85))
    color = b.mixc(color, (0.03, 0.015, 0.007, 1.0), b.op("MULTIPLY", cracks, 0.9))
    color = b.mixc(color, bark_col, bark)

    # --- Roughness: end grain is porous and rough ---
    wood_rough = b.op("MULTIPLY_ADD", saw, 0.20, 0.70)
    rough = b.mixf(wood_rough, 0.95, bark)

    # --- Normals: bark and cracks first, then rings, saw marks and pores ---
    big = b.op("SUBTRACT", b.op("MULTIPLY", bark, b.op("MULTIPLY_ADD", furrow, 0.9, 0.5)),
               b.op("MULTIPLY", cracks, 1.1))
    n1 = bump(b, big, 1.0, 0.02)
    fine = b.op("MULTIPLY", ring, 0.22)
    fine = b.op("ADD", fine, b.op("MULTIPLY", b.op("MULTIPLY", saw, 0.25), inv_bark))
    fine = b.op("ADD", fine, b.op("MULTIPLY", b.op("MULTIPLY", speck, 0.2), inv_bark))
    fine = b.op("SUBTRACT", fine, b.op("MULTIPLY", pith, 0.1))
    n2 = bump(b, fine, 1.0, 0.004, normal=n1)

    finish(b, color, 0.0, rough, n2)
    return mat


def assign_material(mat):
    """Assign to the active mesh object, or create a preview log slice."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        bpy.ops.mesh.primitive_cylinder_add(radius=PLANE_SIZE / 2, depth=0.25, vertices=96)
        obj = bpy.context.active_object

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


if __name__ == "__main__":
    material = build_slice()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")