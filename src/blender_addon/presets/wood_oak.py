"""
Oak: golden-brown planks with cathedral grain, open pores and ray flecks.

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
OAK_RING_FREQ = 11.0
OAK_PORE_YZ = 140.0        # pore fineness across the grain
OAK_PORE_RANGE = (0.36, 0.47)  # noise range that becomes open pores
OAK_RAY_DENSITY = 0.18     # medullary ray flecks


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


def flecks(b, pos, x_scale, yz_scale, density, size):
    """Sparse short dashes along the grain (pith flecks, medullary rays)."""
    s = b.mapping(pos, scale=(x_scale, yz_scale, yz_scale))
    vor = b.voronoi(s, 1.0)
    cell = b.sepcol(vor.outputs["Color"])
    on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - density)
    dash = b.remap(vor.outputs["Distance"], 0.0, size, 1.0, 0.0, smooth=True)
    return b.op("MULTIPLY", dash, on)


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_oak():
    mat, b, pos = new_material("Wood_Oak")

    ring, rand = wood_rings(b, pos, OAK_RING_FREQ, tilt=3.0,
                            axis_offset=(0.0, -0.7, 0.4), warp=0.05)
    ring_col = ring_colors(b, ring, rand, [
        (0.00, (0.54, 0.33, 0.14, 1.0)),
        (0.60, (0.47, 0.27, 0.105, 1.0)),
        (0.90, (0.30, 0.155, 0.058, 1.0)),
        (1.00, (0.24, 0.12, 0.045, 1.0)),
    ], contrast=0.4)
    fib = fibers(b, pos, 1.0, 70.0, detail=8.0, rough=0.6, distortion=0.3)
    tone = b.noise(pos, 1.4, detail=3.0).outputs["Fac"]
    early = b.remap(ring, 0.0, 0.55, 1.0, 0.0)
    pore_mask = pores(b, pos, 22.0, OAK_PORE_YZ, OAK_PORE_RANGE[0], OAK_PORE_RANGE[1],
                      mask=early)
    ray = flecks(b, pos, 5.0, 60.0, OAK_RAY_DENSITY, 0.3)

    color = b.mixc(ring_col, (0.60, 0.39, 0.18, 1.0), b.remap(tone, 0.35, 0.65, 0.0, 0.35))
    color = b.mixc(color, (0.20, 0.10, 0.04, 1.0), b.remap(fib, 0.45, 0.20, 0.0, 0.35))
    color = b.mixc(color, (0.66, 0.44, 0.22, 1.0), b.op("MULTIPLY", ray, 0.45))
    color = b.mixc(color, (0.10, 0.045, 0.018, 1.0), b.op("MULTIPLY", pore_mask, 0.85))

    rough = b.mixf(b.op("MULTIPLY_ADD", fib, 0.15, 0.50), 0.85, pore_mask)

    # Normals: ring and fiber relief first, then open pores and rays
    relief = b.op("ADD", b.op("MULTIPLY", ring, 0.35), b.op("MULTIPLY", fib, 0.3))
    n1 = bump(b, relief, 1.0, 0.004)
    detail = b.op("SUBTRACT", b.op("MULTIPLY", ray, 0.2), pore_mask)
    n2 = bump(b, detail, 1.0, 0.003, normal=n1)

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
    material = build_oak()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
