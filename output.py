"""Materials, collections and writing the built objects into the scene."""


import bmesh
import bpy

from .constants import CAT_COLORS, OUTPUT_COLLECTION, PROXY_TAG, WALL_COLLECTION


def material_for(cat):
    """Material for a category: the color shows in Solid mode (diffuse_color)
    and in Rendered mode (the Principled BSDF Base Color)."""
    name = f"Arcology_{cat}"
    color = CAT_COLORS[cat]
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if out is None:
            out = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = color
    return mat


def _walk_collections(coll):
    yield coll
    for ch in coll.children:
        yield from _walk_collections(ch)


def clear_output(prune=True):
    """Removes the proxy objects and meshes this add-on made (tagged PROXY_TAG). Anything else in the
    output collection, and everything outside it, is left alone. Batch removal keeps this fast."""
    # Every object this add-on made, wherever it was written (the output root or a chosen collection).
    doomed = [ob for ob in bpy.data.objects if ob.get(PROXY_TAG)]
    meshes = list({ob.data for ob in doomed if ob.type == 'MESH' and ob.data is not None})
    if doomed:
        bpy.data.batch_remove(doomed)
    if meshes:
        bpy.data.batch_remove([me for me in meshes if me.users == 0])
    # Collections are kept between rebuilds (so the outliner keeps their open/closed state);
    # empty ones are only removed after a rebuild has refilled what it needs, or on Clear.
    if prune:
        prune_output_collections()


def output_collection(scene):
    root = bpy.data.collections.get(OUTPUT_COLLECTION)
    if root is None:
        root = bpy.data.collections.new(OUTPUT_COLLECTION)
    if root.name not in [c.name for c in scene.collection.children]:
        scene.collection.children.link(root)
    return root


ROLE_KEY = "arcology_role"


def child_collection(root, role):
    """The add-on collection for one role (a layer, the walls, a group) under root. Found by its role tag,
    so it is reused between rebuilds, and each footprint's root keeps its own. Your collections are never used."""
    for c in root.children:
        if c.get(PROXY_TAG) and c.get(ROLE_KEY) == role:
            return c
    name = role if root.name == OUTPUT_COLLECTION else f"{role} ({root.name})"
    coll = bpy.data.collections.new(name)
    coll[PROXY_TAG] = True
    coll[ROLE_KEY] = role
    root.children.link(coll)
    return coll


def layer_collection(root, layer):
    """The 'Layer N' collection under root. Reused between rebuilds, so the outliner keeps its state."""
    return child_collection(root, f"Layer {layer}")


def prune_output_collections():
    """Removes add-on collections that are empty. Collections you made are never touched."""
    while True:
        empty = [c for c in bpy.data.collections if c.get(PROXY_TAG) and not c.objects and not c.children]
        if not empty:
            break
        bpy.data.batch_remove(empty)


def write_output(root, buckets):
    count = 0
    # A bucket key is (category, layer), or (category, layer, face) for pieces that get an object of their own.
    for key, bm in sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2:])):
        cat, layer = key[0], key[1]
        face = key[2] if len(key) > 2 else None
        if cat == "walls":
            coll = child_collection(root, WALL_COLLECTION)
            name = f"Wall_L{layer:02d}"
        else:
            coll = layer_collection(root, layer)
            name = f"{cat}_L{layer:02d}"
        if face is not None:
            name += f"_F{face:04d}"
        me = bpy.data.meshes.new(name)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(me)
        bm.free()
        bmesh_normals(me)
        ob = bpy.data.objects.new(name, me)
        ob[PROXY_TAG] = True
        me.materials.append(material_for(cat))
        coll.objects.link(ob)
        count += 1
    return count


def bmesh_normals(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(me)
    bm.free()


def active_entry(scene):
    """The arcology the panel is showing, or None when the list is empty."""
    if not scene.arcologies:
        return None
    index = min(max(scene.arcology_index, 0), len(scene.arcologies) - 1)
    return scene.arcologies[index]


def ensure_uids(scene):
    """Gives every arcology an id that stays with it while it is in the list. Its output lives under that id."""
    next_id = max((e.uid for e in scene.arcologies), default=0) + 1
    for e in scene.arcologies:
        if e.uid == 0:
            e.uid = next_id
            next_id += 1


def arcology_role(entry):
    """The name of the output collection of one arcology. Keyed by its id, so it stays put when the list changes."""
    return f"Arcology {entry.uid}"


def find_child(root, role):
    for c in root.children:
        if c.get(PROXY_TAG) and c.get(ROLE_KEY) == role:
            return c
    return None


def clear_arcology(entry):
    """Removes the proxy objects of this one arcology. The rest of the output is left alone."""
    root = bpy.data.collections.get(OUTPUT_COLLECTION)
    sub = find_child(root, arcology_role(entry)) if root is not None else None
    if sub is None:
        return
    doomed = list(sub.all_objects)
    meshes = list({ob.data for ob in doomed if ob.type == 'MESH' and ob.data is not None})
    if doomed:
        bpy.data.batch_remove(doomed)
    spare = [me for me in meshes if me.users == 0]
    if spare:
        bpy.data.batch_remove(spare)
    prune_output_collections()


def drop_prefabs(records):
    """Removes the prefab objects the copies were made from. They were never linked to the scene, and each copy has its
    own link to the mesh, so the copies are left whole."""
    doomed = []
    for rec in records:
        src = rec["src"]
        if src.get("prototype") and src["obj"] not in doomed:
            doomed.append(src["obj"])
    if doomed:
        meshes = list({ob.data for ob in doomed if ob.data is not None})
        bpy.data.batch_remove(doomed)
        spare = [me for me in meshes if me.users == 0]
        if spare:
            bpy.data.batch_remove(spare)
    return len(doomed)
