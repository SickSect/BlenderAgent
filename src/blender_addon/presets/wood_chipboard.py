"""
Raw chipboard (DSP): pressed wood chips of several sizes, gaps and resin specks.

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
CHIP_LAYERS = [
    # (voronoi scale, rotation deg, squash), large chunks first
    (6.0, 20.0, 0.50),
    (18.0, 70.0, 0.55),
    (45.0, 130.0, 0.60),
]
CHIP_STOPS = [
    (0.0, (0.33, 0.19, 0.09, 1.0)),
    (0.5, (0.52, 0.33, 0.16, 1.0)),
    (1.0, (0.68, 0.50, 0.30, 1.0)),
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


def chip_layer(b, pos, scale, rot, squash):
    """One layer of wood chips: (random value per chip, gap mask between chips)."""
    vec = b.mapping(pos, scale=(1.0, squash, 1.0), rot_deg=(0, 0, rot))
    cells = b.voronoi(vec, scale)
    rnd = b.sepcol(cells.outputs["Color"]).outputs["Red"]
    edge = b.voronoi(vec, scale, feature="DISTANCE_TO_EDGE").outputs["Distance"]
    return rnd, b.remap(edge, 0.0, 0.06, 1.0, 0.0)


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_chipboard():
    mat, b, pos = new_material("Wood_Chipboard")

    (r1, g1), (r2, g2), (r3, g3) = [chip_layer(b, pos, *spec) for spec in CHIP_LAYERS]

    # Where the medium and large chips take over from the fine ones
    m1 = b.remap(b.noise(pos, 2.5, detail=3.0).outputs["Fac"], 0.55, 0.65, 0.0, 1.0)
    offset = b.mapping(pos, loc=(3.1, 1.7, 0.0))
    m2 = b.remap(b.noise(offset, 4.0, detail=3.0).outputs["Fac"], 0.42, 0.55, 0.0, 1.0)

    col3 = b.ramp(r3, CHIP_STOPS)
    col2 = b.ramp(r2, CHIP_STOPS)
    col1 = b.ramp(r1, CHIP_STOPS)
    color = b.mixc(b.mixc(col3, col2, m2), col1, m1)
    gap = b.mixf(b.mixf(g3, g2, m2), g1, m1)
    chip_h = b.mixf(b.mixf(r3, r2, m2), r1, m1)

    # Light resin / glue specks
    speck_vor = b.voronoi(pos, 140.0)
    speck_on = b.op("GREATER_THAN", b.sepcol(speck_vor.outputs["Color"]).outputs["Red"], 0.93)
    speck = b.op("MULTIPLY", b.remap(speck_vor.outputs["Distance"], 0.0, 0.25, 1.0, 0.0), speck_on)

    color = b.mixc(color, (0.05, 0.03, 0.015, 1.0), b.op("MULTIPLY", gap, 0.85))
    color = b.mixc(color, (0.80, 0.66, 0.46, 1.0), b.op("MULTIPLY", speck, 0.7))

    rough = b.op("MULTIPLY_ADD", r3, 0.15, 0.80)
    rough = b.mixf(rough, 1.0, gap)

    height = b.op("SUBTRACT", b.op("MULTIPLY", chip_h, 0.5), b.op("MULTIPLY", gap, 0.9))
    n1 = bump(b, height, 1.0, 0.006)
    grain = b.noise(pos, 160.0, detail=5.0).outputs["Fac"]
    n2 = bump(b, grain, 0.8, 0.0015, normal=n1)

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
    material = build_chipboard()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")
