"""Source-object pools and placement records shared by every shape's builder."""


import math

from mathutils import Matrix, Vector

from .constants import PROXY_TAG, ROAD_BASE, WALL_COLLECTION
from .geometry import lattice_key, rot2
from .output import child_collection, layer_collection


# ---------------------------------------------------------------------------
# Build the city into per-(category, layer) bmeshes
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Source objects from the Collections box: copies of your own objects fill the proxy slots
# ---------------------------------------------------------------------------
_SOURCE_CACHE = {}


def convex_hull_2d(points):
    """Convex hull of 2D points (monotone chain), as Vectors."""
    pts = sorted({(round(p.x, 6), round(p.y, 6)) for p in points})
    if len(pts) <= 2:
        return [Vector(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [Vector(p) for p in lower[:-1] + upper[:-1]]


def poly_area(poly):
    n = len(poly)
    return abs(sum(poly[i].x * poly[(i + 1) % n].y - poly[(i + 1) % n].x * poly[i].y
                   for i in range(n))) / 2.0


def _source_geometry(o):
    """Footprint, height and anchors of one source object, read in the object's own frame: its scale is kept, but its
    turn about the vertical axis is not, since every copy is turned by the placement that makes it. Cached, since big
    meshes are slow to read."""
    key = (o.as_pointer(), o.data.as_pointer(), tuple(round(x, 5) for row in o.matrix_world for x in row))
    hit = _SOURCE_CACHE.get(key)
    if hit is not None:
        return hit
    loc, _turn, scale = o.matrix_world.decompose()
    xform = Matrix.Translation(loc) @ Matrix.Diagonal(scale.to_4d())
    verts = [xform @ v.co for v in o.data.vertices]
    lo = Vector((min(v.x for v in verts), min(v.y for v in verts), min(v.z for v in verts)))
    hi = Vector((max(v.x for v in verts), max(v.y for v in verts), max(v.z for v in verts)))
    bc = Vector(((lo.x + hi.x) * 0.5, (lo.y + hi.y) * 0.5, lo.z))
    sample = verts[::max(1, len(verts) // 4000)]
    hull = convex_hull_2d([Vector((v.x - bc.x, v.y - bc.y)) for v in sample])
    org = xform.translation.copy()
    c0 = Vector((org.x, org.y))
    # The flat side is read from every vertex, not the sample: a sampled hull can pick a different long edge.
    hull0 = convex_hull_2d([Vector((v.x - c0.x, v.y - c0.y)) for v in verts])
    # Trapezoid flat side: outward normal of the longest edge of the footprint, seen from the origin.
    flat_normal = Vector((0.0, 1.0))
    if len(hull0) >= 2:
        best = None
        for i in range(len(hull0)):
            a, b = hull0[i], hull0[(i + 1) % len(hull0)]
            if best is None or (b - a).length > best[0]:
                best = ((b - a).length, a, b)
        _, a, b = best
        flat_normal = Vector((b.y - a.y, -(b.x - a.x))).normalized()
        cen = sum(hull0, Vector((0.0, 0.0))) / len(hull0)
        if ((a + b) * 0.5 - cen).dot(flat_normal) < 0:
            flat_normal = -flat_normal
    geo = {"hull": hull, "height": hi.z - lo.z, "ext": Vector((hi.x - lo.x, hi.y - lo.y)), "bottom_center": bc,
           "origin": org, "zlo": lo.z - org.z, "c0": c0, "zb": lo.z, "f": flat_normal, "rz": 0.0, "xform": xform}
    if len(_SOURCE_CACHE) > 64:
        _SOURCE_CACHE.clear()
    _SOURCE_CACHE[key] = geo
    return geo


def source_pool(coll):
    """The mesh objects in a picked collection, ready to copy. Empty when nothing is picked."""
    if coll is None:
        return []
    return [dict(_source_geometry(o), obj=o) for o in coll.objects
            if o.type == 'MESH' and not o.get(PROXY_TAG) and len(o.data.vertices) > 0]


def fit_to_hex(piece, theta, apothem):
    """Largest uniform XY scale at which the piece, turned by theta, stays inside the hex."""
    pts = [rot2(p, theta) for p in piece["hull"]]
    s = 1e6
    for k in range(6):
        ang = math.radians(60.0 * k)
        nrm = Vector((math.cos(ang), math.sin(ang)))
        reach = max(p.dot(nrm) for p in pts)
        if reach > 1e-6:
            s = min(s, apothem / reach)
    return s


def hex_record(pool, rng, c, z, height, layer, L, group="Hexagons", cat="towers"):
    """A copy of one Hexagons (or Roof) object: turned in 60 degree steps, filling the hex, height tall."""
    src = pool[rng.randrange(len(pool))]
    theta = rng.randrange(6) * math.tau / 6.0
    s = fit_to_hex(src, theta, L.APO)
    kz = height / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((c.x, c.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s, s, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src,
            "matrix": P_ @ Sc @ T @ src["xform"]}


def road_strip_record(pool, rng, centre, theta, length, width, z, height, layer):
    """A copy of one Road object, as a road strip. Its origin sits at the centre of the strip and its X axis runs
    along the road at angle theta. It is stretched to the strip's length and width, and to the road height."""
    src = pool[rng.randrange(len(pool))]
    xs = [v.x for v in src["hull"]]
    ys = [v.y for v in src["hull"]]
    ext_x = max(xs) - min(xs)
    ext_y = max(ys) - min(ys)
    kx = length / ext_x if ext_x > 1e-9 else 1.0
    ky = width / ext_y if ext_y > 1e-9 else 1.0
    kz = height / src["height"] if src["height"] > 0 else 1.0
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((kx, ky, kz, 1.0)))
    T = Matrix.Translation((centre.x, centre.y, z))
    return {"group": "Road", "cat": "roads", "layer": layer, "src": src,
            "matrix": T @ Matrix.Rotation(theta, 4, 'Z') @ Sz @ C @ src["xform"]}


def stretch_record(pool, rng, centre, theta, length, width, z, height, layer, group, cat):
    """A copy of one of your objects stretched to a rectangle: its length along the angle theta, its width across it,
    one height tall, and its bottom centre on the centre of the rectangle. Used for Penrose roads and elevators."""
    src = pool[rng.randrange(len(pool))]
    kx = length / src["ext"].x if src["ext"].x > 1e-9 else 1.0
    ky = width / src["ext"].y if src["ext"].y > 1e-9 else 1.0
    kz = height / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((centre.x, centre.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sz = Matrix.Diagonal(Vector((kx, ky, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src, "matrix": P_ @ Sz @ T @ src["xform"]}


def trap_record(pool, rng, spot, k, L):
    """A copy of one Road Trapezoid object for one level of a road hex, turned so its flat side faces
    the road, as the original script did."""
    src = pool[rng.randrange(len(pool))]
    target = spot["target"]
    theta = math.atan2(target.y, target.x) - math.atan2(src["f"].y, src["f"].x)
    Rm = Matrix.Rotation(theta, 4, 'Z')
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    kz = L.HEX_H / src["height"] if src["height"] > 0 else 1.0
    Sz = Matrix.Diagonal(Vector((1.0, 1.0, kz, 1.0)))
    T = Matrix.Translation((spot["c"].x, spot["c"].y, ROAD_BASE + k * L.PITCH))
    return {"group": "Road Trapezoids", "cat": "trapezoids", "layer": k + 1, "src": src,
            "matrix": T @ Rm @ Sz @ C @ src["xform"]}


def elevator_record(pool, rng, c, z, height, layer, xy=1.0, turn=0.0, spin=False):
    """A copy of one Elevators object on one level. Its origin sits at c (the centre of a hex cell, or a corner of a
    Penrose tile), turned by turn about the vertical axis, with its own size scaled by xy; it is one level tall.
    spin keeps the object's own turn about the vertical axis, as it is laid out in the scene."""
    src = pool[rng.randrange(len(pool))]
    kz = height / src["height"] if src["height"] > 0 else 1.0
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((xy, xy, kz, 1.0)))            # xy scales the shaft with the octagon edge
    T = Matrix.Translation((c.x, c.y, z))
    rz = 0.0
    if spin and src.get("obj") is not None:
        rz = src["obj"].matrix_world.to_quaternion().to_euler().z
    return {"group": "Elevators", "cat": "elevators", "layer": layer, "src": src,
            "matrix": T @ Matrix.Rotation(turn, 4, 'Z') @ Matrix.Rotation(rz, 4, 'Z') @ Sz @ C @ src["xform"]}


def wall_record(pool, rng, centre, ang, z, height, layer, L, length=None, thick=None):
    """A copy of one Walls object on an exposed side of a hex. In your source the exterior faces west and
    the length runs north/south. It is turned so the exterior faces out of the hex, then stretched to the
    wall's slot: one hex edge long, WALL_T thick and one level tall."""
    src = pool[rng.randrange(len(pool))]
    xs = [v.x for v in src["hull"]]
    ys = [v.y for v in src["hull"]]
    ext_x = max(xs) - min(xs)
    ext_y = max(ys) - min(ys)
    theta = math.radians(ang) + math.pi          # source -X (west, its exterior) points along the outward normal
    wt = L.WALL_T if thick is None else thick
    wl = L.R if length is None else length
    sx = wt / ext_x if ext_x > 1e-9 else 1.0
    sy = wl / ext_y if ext_y > 1e-9 else 1.0
    sz = height / src["height"] if src["height"] > 0 else 1.0
    M = (Matrix.Translation((centre.x, centre.y, z))
         @ Matrix.Rotation(theta, 4, 'Z')
         @ Matrix.Diagonal(Vector((sx, sy, sz, 1.0)))
         @ Matrix.Translation(-src["bottom_center"])
         @ src["xform"])
    return {"group": "Walls", "cat": "walls", "layer": layer, "src": src, "matrix": M}


def park_record(src, cells, th, z_base, layer):
    """A copy of one Parks object on its roof, its origin on the first hex, turned th about it."""
    c0 = cells[0]
    M = (Matrix.Translation((c0.x, c0.y, z_base - src["zlo"]))
         @ Matrix.Rotation(th, 4, 'Z')
         @ Matrix.Translation(-src["origin"]))
    return {"group": "Parks", "cat": "parks", "layer": layer, "src": src,
            "matrix": M @ src["xform"]}


def place_picked_parks(shapes, sites, rng, L):
    """Parks from your Parks objects, placed as the original script placed them: a random shape at a
    random free spot, until no shape fits. Returns (shape, cells, turn, layers, base z) per park."""
    if not sites or not shapes:
        return []
    ref = sites[0][0]
    site_keys = {}
    for c, H, zb in sites:
        k = lattice_key(c, ref, L)
        if k is not None:
            site_keys[k] = (c, (H, zb))
    used = set()
    placed = []
    active = list(range(len(shapes)))
    while active:
        i = active[rng.randrange(len(active))]
        sh = shapes[i]
        options = []
        for k0, (c0, lev0) in site_keys.items():
            for m in range(6):
                th = m * math.tau / 6.0
                ks, cells, ok = [], [], True
                for off in sh["offsets"]:
                    pos = c0 + rot2(off, th)
                    kk = lattice_key(pos, ref, L)
                    if kk is None or kk not in site_keys or kk in used or kk in ks or site_keys[kk][1] != lev0:
                        ok = False
                        break
                    ks.append(kk)
                    cells.append(pos)
                if ok:
                    options.append((th, ks, cells, k0))
        if not options:
            active.remove(i)
            continue
        th, ks, cells, k0 = options[rng.randrange(len(options))]
        used.update(ks)
        _, (H0, zb0) = site_keys[k0]
        placed.append((sh, cells, th, H0, zb0))
    return placed


def write_copies(root, records):
    """Writes the copies of your source objects into their layer collections (walls into the Walls collection).
    Each copy is a plain object with no parent, so no relationship lines are drawn to the origin."""
    for n, rec in enumerate(records):
        if rec["cat"] == "walls":
            coll = child_collection(root, WALL_COLLECTION)
        else:
            coll = layer_collection(root, rec["layer"])
        ob = rec["src"]["obj"].copy()
        ob.name = f"{rec['cat']}_copy_L{rec['layer']:02d}_{n:05d}"
        ob[PROXY_TAG] = True
        coll.objects.link(ob)
        ob.parent = None                     # a copy of an object that has a parent would keep that parent
        ob.matrix_world = rec["matrix"]
    return len(records)
