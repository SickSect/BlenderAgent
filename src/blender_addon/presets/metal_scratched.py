"""
Heavily scratched metal: dense fine scratches plus deep gouges.

Standalone script for Blender 3.4+ / 4.x.
Open the Scripting tab, load this file, press Run Script.
The material is assigned to the active mesh object; if there is none,
a preview object is created.
"""

import math

import bpy

# ----------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------
PLANE_SIZE = 2.0           # meters, used only when a preview object is created
GLOBAL_SCALE = 1.0         # scales every pattern at once
BRUSH_STRETCH = 120.0      # higher = finer brush lines in the base metal

# --- Scratched ---
FINE_SCRATCHES = [
    (rot, 18.0 + (i * 7) % 17, 0.030, 5.0 + (i % 3), 0.46, 0.58)
    for i, rot in enumerate([0, 22, 47, 68, 93, 115, 138, 161])
]
DEEP_SCRATCHES = [
    (15.0, 5.0, 0.050, 2.5, 0.60, 0.68),
    (100.0, 6.5, 0.050, 3.0, 0.62, 0.70),
    (150.0, 4.0, 0.060, 2.0, 0.64, 0.72),
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

    def dot(self, a, b):
        node = self._new("ShaderNodeVectorMath")
        node.operation = "DOT_PRODUCT"
        self._feed(node.inputs[0], a)
        self._feed(node.inputs[1], b)
        return node.outputs["Value"]

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
# Building blocks
# ----------------------------------------------------------------------
def new_material(name):
    """Create an empty material and return (material, builder, object-space position)."""
    old = bpy.data.materials.get(name)
    if old:
        bpy.data.materials.remove(old)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    b = Builder(mat.node_tree)
    tex = b._new("ShaderNodeTexCoord")
    pos = b.mapping(tex.outputs["Object"], scale=(GLOBAL_SCALE,) * 3)
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
    """Thin stretched noise contours masked by a low-frequency coverage noise."""
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
    """Combine several scratch layers with a Maximum."""
    result = None
    for rot, stretch, width, cover_scale, lo, hi in specs:
        layer = scratch_layer(b, pos, rot, stretch, width, cover_scale, lo, hi, **kwargs)
        result = layer if result is None else b.op("MAXIMUM", result, layer)
    return result


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_scratched():
    mat, b, pos = new_material("Metal_Scratched")

    # Satin base with faint brushing
    s1 = b.mapping(pos, scale=(2.0, BRUSH_STRETCH, 2.0))
    brush = b.noise(s1, 1.0, detail=6.0, rough=0.6).outputs["Fac"]
    haze = b.noise(pos, 3.5, detail=6.0, rough=0.55, distortion=0.5).outputs["Fac"]

    fine = scratch_set(b, pos, FINE_SCRATCHES, distortion=0.15)
    deep = scratch_set(b, pos, DEEP_SCRATCHES, x_scale=1.0)
    any_scratch = b.op("MAXIMUM", fine, deep)

    # Color: dark steel, scratches expose bright metal
    color = b.mixc((0.30, 0.31, 0.33, 1.0), (0.36, 0.37, 0.39, 1.0), brush)
    color = b.mixc(color, (0.85, 0.85, 0.88, 1.0), b.op("MULTIPLY", fine, 0.6))
    color = b.mixc(color, (0.70, 0.70, 0.73, 1.0), deep)

    # Roughness: scratches scatter light, so they get rougher than the base
    rough = b.op("MULTIPLY_ADD", brush, 0.10, 0.28)
    rough = b.op("ADD", rough, b.op("MULTIPLY", haze, 0.10))
    rough = b.mixf(rough, 0.50, fine)
    rough = b.mixf(rough, 0.60, deep)

    # Normals: faint brushing first, then fine scratches and deep gouges
    n1 = bump(b, brush, 0.3, 0.002)
    height = b.op("ADD", b.op("MULTIPLY", fine, -0.25), b.op("MULTIPLY", deep, -1.0))
    n2 = bump(b, height, 1.0, 0.004, normal=n1)

    finish(b, color, 1.0, rough, n2)
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
    material = build_scratched()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")