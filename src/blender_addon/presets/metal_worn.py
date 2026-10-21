"""
Worn / scuffed metal: rubbed patches, grime, pits, shallow dents, edge wear.

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
#   PREVIEW_SHAPE "CUBE" shows edge wear on the created preview object
PREVIEW_SHAPE = "PLANE"    # "PLANE" or "CUBE"

# --- Worn ---
USE_EDGE_WEAR = True       # needs Cycles for the Bevel node; harmless on flat planes
EDGE_RADIUS = 0.03
EDGE_SOFTNESS = (0.995, 0.93)   # dot(bevel normal, normal): no wear .. full wear
WORN_DENT_SCALE = 2.2
WORN_DENT_DENSITY = 0.25
WORN_DENT_DEPTH = 0.02
WORN_SCUFFS = [
    (20.0, 6.0, 0.10, 2.5, 0.50, 0.62),
    (100.0, 9.0, 0.09, 3.5, 0.52, 0.64),
    (155.0, 7.0, 0.08, 3.0, 0.52, 0.64),
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


def dents(b, pos, scale, density):
    """Sparse Voronoi bowls on warped coordinates. Returns a 0..1 depth mask."""
    warp = b.noise(pos, 2.0, detail=2.0).outputs["Color"]
    warped = b.vop("MULTIPLY_ADD", warp, (0.35, 0.35, 0.35), pos)
    vor = b.voronoi(warped, scale)
    cell = b.sepcol(vor.outputs["Color"])
    cell_on = b.op("GREATER_THAN", cell.outputs["Red"], 1.0 - density)
    size_var = b.op("MULTIPLY_ADD", cell.outputs["Green"], 0.5, 0.5)
    bowl = b.remap(vor.outputs["Distance"], 0.0, 0.55, 1.0, 0.0, smooth=True)
    return b.op("MULTIPLY", b.op("MULTIPLY", bowl, cell_on), size_var)


def edge_wear(b):
    """0..1 mask that is high on geometry edges (Bevel node, Cycles only)."""
    geo = b._new("ShaderNodeNewGeometry")
    bevel = b._new("ShaderNodeBevel")
    bevel.samples = 8
    bevel.inputs["Radius"].default_value = EDGE_RADIUS
    d = b.dot(bevel.outputs["Normal"], geo.outputs["Normal"])
    return b.remap(d, EDGE_SOFTNESS[0], EDGE_SOFTNESS[1], 0.0, 1.0, smooth=True)


# ----------------------------------------------------------------------
# Material
# ----------------------------------------------------------------------
def build_worn():
    mat, b, pos = new_material("Metal_Worn")

    dent = dents(b, pos, WORN_DENT_SCALE, WORN_DENT_DENSITY)

    # Rubbed (bright) areas vs dull oxidized / dirty areas
    wear_noise = b.noise(pos, 3.0, detail=8.0, rough=0.6, distortion=0.4).outputs["Fac"]
    wear = b.remap(wear_noise, 0.42, 0.60, 0.0, 1.0, smooth=True)
    if USE_EDGE_WEAR:
        wear = b.op("ADD", wear, edge_wear(b), clamp=True)

    grime = b.noise(pos, 6.0, detail=10.0, rough=0.6, distortion=0.2).outputs["Fac"]
    grain = b.noise(pos, 90.0, detail=10.0, rough=0.7).outputs["Fac"]

    # Small pits
    vor = b.voronoi(pos, 55.0)
    cell = b.sepcol(vor.outputs["Color"])
    pit_on = b.op("GREATER_THAN", cell.outputs["Red"], 0.72)
    pit_bowl = b.remap(vor.outputs["Distance"], 0.0, 0.20, 1.0, 0.0, smooth=True)
    pits = b.op("MULTIPLY", pit_bowl, pit_on)

    scuffs = scratch_set(b, pos, WORN_SCUFFS)

    # Color
    dull = b.ramp(grime, [
        (0.00, (0.16, 0.16, 0.17, 1.0)),
        (0.55, (0.25, 0.24, 0.23, 1.0)),
        (1.00, (0.09, 0.085, 0.075, 1.0)),
    ])
    color = b.mixc(dull, (0.62, 0.62, 0.64, 1.0), wear)
    color = b.mixc(color, (0.03, 0.03, 0.03, 1.0), b.op("MULTIPLY", pits, 0.9))
    color = b.mixc(color, (0.72, 0.72, 0.74, 1.0), b.op("MULTIPLY", scuffs, 0.7))

    # Metallic and roughness
    metallic = b.mixf(0.75, 1.0, wear)
    rough = b.mixf(b.mixf(0.55, 0.80, grime), 0.30, wear)
    rough = b.mixf(rough, 0.45, scuffs)
    rough = b.mixf(rough, 0.90, pits)

    # Normals: shallow dents, then grime layer / pits / scuffs / grain
    n1 = bump(b, b.op("SUBTRACT", 1.0, dent), 1.0, WORN_DENT_DEPTH)
    grime_h = b.op("MULTIPLY", b.op("SUBTRACT", 1.0, wear), 0.12)
    pit_h = b.op("MULTIPLY", pits, -0.25)
    scuff_h = b.op("MULTIPLY", scuffs, -0.05)
    grain_h = b.op("MULTIPLY", grain, 0.04)
    height = b.op("ADD", b.op("ADD", grime_h, pit_h), b.op("ADD", scuff_h, grain_h))
    n2 = bump(b, height, 1.0, 0.01, normal=n1)

    finish(b, color, metallic, rough, n2)
    return mat


def assign_material(mat):
    """Assign to the active mesh object, or create a preview object."""
    obj = bpy.context.active_object
    if obj is None or obj.type != "MESH":
        if PREVIEW_SHAPE == "CUBE":
            bpy.ops.mesh.primitive_cube_add(size=PLANE_SIZE)
        else:
            bpy.ops.mesh.primitive_plane_add(size=PLANE_SIZE)
        obj = bpy.context.active_object

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


if __name__ == "__main__":
    material = build_worn()
    assign_material(material)
    print(f"Material '{material.name}' created and assigned.")