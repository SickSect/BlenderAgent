"""Version-tolerant helpers for the Blender Python API.

Blender renames things between versions. The Principled BSDF lost
``Transmission`` (now ``Transmission Weight``), ``Specular`` (now ``Specular IOR
Level``), ``Clearcoat`` (now ``Coat Weight``) in 4.0; node groups changed how
their sockets are declared in 4.0 as well. Code that hard-codes a socket name
breaks on the next Blender release — silently, if the socket is optional.

This module is the **single source of truth** for those names. Before it
existed the same knowledge was copied into three places in different shapes
(``materials._link``, ``snapshot._socket`` and the prose of the skill files),
and the three drifted apart.

The module is deliberately **bpy-free**: every helper works with duck-typed
objects, so it can be imported by the add-on, by generated AI code (through
``bpy.app.driver_namespace``) and by plain unit tests without Blender.

Helpers come in two flavours:

* ``get_input`` / ``set_input`` — try every known spelling of a socket;
* ``link`` — connect two sockets, tolerating renames on both ends.

Nothing here raises for a missing socket: a partial material is better than a
crash (see ``materials.py``).
"""

from __future__ import annotations

# Canonical socket name -> every spelling it has had. The first entry is the
# current one; the rest are legacy names kept for older Blender versions.
SOCKET_ALIASES = {
    "Base Color": ("Base Color",),
    "Metallic": ("Metallic",),
    "Roughness": ("Roughness",),
    "IOR": ("IOR",),
    "Alpha": ("Alpha",),
    "Normal": ("Normal",),
    "Transmission Weight": ("Transmission Weight", "Transmission"),
    "Emission Color": ("Emission Color", "Emission"),
    "Emission Strength": ("Emission Strength",),
    "Specular IOR Level": ("Specular IOR Level", "Specular"),
    "Coat Weight": ("Coat Weight", "Clearcoat"),
    "Coat Roughness": ("Coat Roughness", "Clearcoat Roughness"),
    "Sheen Weight": ("Sheen Weight", "Sheen"),
    "Subsurface Weight": ("Subsurface Weight", "Subsurface"),
    "Displacement": ("Displacement",),
    "Surface": ("Surface",),
}

# Sockets that carry *data*, not colour. An image feeding one of these must
# use the "Non-Color" colour space — the classic silent lighting bug.
DATA_SOCKETS = frozenset(
    {
        "Roughness",
        "Metallic",
        "Normal",
        "Displacement",
        "Coat Roughness",
        "Sheen Roughness",
        "Specular IOR Level",
        "Subsurface Weight",
    }
)

# Legacy name -> canonical name, so a tree built by an older script can be
# recognised ("Transmission" is the 3.x name of "Transmission Weight").
LEGACY_TO_CANONICAL = {
    legacy: canonical
    for canonical, spellings in SOCKET_ALIASES.items()
    for legacy in spellings[1:]
}

# Node types we look up by hand all over the add-on.
PRINCIPLED_TYPE = "BSDF_PRINCIPLED"
OUTPUT_TYPE = "OUTPUT_MATERIAL"
IMAGE_TYPE = "TEX_IMAGE"


def socket_aliases(name: str) -> tuple:
    """Every spelling of *name*, current first."""
    return SOCKET_ALIASES.get(name, (name,))


def canonical_socket(name: str) -> str:
    """Canonical name for a (possibly legacy) socket *name*."""
    return LEGACY_TO_CANONICAL.get(name, name)


def get_input(node, *names: str):
    """First existing input socket among *names*, or ``None``.

    ``names`` are tried in order, so the canonical spelling should come
    first. Duplicates are harmless.
    """
    if node is None:
        return None
    inputs = getattr(node, "inputs", None)
    if inputs is None:
        return None
    for name in names:
        socket = inputs.get(name)
        if socket is not None:
            return socket
    return None


def set_input(node, value, *names: str) -> bool:
    """Write *value* into the first existing input among *names*.

    Returns ``True`` when a socket was found. Socket names differ between
    Blender versions, so callers pass every known spelling.
    """
    socket = get_input(node, *names)
    if socket is None:
        return False
    try:
        socket.default_value = value
    except (TypeError, ValueError):
        return False
    return True


def find_node(tree, node_type: str):
    """First node of ``node_type`` in *tree*, or ``None``."""
    nodes = getattr(tree, "nodes", None)
    if nodes is None:
        return None
    for node in nodes:
        if getattr(node, "type", None) == node_type:
            return node
    return None


def find_principled(tree):
    """The Principled BSDF of *tree*, or ``None``."""
    return find_node(tree, PRINCIPLED_TYPE)


def find_output(tree):
    """The Material Output node of *tree*, or ``None``."""
    return find_node(tree, OUTPUT_TYPE)


def link(links, source, out_name: str, target, in_name: str, *aliases: str) -> bool:
    """Connect ``source.outputs[out_name]`` to ``target.inputs[in_name]``.

    ``aliases`` cover socket renames across Blender versions ("Emission Color"
    vs the older "Emission"). Returns ``False`` when a socket is missing
    instead of raising, so a partial texture set still produces a material.
    """
    outputs = getattr(source, "outputs", None)
    if outputs is None:
        return False
    out = outputs.get(out_name)
    if out is None:
        return False
    target_socket = get_input(target, in_name, *aliases)
    if target_socket is None:
        return False
    links.new(out, target_socket)
    return True


def group_socket(
    tree, name: str, socket_type: str = "NodeSocketColor", in_out: str = "INPUT"
):
    """Declare (or find) a socket on a node group.

    Blender 4.0 moved group sockets to ``tree.interface``; 3.x used
    ``tree.inputs``/``tree.outputs``. Both are handled here so a group built by
    one Blender still opens in the other.
    """
    interface = getattr(tree, "interface", None)
    if interface is not None:
        for item in interface.items_tree:
            if (
                getattr(item, "name", None) == name
                and getattr(item, "in_out", None) == in_out
            ):
                return item
        return interface.new_socket(
            name=name, in_out=in_out, socket_type=socket_type
        )
    legacy = tree.inputs if in_out == "INPUT" else tree.outputs
    for item in legacy:
        if item.name == name:
            return item
    return legacy.new(socket_type, name)


def group_input(tree, name: str, socket_type: str = "NodeSocketColor"):
    """The *input* socket of a node group, declared if missing."""
    return group_socket(tree, name, socket_type, "INPUT")


def group_output(tree, name: str, socket_type: str = "NodeSocketColor"):
    """The *output* socket of a node group, declared if missing."""
    return group_socket(tree, name, socket_type, "OUTPUT")


def install(namespace: dict) -> None:
    """Publish this module's helpers under *namespace* (a dict).

    The add-on calls it with ``bpy.app.driver_namespace`` so that code the
    assistant generates can reach the helpers::

        import bpy
        api = bpy.app.driver_namespace.get("gaa_api")
        if api is not None:
            api.set_input(bsdf, 1.0, "Transmission Weight", "Transmission")
    """
    namespace["gaa_api"] = _PublicApi()


class _PublicApi:
    """The slice of this module that generated code is allowed to use.

    A tiny facade rather than the module itself: the model should reach for
    ``set_input`` and ``get_input``, not for ``SOCKET_ALIASES``.
    """

    set_input = staticmethod(set_input)
    get_input = staticmethod(get_input)
    find_principled = staticmethod(find_principled)
    find_output = staticmethod(find_output)
    link = staticmethod(link)
    socket_aliases = staticmethod(socket_aliases)
    canonical_socket = staticmethod(canonical_socket)
    DATA_SOCKETS = DATA_SOCKETS
