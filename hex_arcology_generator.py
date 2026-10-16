"""
Hex Arcology Proxy Generator (Blender add-on)

Builds the hex arcology from ultra-low-poly proxy shapes (hex prisms, boxes and pyramids) instead of
your detailed modules, so it can rebuild live while you drag the sliders in the sidebar.

Install: Edit > Preferences > Add-ons > Install from Disk, pick this file, tick the add-on on.
Use: 3D Viewport > N sidebar > "Arcology" tab.

Every change to a slider rebuilds the city a moment later (turn "Live" off to rebuild by hand).
Output goes to the "Arcology Proxy" collection, or to the collections you pick in the Collections box.
Your own objects are never changed or deleted.

Footprint: pick a mesh in the Footprint field, or name a mesh "Ark Polygon" or "30 Acre Ark".
Roof Plane: optional. Pick a mesh; its height above each hexagon centre sets that hex's layer count,
so it can be sculpted into slopes. Without one, every hex gets the layer cap.
"""

bl_info = {
    "name": "Modular Hexagonal Arcology Generator",
    "author": "Traveling",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Arcology",
    "description": "Generates the hex arcology from ultra-low-poly proxies, live, with sliders.",
    "category": "Add Mesh",
}

import math
import random
import textwrap
import time
import traceback
from types import SimpleNamespace

import bpy
import bmesh
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector  # noqa: F401  (Matrix kept for users who extend the script)
from mathutils.bvhtree import BVHTree

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OUTPUT_COLLECTION = "Arcology Proxy"
WALL_COLLECTION = "Walls.Polygon"
PROXY_TAG = "arcology_proxy"       # custom property on every object this add-on makes
FOOTPRINT_NAMES = ("Ark Polygon", "30 Acre Ark")
FT = 0.3048
TILE_BOT = 0.42
TILE_TOP = 0.59
ROAD_BASE = 1.077
ROAD_H = 0.61
ROAD_W = 6.09
TREE_RADIUS_FRAC = 0.09          # canopy radius (about 3 m), as a fraction of the hex size
TRUNK_R_FRAC = 0.0075            # trunk radius (about 0.25 m)
TRUNK_H_FRAC = 0.09              # trunk height (about 3 m)
BENCH_LEN_FRAC = 0.06            # bench length (about 2 m)
BENCH_DEPTH_FRAC = 0.018         # bench depth (about 0.6 m)
BENCH_SEAT_H = 0.45
BENCH_BACK_H = 0.95
BENCH_BACK_T = 0.08
PARK_SLAB_H = 0.30
# Road trapezoid outline, traced from the reference screenshot. Hex-size units; u runs along the road,
# v across it, origin at the hex centre.
TRAP_UV = ((-0.345, 0.372), (-0.340, 0.066), (0.397, 0.067), (0.400, 0.372), (0.047, 0.579), (0.006, 0.578))
COLUMN_R = 3.0 * FT / 2.0        # 3 ft across the corners, as a hexagonal prism
ELEVATOR_R_FRAC = 0.09           # elevator shaft: hexagon radius, as a fraction of the hex size. Three per junction, inside the corners
ELEVATOR_WALL_FRAC = 0.012       # thin divider wall through the shaft, corner to corner
ELEVATOR_H_FT = 36.0             # each elevator shaft is 36 ft tall
PATH_WIDTH_FT = 4.0              # park paths are 4 ft wide
PATH_LIFT_FT = 1.0               # paths stand this far above the park surface (avoids z-fighting)
PATH_BENDS = (8, 12)             # bends per park hexagon path, picked at random in this range
TRAP_TOL_FRAC = 0.30             # a tower may clip a trapezoid by this much (fraction of hex size)
WALL_THICK_FRAC = 0.03
HEX_ANGLES = (30, 90, 150, 210, 270, 330)                       # pointy-top hex corners
FAMILIES = [
    ("row", lambda i, j: (i + 1, j), lambda i, j: j),
    ("col", lambda i, j: (i, j + 1), lambda i, j: i),
    ("diag", lambda i, j: (i - 1, j + 1), lambda i, j: i + j),
]
WALL_EDGES = [((1, 0), 0), ((0, 1), 60), ((-1, 1), 120), ((-1, 0), 180), ((0, -1), 240), ((1, -1), 300)]
TRI_CELLS = [(0, 0), (1, 0), (1, -1)]
CAT_COLORS = {
    "tiles": (0.78, 0.74, 0.66, 1.0),
    "roads": (0.22, 0.22, 0.24, 1.0),
    "roofs": (0.55, 0.55, 0.58, 1.0),
    "towers": (0.92, 0.91, 0.88, 1.0),
    "trapezoids": (0.70, 0.62, 0.52, 1.0),
    "elevators": (0.25, 0.30, 0.36, 1.0),
    "parks": (0.30, 0.55, 0.25, 1.0),
    "canopies": (0.12, 0.35, 0.12, 1.0),
    "trunks": (0.36, 0.25, 0.14, 1.0),
    "benches": (0.50, 0.34, 0.20, 1.0),
    "columns": (0.80, 0.78, 0.72, 1.0),
    "paths": (0.80, 0.76, 0.66, 1.0),
    "walls": (0.85, 0.85, 0.80, 1.0),
}

_pending = False                  # a live rebuild is waiting in the timer queue


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def make_layout(p):
    S = p.hex_edge * math.sqrt(3.0)     # across the flats; the edge length is what you set
    level = p.level_ft * FT
    roof = p.roof_ft * FT
    return SimpleNamespace(
        S=S,
        R=S / math.sqrt(3.0),
        APO=S / 2.0,
        ROW=1.5 * S / math.sqrt(3.0),
        HEX_H=level,
        ROOF_H=roof,
        PITCH=level + roof,
        TRAP_TOL=TRAP_TOL_FRAC * S,
        WALL_T=WALL_THICK_FRAC * S,
    )


def lattice_vec(a, b, L):
    return Vector((a * L.S + b * L.S * 0.5, b * L.ROW))


def lattice_key(p, ref, L):
    dx = p.x - ref.x
    dy = p.y - ref.y
    b = dy / L.ROW
    a = (dx - b * L.S * 0.5) / L.S
    ai, bi = round(a), round(b)
    if abs(a - ai) > 0.02 or abs(b - bi) > 0.02:
        return None
    return (ai, bi)


def rot2(v, theta):
    c, s = math.cos(theta), math.sin(theta)
    return Vector((v.x * c - v.y * s, v.x * s + v.y * c))


def layers_under(z, L):
    """Number of layers whose level still fits below height z (at least 1)."""
    n = int(math.floor((z - TILE_TOP - L.HEX_H + 1e-4) / L.PITCH)) + 1   # 0.1 mm allowance for float32
    return max(1, n)


def overlap_depth(P, Q):
    """How far two convex 2D polygons overlap. Zero when they only touch or are apart."""
    best = None
    for poly in (P, Q):
        n = len(poly)
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            ax = Vector((-(b.y - a.y), b.x - a.x))
            if ax.length_squared < 1e-12:
                continue
            ax.normalize()
            pa = [p.dot(ax) for p in P]
            qa = [q.dot(ax) for q in Q]
            ov = min(max(pa), max(qa)) - max(min(pa), min(qa))
            if ov <= 0:
                return 0.0
            best = ov if best is None else min(best, ov)
    return best or 0.0


def hex_ring(c, radius):
    return [Vector((c.x + radius * math.cos(math.radians(a)), c.y + radius * math.sin(math.radians(a))))
            for a in HEX_ANGLES]


def rect_poly(c, ux, half_u, half_v):
    """Rectangle centred on c, long along the unit vector ux."""
    uy = Vector((-ux.y, ux.x))
    return [c + ux * (su * half_u) + uy * (sv * half_v)
            for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


# ---------------------------------------------------------------------------
# Footprint, roof plane and lattice
# ---------------------------------------------------------------------------
def footprint_object(p):
    if p.footprint is not None and p.footprint.type == 'MESH':
        return p.footprint
    for name in FOOTPRINT_NAMES:
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.type == 'MESH':
            return ob
    raise RuntimeError("No footprint mesh. Pick one in the Footprint field, or name a mesh "
                       "'Ark Polygon' or '30 Acre Ark'.")


def mesh_height_tester(ob):
    """Returns (height_at(x, y), lo, hi). height_at gives the height of the object's top surface
    above (x, y), or None where the object is not there."""
    wv = [ob.matrix_world @ v.co for v in ob.data.vertices]
    bvh = BVHTree.FromPolygons(wv, [tuple(poly.vertices) for poly in ob.data.polygons])
    lo = Vector((min(v.x for v in wv), min(v.y for v in wv), min(v.z for v in wv)))
    hi = Vector((max(v.x for v in wv), max(v.y for v in wv), max(v.z for v in wv)))
    top = hi.z + 10.0

    def height_at(x, y):
        loc = bvh.ray_cast(Vector((x, y, top)), Vector((0.0, 0.0, -1.0)))[0]
        return None if loc is None else loc.z

    return height_at, lo, hi


def lattice_points(inside, lo, hi, rng, L):
    """Hex centres inside the footprint, on a lattice with a random offset."""
    u = rng.random()
    v = rng.random()
    ox = lo.x - L.S + u * L.S
    oy = lo.y - L.ROW + v * L.ROW
    pts = {}
    j_min = int(math.floor((lo.y - oy) / L.ROW)) - 2
    j_max = int(math.ceil((hi.y - oy) / L.ROW)) + 2
    for j in range(j_min, j_max + 1):
        y = oy + j * L.ROW
        off = j * L.S / 2.0
        i_min = int(math.floor((lo.x - ox - off) / L.S)) - 2
        i_max = int(math.ceil((hi.x - ox - off) / L.S)) + 2
        for i in range(i_min, i_max + 1):
            x = ox + off + i * L.S
            if inside(x, y):
                pts[(i, j)] = Vector((x, y))
    return pts


def random_roads(pts, rng, chance):
    """Random straight roads of hex centres. Returns (runs, degree)."""
    chosen = {}
    edges = []
    degree = {k: 0 for k in pts}
    for f_index, (name, step, line_key) in enumerate(FAMILIES):
        for key in sorted(pts):
            n = step(*key)
            if n not in pts:
                continue
            line = (f_index, line_key(*key))
            if line not in chosen:
                chosen[line] = rng.random() < chance
            if chosen[line]:
                edges.append((f_index, key, n))
                degree[key] += 1
                degree[n] += 1
    runs = []
    for f_index, _ in enumerate(FAMILIES):
        nxt = {}
        has_prev = set()
        for f, a, b in edges:
            if f == f_index:
                nxt[a] = b
                has_prev.add(b)
        for start in sorted(nxt):
            if start in has_prev:
                continue
            run = [start]
            cur = start
            while cur in nxt:
                cur = nxt[cur]
                run.append(cur)
            runs.append(run)
    return runs, degree


def trapezoid_spots(runs, pts, degree, hmax, L):
    """Two trapezoids per road hex, one on each side of the road, flat side facing the road.
    Transport hexes are left for the elevators."""
    spots = []
    targets = {}
    for run in runs:
        d = (pts[run[-1]] - pts[run[0]]).normalized()
        n = Vector((-d.y, d.x))
        for key in run:
            if degree[key] >= 3:
                continue
            c = pts[key]
            for side in (1.0, -1.0):
                direction = n * side
                taken = targets.setdefault(key, [])
                if any(direction.dot(t) > 0.5 for t in taken):
                    continue
                taken.append(direction)
                poly = [c + d * (u * L.S) + n * (side * v * L.S) for u, v in TRAP_UV]
                spots.append({"key": key, "c": c, "d": d, "poly": poly, "levels": hmax[key] - 1,
                              "target": -direction})
    return spots


# ---------------------------------------------------------------------------
# Proxy geometry helpers (all write into a bmesh)
# ---------------------------------------------------------------------------
def add_prism(bm, poly, z0, z1):
    """A flat-sided prism over a 2D polygon, from z0 to z1."""
    n = len(poly)
    bot = [bm.verts.new((p.x, p.y, z0)) for p in poly]
    top = [bm.verts.new((p.x, p.y, z1)) for p in poly]
    try:
        bm.faces.new(bot[::-1])
        bm.faces.new(top)
    except ValueError:
        pass
    for i in range(n):
        j = (i + 1) % n
        try:
            bm.faces.new((bot[i], bot[j], top[j], top[i]))
        except ValueError:
            pass


def _hex_dir(a):
    return Vector((math.cos(a), math.sin(a)))


def _clamp_hex(p, c, lim):
    """Pull p radially toward c until it sits inside the hexagon whose apothem is lim."""
    q = p - c
    m = max(q.dot(_hex_dir(k * math.tau / 6.0)) for k in range(6))
    if m > lim > 0:
        q *= lim / m
    return c + q


def _wind(pts, p0, p1, bends, rng, c, lim, L):
    """Append `bends` zigzag points between p0 and p1 (p1 itself is added by the caller)."""
    d = p1 - p0
    across = Vector((-d.y, d.x)).normalized() if d.length > 1e-9 else Vector((0.0, 1.0))
    sign = 1.0
    spacing = d.length / (bends + 1.0)
    for i in range(1, bends + 1):
        t = i / (bends + 1.0)
        # offset scales with the spacing between bends, so each leg turns gently rather than in sharp spikes
        amp = rng.uniform(0.20, 0.30) * spacing * math.sin(math.pi * t)
        pts.append(_clamp_hex(p0.lerp(p1, t) + across * (sign * amp), c, lim))
        sign = -sign


def add_path(bm, c, z0, rng, L, edges):
    """A winding path through one park hexagon, 4 ft wide, with PATH_BENDS bends in total.
    edges: neighbour directions (0-5, each 60 degrees) where the path must meet the next park.
    The path runs edge midpoint to edge midpoint, so neighbouring paths meet at the shared edge.
    With no edges it runs straight across the hex; with one it ends in the middle of the hex."""
    w = PATH_WIDTH_FT * FT
    lim = L.APO - w / 2.0
    total = rng.randint(PATH_BENDS[0], PATH_BENDS[1])
    if len(edges) >= 2:
        anchors = [c + _hex_dir(m * math.tau / 6.0) * L.APO for m in sorted(edges)]
    elif edges:
        m = edges[0]
        anchors = [c + _hex_dir(m * math.tau / 6.0) * L.APO,
                   c + _hex_dir(((m + 3) % 6) * math.tau / 6.0) * (L.APO * 0.5)]
    else:
        a = rng.random() * math.tau
        anchors = [c + _hex_dir(a) * (L.APO * 0.6), c + _hex_dir(a + math.pi) * (L.APO * 0.6)]
    segs = len(anchors) - 1
    share = [total // segs] * segs
    for _ in range(total - sum(share)):
        share[rng.randrange(segs)] += 1
    pts = [anchors[0]]
    for i in range(segs):
        _wind(pts, anchors[i], anchors[i + 1], share[i], rng, c, lim, L)
        pts.append(anchors[i + 1])
    # Clean polyline: drop repeated points
    clean = [pts[0]]
    for q in pts[1:]:
        if (q - clean[-1]).length > 1e-6:
            clean.append(q)
    if len(clean) < 2:
        return
    # Flat ribbon, one quad per segment: no thickness, so it adds two triangles per segment
    n = len(clean)
    dirs = []
    for i in range(n - 1):
        d = clean[i + 1] - clean[i]
        dirs.append(d.normalized())
    norms = [Vector((-d.y, d.x)) for d in dirs]
    offs = []
    for i in range(n):
        if i == 0:
            if edges:
                m = edges[0] if len(edges) == 1 else sorted(edges)[0]
                o = _hex_dir(m * math.tau / 6.0 + math.pi / 2.0)   # across the edge, so it meets the neighbour
            else:
                o = norms[0]
        elif i == n - 1:
            if edges and len(edges) >= 2:
                m = sorted(edges)[-1]
                o = _hex_dir(m * math.tau / 6.0 + math.pi / 2.0)
            else:
                o = norms[-1]
        else:
            vn = (norms[i - 1] + norms[i]).normalized()
            o = vn / max(vn.dot(norms[i]), 0.5)
        offs.append(o * (w / 2.0))
    left = [bm.verts.new((clean[i].x + offs[i].x, clean[i].y + offs[i].y, z0)) for i in range(n)]
    right = [bm.verts.new((clean[i].x - offs[i].x, clean[i].y - offs[i].y, z0)) for i in range(n)]
    for i in range(n - 1):
        try:
            bm.faces.new((left[i], left[i + 1], right[i + 1], right[i]))
        except ValueError:
            pass


def add_sphere(bm, centre, radius, segs=8, rings=3):
    """A UV sphere written vertex by vertex (much faster than bmesh.ops.create_uvsphere when
    called hundreds of times). With the defaults it is 48 triangles once triangulated."""
    def face(verts):
        try:
            bm.faces.new(verts)
        except ValueError:
            pass
    top = bm.verts.new(centre + Vector((0.0, 0.0, radius)))
    bot = bm.verts.new(centre - Vector((0.0, 0.0, radius)))
    layers = []
    for j in range(1, rings + 1):
        phi = math.pi * j / (rings + 1)
        z = radius * math.cos(phi)
        rr = radius * math.sin(phi)
        layers.append([bm.verts.new((centre.x + rr * math.cos(k * math.tau / segs),
                                     centre.y + rr * math.sin(k * math.tau / segs), centre.z + z))
                       for k in range(segs)])
    first, last = layers[0], layers[-1]
    for k in range(segs):
        k2 = (k + 1) % segs
        face((top, first[k], first[k2]))
        face((bot, last[k2], last[k]))
    for upper, lower in zip(layers, layers[1:]):
        for k in range(segs):
            k2 = (k + 1) % segs
            face((upper[k], upper[k2], lower[k2], lower[k]))


def add_tree(bm_trunk, bm_canopy, c, z0, scale, L):
    """A low-poly tree: an 8-sided trunk and a UV-sphere canopy, about 76 triangles in total."""
    tr = TRUNK_R_FRAC * L.S * scale
    th = TRUNK_H_FRAC * L.S * scale
    cr = TREE_RADIUS_FRAC * L.S * scale
    ring = [Vector((c.x + tr * math.cos(k * math.tau / 8), c.y + tr * math.sin(k * math.tau / 8)))
            for k in range(8)]
    add_prism(bm_trunk, ring, z0, z0 + th)
    centre = Vector((c.x, c.y, z0 + th + 0.8 * cr))
    add_sphere(bm_canopy, centre, cr)


def tree_spots(c, n, rng, L):
    """Up to n tree positions inside one hex (random, with canopies kept apart).
    Returns (position, size scale) pairs."""
    spots = []
    cr_max = TREE_RADIUS_FRAC * L.S * 1.15
    reach = L.APO - cr_max - 0.5
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        a = rng.random() * math.tau
        r = reach * math.sqrt(rng.random())
        pos = c + Vector((math.cos(a) * r, math.sin(a) * r))
        scale = rng.uniform(0.85, 1.15)
        if all((pos - q).length >= 2.2 * cr_max for q, _ in spots):
            spots.append((pos, scale))
    return spots


def bench_spots(c, n, trees, rng, L):
    """Up to n bench positions inside one hex, clear of the trees. Returns (position, angle) pairs."""
    spots = []
    reach = L.APO - 2.0
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        a = rng.random() * math.tau
        r = reach * math.sqrt(rng.random())
        pos = c + Vector((math.cos(a) * r, math.sin(a) * r))
        clear = all((pos - tp).length >= TREE_RADIUS_FRAC * L.S * sc + 2.5 for tp, sc in trees)
        clear = clear and all((pos - q).length >= 3.0 for q, _ in spots)
        if clear:
            spots.append((pos, rng.random() * math.tau))
    return spots


def add_bench(bm, c, theta, z0, L):
    """A tiny park bench: a seat with a backrest along its rear edge."""
    ux = Vector((math.cos(theta), math.sin(theta)))
    uy = Vector((-ux.y, ux.x))
    half_len = BENCH_LEN_FRAC * L.S / 2.0
    half_dep = BENCH_DEPTH_FRAC * L.S / 2.0
    add_prism(bm, rect_poly(c, ux, half_len, half_dep), z0, z0 + BENCH_SEAT_H)
    back_c = c + uy * (half_dep - BENCH_BACK_T / 2.0)
    add_prism(bm, rect_poly(back_c, ux, half_len, BENCH_BACK_T / 2.0), z0 + BENCH_SEAT_H, z0 + BENCH_BACK_H)


# ---------------------------------------------------------------------------
# Parks
# ---------------------------------------------------------------------------
def place_parks(sites, density, rng, L):
    """Parks on the roofs. sites are (centre, top layer, base z). A tri-hex (three cells) is tried
    first at each site, then a single hex. Cells must share one top layer and base height."""
    if not sites:
        return []
    ref = sites[0][0]
    keymap = {}
    for c, H, zb in sites:
        k = lattice_key(c, ref, L)
        if k is not None:
            keymap[k] = (c, H, zb)
    used = set()
    out = []
    order = list(keymap.keys())
    rng.shuffle(order)
    for k0 in order:
        if k0 in used or rng.random() > density:
            continue
        c0, H0, zb0 = keymap[k0]
        thetas = [m * (math.tau / 6.0) for m in range(6)]
        rng.shuffle(thetas)
        placed = False
        for th in thetas:
            ks = []
            cells = []
            ok = True
            for a, b in TRI_CELLS:
                off = rot2(lattice_vec(a, b, L), th)
                kk = lattice_key(c0 + off, ref, L)
                if kk is None or kk not in keymap or kk in used or kk in ks:
                    ok = False
                    break
                _, H1, zb1 = keymap[kk]
                if H1 != H0 or zb1 != zb0:
                    ok = False
                    break
                ks.append(kk)
                cells.append(c0 + off)
            if ok:
                used.update(ks)
                out.append(("tri", cells, H0, zb0))
                placed = True
                break
        if not placed:
            used.add(k0)
            out.append(("single", [c0], H0, zb0))
    return out


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
    """Footprint, height and anchors of one source object. Cached, since big meshes are slow to read."""
    key = (o.as_pointer(), o.data.as_pointer(), tuple(round(x, 5) for row in o.matrix_world for x in row))
    hit = _SOURCE_CACHE.get(key)
    if hit is not None:
        return hit
    verts = [o.matrix_world @ v.co for v in o.data.vertices]
    lo = Vector((min(v.x for v in verts), min(v.y for v in verts), min(v.z for v in verts)))
    hi = Vector((max(v.x for v in verts), max(v.y for v in verts), max(v.z for v in verts)))
    bc = Vector(((lo.x + hi.x) * 0.5, (lo.y + hi.y) * 0.5, lo.z))
    sample = verts[::max(1, len(verts) // 4000)]
    hull = convex_hull_2d([Vector((v.x - bc.x, v.y - bc.y)) for v in sample])
    org = o.matrix_world.translation.copy()
    c0 = Vector((org.x, org.y))
    hull0 = convex_hull_2d([Vector((v.x - c0.x, v.y - c0.y)) for v in sample])
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
    geo = {"hull": hull, "height": hi.z - lo.z, "bottom_center": bc, "origin": org,
           "zlo": lo.z - org.z, "c0": c0, "zb": lo.z, "f": flat_normal,
           "rz": o.matrix_world.to_euler('XYZ').z}
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
            "matrix": P_ @ Sc @ T @ src["obj"].matrix_world}


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
            "matrix": T @ Rm @ Sz @ C @ src["obj"].matrix_world}


def elevator_record(pool, rng, c, z, height, layer):
    """A copy of one Elevators object, replacing all six shafts of one transport hex on one level. Its
    origin sits at the centre of the hex cell; it keeps its own orientation, and is one level tall."""
    src = pool[rng.randrange(len(pool))]
    kz = height / src["height"] if src["height"] > 0 else 1.0
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((1.0, 1.0, kz, 1.0)))
    T = Matrix.Translation((c.x, c.y, z))
    return {"group": "Elevators", "cat": "elevators", "layer": layer, "src": src,
            "matrix": T @ Sz @ C @ src["obj"].matrix_world}


def wall_record(pool, rng, centre, ang, z, height, layer, L):
    """A copy of one Walls object on an exposed side of a hex. In your source the exterior faces west and
    the length runs north/south. It is turned so the exterior faces out of the hex, then stretched to the
    wall's slot: one hex edge long, WALL_T thick and one level tall."""
    src = pool[rng.randrange(len(pool))]
    xs = [v.x for v in src["hull"]]
    ys = [v.y for v in src["hull"]]
    ext_x = max(xs) - min(xs)
    ext_y = max(ys) - min(ys)
    theta = math.radians(ang) + math.pi          # source -X (west, its exterior) points along the outward normal
    sx = L.WALL_T / ext_x if ext_x > 1e-9 else 1.0
    sy = L.R / ext_y if ext_y > 1e-9 else 1.0
    sz = height / src["height"] if src["height"] > 0 else 1.0
    M = (Matrix.Translation((centre.x, centre.y, z))
         @ Matrix.Rotation(theta, 4, 'Z')
         @ Matrix.Diagonal(Vector((sx, sy, sz, 1.0)))
         @ Matrix.Translation(-src["bottom_center"])
         @ src["obj"].matrix_world)
    return {"group": "Walls", "cat": "walls", "layer": layer, "src": src, "matrix": M}


def park_record(src, cells, th, z_base, layer):
    """A copy of one Parks object on its roof, its origin on the first hex, turned th about it."""
    c0 = cells[0]
    M = (Matrix.Translation((c0.x, c0.y, z_base - src["zlo"]))
         @ Matrix.Rotation(th, 4, 'Z')
         @ Matrix.Translation(-src["origin"]))
    return {"group": "Parks", "cat": "parks", "layer": layer, "src": src,
            "matrix": M @ src["obj"].matrix_world}


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


def write_copies(scene, records):
    """Writes the copies of your source objects into the Hexagons, Road Trapezoids and Parks groups."""
    root = output_collection(scene)
    parents = {}
    for n, rec in enumerate(records):
        key = (rec["cat"], rec["layer"])
        if rec["cat"] == "walls":
            coll = child_collection(root, WALL_COLLECTION)
        else:
            coll = layer_collection(root, rec["layer"])
        if key not in parents:
            # One empty per category per layer, so each layer still holds one object per category.
            em = bpy.data.objects.new(f"{rec['cat']}_L{rec['layer']:02d}", None)
            em.empty_display_type = 'PLAIN_AXES'
            em.empty_display_size = 0.1
            em[PROXY_TAG] = True
            coll.objects.link(em)
            parents[key] = em
        ob = rec["src"]["obj"].copy()
        ob.name = f"{rec['cat']}_copy_L{rec['layer']:02d}_{n:05d}"
        ob[PROXY_TAG] = True
        coll.objects.link(ob)
        ob.parent = parents[key]
        ob.matrix_world = rec["matrix"]
    return len(records)


def build_city(p):
    L = make_layout(p)
    fp = footprint_object(p)
    height_at, lo, hi = mesh_height_tester(fp)
    inside = lambda x, y: height_at(x, y) is not None
    rng = random.Random(p.seed)
    copy_rng = random.Random(p.seed * 7919 + 1)     # separate, so picking objects never moves the layout
    records = []                                    # copies of your objects, written after the proxies
    hex_src = source_pool(p.coll_hexagons)
    trap_src = source_pool(p.coll_trapezoids)
    park_src = source_pool(p.coll_parks)
    wall_src = source_pool(p.coll_walls)
    roof_src = source_pool(p.coll_roof)
    roof_rng = random.Random(p.seed * 7919 + 2)     # roof picks have their own random sequence
    elevator_src = source_pool(p.coll_elevators)
    elev_rng = random.Random(p.seed * 7919 + 3)     # elevator picks have their own random sequence
    for e in park_src:
        n_hex = round(poly_area(e["hull"]) / (2.598076 * L.R * L.R))   # hexagon area = 2.598 R^2
        cells = TRI_CELLS if n_hex >= 2 else [(0, 0)]
        e["offsets"] = [rot2(lattice_vec(a, b, L), e["rz"]) for a, b in cells]

    pts = lattice_points(inside, lo, hi, rng, L)
    if not pts:
        raise RuntimeError("No hex centres fall inside the footprint.")

    cap = max(1, p.max_layers)
    plane = None
    if p.roof_plane is not None and p.roof_plane.type == 'MESH':
        plane, _, _ = mesh_height_tester(p.roof_plane)
    hmax = {}
    outside_plane = 0
    for key, c in pts.items():
        if plane is None:
            n = cap
        else:
            z = plane(c.x, c.y)
            if z is None:
                n = 1
                outside_plane += 1
            else:
                n = layers_under(z, L)
        hmax[key] = max(1, min(cap, n))

    runs, degree = random_roads(pts, rng, p.road_chance)
    spots = trapezoid_spots(runs, pts, degree, hmax, L)
    trap_keys = {s["key"] for s in spots}

    # Classify every hex, as the script does.
    tower_hexes = []        # (key, centre)
    ground_hexes = []       # (key, centre, road?)
    hub_hexes = []          # (key, centre)
    field_blocked = 0
    for key in sorted(pts):
        c = pts[key]
        if degree[key] >= 3:
            hub_hexes.append((key, c))
        elif degree[key] >= 1:
            ground_hexes.append((key, c, True))
        else:
            poly = hex_ring(c, L.R)
            blocked = any(overlap_depth(poly, s["poly"]) > L.TRAP_TOL for s in spots)
            if blocked:
                field_blocked += 1
                ground_hexes.append((key, c, False))
            else:
                tower_hexes.append((key, c))

    tmin, tmax = sorted((p.tower_min, p.tower_max))
    tower_heights = []
    for key, c in tower_hexes:
        H = min(rng.randint(max(1, tmin), max(1, tmax)), hmax[key])
        tower_heights.append(H)
    top = dict(hmax)
    for (key, _), H in zip(tower_hexes, tower_heights):
        top[key] = H

    buckets = {}

    def bucket(cat, layer):
        k = (cat, layer)
        if k not in buckets:
            buckets[k] = bmesh.new()
        return buckets[k]

    P = L.PITCH
    H_ = L.HEX_H
    # Level-1 tile under every hex.
    for key, c in pts.items():
        add_prism(bucket("tiles", 1), hex_ring(c, L.R), TILE_BOT, TILE_TOP)

    def roof(c, z0, z1, layer):
        if roof_src:
            records.append(hex_record(roof_src, roof_rng, c, z0, z1 - z0, layer, L,
                                      group="Roof", cat="roofs"))
        else:
            add_prism(bucket("roofs", layer), hex_ring(c, L.R), z0, z1)

    # Towers: a module on each level, a roof above each module.
    if p.show_towers:
        for (key, c), Ht in zip(tower_hexes, tower_heights):
            for k in range(Ht - 1):
                z = TILE_TOP + k * P
                if hex_src:
                    records.append(hex_record(hex_src, copy_rng, c, z, H_, k + 1, L))
                else:
                    add_prism(bucket("towers", k + 1), hex_ring(c, L.R), z, z + H_)
                roof(c, z + H_, z + P, k + 2)

    # Ground hexes (roads, blocked field hexes) and transport hexes: floors and roofs.
    for key, c, road in [(k, c, r) for k, c, r in ground_hexes] + [(k, c, False) for k, c in hub_hexes]:
        for k in range(1, hmax[key] - 1):
            z = k * P
            add_prism(bucket("tiles", k + 1), hex_ring(c, L.R), TILE_BOT + z, TILE_TOP + z)
        base = ROAD_BASE if road else TILE_TOP
        for k in range(hmax[key] - 1):
            z = base + k * P
            roof(c, z + H_, z + P, k + 2)
    for key, c in hub_hexes:
        for k in range(hmax[key] - 1):
            z = TILE_TOP + k * P
            roof(c, z + H_, z + P, k + 2)

    # Roads: one piece per hex the road passes through, stopping at that hex's top layer.
    if p.show_roads:
        for run in runs:
            d = (pts[run[-1]] - pts[run[0]]).normalized()
            for key in run:
                c = pts[key]
                add_prism(bucket("roads", 1), rect_poly(c, d, L.S / 2.0, ROAD_W / 2.0), ROAD_BASE, ROAD_BASE + ROAD_H)
                for k in range(1, hmax[key] - 1):
                    z = ROAD_BASE + k * P
                    add_prism(bucket("roads", k + 1), rect_poly(c, d, L.S / 2.0, ROAD_W / 2.0), z, z + ROAD_H)

    # Trapezoids: stacked up to the layer under each hex's top.
    if p.show_trapezoids:
        for s in spots:
            for k in range(s["levels"]):
                z = ROAD_BASE + k * P
                if trap_src:
                    records.append(trap_record(trap_src, copy_rng, s, k, L))
                else:
                    add_prism(bucket("trapezoids", k + 1), s["poly"], z, z + H_)

    # Elevators: a hexagonal shaft per level at each transport hex, poking through the top, with a thin
    # divider wall running corner to corner through it. Each hex keeps one wall direction in every level.
    if p.show_elevators:
        shaft_r = ELEVATOR_R_FRAC * L.S
        wall_t = ELEVATOR_WALL_FRAC * L.S
        elev_h = ELEVATOR_H_FT * FT
        for key, c in hub_hexes:
            if elevator_src:
                # One Elevators object fills the whole transport hex: all six shafts, one per level.
                for k in range(hmax[key]):
                    records.append(elevator_record(elevator_src, elev_rng, c, TILE_TOP + k * P, elev_h, k + 1))
                continue
            # Three shafts per junction, on alternate corners of the junction hexagon. Each is split by a
            # wall running out from the junction, so each junction holds six elevator shafts.
            for a_deg in (30.0, 150.0, 270.0):
                a = math.radians(a_deg)
                ux = Vector((math.cos(a), math.sin(a)))
                sc = c + ux * (L.R - shaft_r)     # inside the hub's corner: the shaft's outer vertex meets the hub corner
                for k in range(hmax[key]):
                    z = TILE_TOP + k * P
                    add_prism(bucket("elevators", k + 1), hex_ring(sc, shaft_r), z, z + elev_h)
                    add_prism(bucket("elevators", k + 1), rect_poly(sc, ux, shaft_r, wall_t / 2.0), z, z + elev_h)

    # Columns: a 3 ft hexagonal column at every hex centre and every hex corner. Each runs from the
    # ground up to the top of the stack it stands in, sliced per layer.
    column_count = 0
    if p.show_columns:
        road_keys = {k for k, _, road in ground_hexes if road}
        sites = {}                                   # rounded (x, y) -> (point, stack layers, base z)

        def add_site(xy, layers, base):
            key = (round(xy.x, 3), round(xy.y, 3))
            if key not in sites or sites[key][1] < layers:
                sites[key] = (xy, layers, base)

        for key, c in pts.items():
            add_site(c, top[key], ROAD_BASE if key in road_keys else TILE_TOP)
            for v in hex_ring(c, L.R):
                add_site(v, top[key], TILE_TOP)
        for xy, layers, base in sites.values():
            column_count += 1
            for k in range(max(0, layers - 1)):
                lo = TILE_BOT if k == 0 else base + k * P
                hi = base + (k + 1) * P
                add_prism(bucket("columns", k + 1), hex_ring(xy, COLUMN_R), lo, hi)

    # Walls: on every exposed side. A side is exposed where the hex across it is shorter.
    walls = 0
    if p.show_walls:
        for key, c in pts.items():
            ta = top[key]
            for (da, db), ang in WALL_EDGES:
                tb = top.get((key[0] + da, key[1] + db), 0)
                if tb >= ta:
                    continue
                n = Vector((math.cos(math.radians(ang)), math.sin(math.radians(ang))))
                centre = c + n * (L.APO + L.WALL_T / 2.0)
                tangent = Vector((-n.y, n.x))
                for k in range(max(tb - 1, 0), ta - 1):
                    z = TILE_TOP + k * P
                    if wall_src:
                        records.append(wall_record(wall_src, copy_rng, centre, ang, z, H_, k + 1, L))
                    else:
                        add_prism(bucket("walls", k + 1), rect_poly(centre, tangent, L.R / 2.0, L.WALL_T / 2.0),
                                  z, z + H_)
                    walls += 1

    # Parks on the top layer of each stack: towers, ground hexes and transport hexes.
    parks = 0
    tree_count = 0
    bench_count = 0
    if p.show_parks:
        sites = [(c, Ht, TILE_TOP) for (_, c), Ht in zip(tower_hexes, tower_heights)]
        for key, c, road in ground_hexes:
            if road and hmax[key] < 2:
                continue
            sites.append((c, hmax[key], ROAD_BASE if road else TILE_TOP))
        sites += [(c, hmax[key], TILE_TOP) for key, c in hub_hexes]
        if park_src:
            placements = [(cells, park_th, Hp, zb, src) for src, cells, park_th, Hp, zb
                          in place_picked_parks(park_src, sites, copy_rng, L)]
        else:
            placements = [(cells, 0.0, Hp, zb, None) for kind, cells, Hp, zb
                          in place_parks(sites, p.park_density, rng, L)]
        # path height of every park cell, keyed by layer and centre, so neighbouring paths can meet
        path_z = {}
        for cells, park_th, Hp, zb, src in placements:
            slab_h = src["height"] if src is not None else PARK_SLAB_H
            z_path = zb + (Hp - 1) * P + slab_h + PATH_LIFT_FT * FT
            for cell in cells:
                path_z[(Hp, round(cell.x, 1), round(cell.y, 1))] = z_path
        for cells, park_th, Hp, zb, src in placements:
            z_base = zb + (Hp - 1) * P
            slab_h = src["height"] if src is not None else PARK_SLAB_H
            if src is not None:
                records.append(park_record(src, cells, park_th, z_base, Hp))
            for cell in cells:
                if src is None:
                    add_prism(bucket("parks", Hp), hex_ring(cell, L.R), z_base, z_base + PARK_SLAB_H)
                if p.show_paths:
                    z_path = path_z[(Hp, round(cell.x, 1), round(cell.y, 1))]
                    edges = []
                    for m in range(6):
                        nb = cell + _hex_dir(m * math.tau / 6.0) * L.S
                        if path_z.get((Hp, round(nb.x, 1), round(nb.y, 1))) == z_path:
                            edges.append(m)
                    add_path(bucket("paths", Hp), cell, z_path, rng, L, edges)
                trees = tree_spots(cell, p.trees_per_cell, rng, L) if p.show_trees else []
                for pos, sc in trees:
                    add_tree(bucket("trunks", Hp), bucket("canopies", Hp), pos, z_base + slab_h, sc, L)
                    tree_count += 1
                if p.show_benches:
                    for pos, th in bench_spots(cell, p.benches_per_cell, trees, rng, L):
                        add_bench(bucket("benches", Hp), pos, th, z_base + slab_h, L)
                        bench_count += 1
                parks += 1

    # Hexagon cells for the Stats box: every level of every hex, except transport hexes (all their levels)
    # and the top layer of each stack, which is the park layer.
    hub_keys = {key for key, _ in hub_hexes}
    cells_counted = sum(max(0, hmax[key] - 1) for key in pts if key not in hub_keys)

    stats = {
        "hexes": len(pts),
        "cells_counted": cells_counted,
        "towers": len(tower_hexes),
        "ground": len(ground_hexes),
        "transport": len(hub_hexes),
        "road_runs": len(runs),
        "trapezoid_spots": len(spots),
        "walls": walls,
        "parks": parks,
        "trees": tree_count,
        "columns": column_count,
        "benches": bench_count,
        "top_layer": max(hmax.values()) if hmax else 0,
        "outside_plane": outside_plane,
        "field_blocked": field_blocked,
    }
    stats["copies"] = len(records)
    return buckets, stats, records


# ---------------------------------------------------------------------------
# Output objects
# ---------------------------------------------------------------------------
def material_for(cat):
    name = f"Arcology_{cat}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.diffuse_color = CAT_COLORS[cat]
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


def child_collection(root, name):
    """A collection under the output root. Only collections this add-on made are reused, so a
    collection of yours with the same name is never touched."""
    coll = bpy.data.collections.get(name)
    if coll is None or not coll.get(PROXY_TAG):
        coll = bpy.data.collections.new(name)
        coll[PROXY_TAG] = True
    if coll.name not in [c.name for c in root.children]:
        root.children.link(coll)
    return coll


GROUP_PROP = {"Hexagons": "coll_hexagons", "Road Trapezoids": "coll_trapezoids", "Parks": "coll_parks",
              "Walls": "coll_walls", "Roof": "coll_roof", "Elevators": "coll_elevators"}


def target_collection(scene, root, group):
    """The collection picked in the Collections box for this group, or a collection the add-on makes."""
    chosen = getattr(scene.arcology_props, GROUP_PROP[group])
    if chosen is not None:
        return chosen
    return child_collection(root, group)


def layer_collection(root, layer):
    """The 'Layer N' collection under the output root. Reused between rebuilds, so the outliner keeps its state."""
    name = f"Layer {layer}"
    coll = bpy.data.collections.get(name)
    if coll is None or not coll.get(PROXY_TAG):
        coll = bpy.data.collections.new(name)
        coll[PROXY_TAG] = True
    if coll.name not in [c.name for c in root.children]:
        root.children.link(coll)
    return coll


def prune_output_collections():
    """Removes add-on collections that are empty. Collections you made are never touched."""
    while True:
        empty = [c for c in bpy.data.collections if c.get(PROXY_TAG) and not c.objects and not c.children]
        if not empty:
            break
        bpy.data.batch_remove(empty)


def write_output(scene, buckets):
    root = output_collection(scene)
    count = 0
    for (cat, layer), bm in sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if cat == "walls":
            coll = child_collection(root, WALL_COLLECTION)
            name = f"Wall_L{layer:02d}"
        else:
            coll = layer_collection(root, layer)
            name = f"{cat}_L{layer:02d}"
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


def rebuild(scene):
    """Clears the old proxy city and builds it again from the current slider values."""
    p = scene.arcology_props
    t0 = time.perf_counter()
    buckets, stats, records = build_city(p)
    p.last_footprint = stats["hexes"]
    p.last_hex_count = stats["cells_counted"]           # every level except transport hexes and the park layer
    clear_output(prune=False)
    n_objects = write_output(scene, buckets) + write_copies(scene, records)
    prune_output_collections()
    dt = (time.perf_counter() - t0) * 1000.0
    p.status = (f"{stats['hexes']} hexes, top layer {stats['top_layer']}, "
                f"{stats['towers']} towers, {stats['walls']} walls, {stats['parks']} park cells, "
                f"{stats['trees']} trees, {stats['benches']} benches, "
                f"{n_objects} objects, {dt:.0f} ms")
    return stats


# ---------------------------------------------------------------------------
# Live updates
# ---------------------------------------------------------------------------
def _timer_fire():
    global _pending
    _pending = False
    try:
        rebuild(bpy.context.scene)
    except Exception as exc:                       # keep the add-on alive; show the error in the panel
        traceback.print_exc()
        try:
            bpy.context.scene.arcology_props.status = f"Error: {exc}"
        except Exception:
            pass
    return None                                    # run once


def schedule_rebuild(delay=0.15):
    """Waits a moment after the last slider change, then rebuilds once."""
    global _pending
    if _pending:
        return
    _pending = True
    bpy.app.timers.register(_timer_fire, first_interval=delay)


def _on_change(self, context):
    if self.live:
        schedule_rebuild()


# ---------------------------------------------------------------------------
# Properties, operators and panel
# ---------------------------------------------------------------------------
def _is_mesh(self, obj):
    return obj.type == 'MESH'


def monthly_mortgage_payment(principal, annual_rate_percent, years=30):
    """Standard fixed-rate mortgage payment per month."""
    n = years * 12
    r = annual_rate_percent / 100.0 / 12.0
    if principal <= 0:
        return 0.0
    if r <= 0:
        return principal / n
    return principal * r / (1.0 - (1.0 + r) ** (-n))


def _set_height_shown(props, key, value, lo_ft, hi_ft):
    """Store a typed or dragged height in feet, clamped to the range in feet."""
    feet = value if props.size_in_feet else value / FT
    props[key] = min(max(feet, lo_ft), hi_ft)


def _set_hex_edge_shown(props, value):
    """Store the typed or dragged value in meters, clamped to the hex edge range."""
    metres = value * (FT if props.size_in_feet else 1.0)
    props["hex_edge"] = min(max(metres, 10.0 / math.sqrt(3.0)), 80.0 / math.sqrt(3.0))


class ArcologyProps(PropertyGroup):
    footprint: PointerProperty(
        name="Footprint", type=bpy.types.Object, poll=_is_mesh, update=_on_change,
        description="The mesh the arcology is built on. Leave empty to use 'Ark Polygon' or '30 Acre Ark'")
    roof_plane: PointerProperty(
        name="Roof Plane", type=bpy.types.Object, poll=_is_mesh, update=_on_change,
        description="Optional. Its height above each hex sets that hex's layer count, so it can be sculpted")
    live: BoolProperty(name="Live update", default=True,
                       description="Rebuild a moment after every change")
    seed: IntProperty(name="Seed", default=42, min=0, max=999999, update=_on_change)
    hex_edge: FloatProperty(name="Hex edge length", default=33.35 / math.sqrt(3.0),
                            min=10.0 / math.sqrt(3.0), max=80.0 / math.sqrt(3.0), update=_on_change,
                            description="Length of one edge of a hexagon, in scene units. "
                                        "Across the flats it is this times 1.732 (33.35 by default)")
    size_in_feet: BoolProperty(
        name="Feet", default=False,
        description="Show the hex edge length in feet instead of meters")
    hex_edge_shown: FloatProperty(
        name="Hex edge length", precision=2, min=5.0, max=160.0, update=_on_change,
        get=lambda self: self.hex_edge / (FT if self.size_in_feet else 1.0),
        set=lambda self, value: _set_hex_edge_shown(self, value),
        description="Length of one edge of a hexagon. Shown in the unit picked by the button beside Size")
    level_ft: FloatProperty(name="Level height (ft)", default=30.0, min=8.0, max=60.0, update=_on_change)
    roof_ft: FloatProperty(name="Roof height (ft)", default=6.0, min=0.5, max=20.0, update=_on_change)
    # Shown in feet or meters, following the button beside Size. The stored value is always feet.
    level_shown: FloatProperty(
        name="Level height", precision=2, min=2.0, max=60.0, update=_on_change,
        get=lambda self: self.level_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "level_ft", value, 8.0, 60.0),
        description="Height of one level. Shown in the unit picked by the button beside Size")
    roof_shown: FloatProperty(
        name="Roof height", precision=2, min=0.15, max=20.0, update=_on_change,
        get=lambda self: self.roof_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "roof_ft", value, 0.5, 20.0),
        description="Height of the roof slab. Shown in the unit picked by the button beside Size")
    max_layers: IntProperty(name="Max layers", default=12, min=1, max=24, update=_on_change,
                            description="The most layers any hex can have")
    tower_min: IntProperty(name="Tower min layers", default=10, min=1, max=24, update=_on_change)
    tower_max: IntProperty(name="Tower max layers", default=12, min=1, max=24, update=_on_change)
    road_chance: FloatProperty(name="Road chance", default=0.12, min=0.0, max=1.0, subtype='FACTOR',
                               update=_on_change, description="Chance that a straight line of hexes is a road")
    park_density: FloatProperty(name="Park density", default=0.8, min=0.0, max=1.0, subtype='FACTOR',
                                update=_on_change, description="Chance that a roof site gets a park")
    show_towers: BoolProperty(name="Towers", default=True, update=_on_change)
    show_roads: BoolProperty(name="Roads", default=True, update=_on_change)
    show_trapezoids: BoolProperty(name="Trapezoids", default=True, update=_on_change)
    show_parks: BoolProperty(name="Parks", default=True, update=_on_change)
    show_elevators: BoolProperty(name="Elevators", default=True, update=_on_change)
    show_walls: BoolProperty(name="Walls", default=True, update=_on_change)
    show_trees: BoolProperty(name="Trees", default=True, update=_on_change)
    coll_hexagons: PointerProperty(
        name="Hexagons", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied to fill the hexagon towers. Empty = generated hexagons")
    coll_trapezoids: PointerProperty(
        name="Road Trapezoids", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied onto the road hexes. Empty = generated trapezoids")
    coll_parks: PointerProperty(
        name="Parks", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied onto the roofs as parks. Empty = generated parks")
    coll_walls: PointerProperty(
        name="Walls", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied onto the exposed sides as walls. Empty = generated walls")
    coll_roof: PointerProperty(
        name="Roof", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied onto the roofs. Empty = generated roofs")
    coll_elevators: PointerProperty(
        name="Elevators", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied into the elevator shafts. Empty = generated shafts")
    show_benches: BoolProperty(name="Benches", default=True, update=_on_change)
    show_paths: BoolProperty(name="Paths", default=True, update=_on_change,
                             description="Winding 4 ft paths across the parks")
    show_columns: BoolProperty(name="Columns", default=True, update=_on_change,
                               description="3 ft hexagonal columns at every hex centre and corner")
    trees_per_cell: IntProperty(name="Trees per park hex", default=5, min=0, max=30, update=_on_change,
                                description="Random trees placed in each park hexagon")
    benches_per_cell: IntProperty(name="Benches per park hex", default=2, min=0, max=30, update=_on_change,
                                  description="Random benches placed in each park hexagon")
    # Stats box. These only change the sums shown, so moving them does not rebuild the city.
    last_hex_count: IntProperty(name="Hexagon cells", default=0,
                                description="Hexagon cells in the last build, counted at every level, excluding transport hexes and the top (park) layer (set by Rebuild)")
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of hexagon cells that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per hexagon", default=20, min=0,
                                description="Residents in each residential hexagon cell")
    stat_cost: FloatProperty(name="Cost Per Hexagon ($M)", default=2.0, min=0.0, precision=2,
                             description="Build cost of one hexagon cell, in millions of dollars")
    last_footprint: IntProperty(name="Footprint hexes", default=0,
                                description="Hexagons across the footprint in the last build, transport included (set by Rebuild)")
    stat_household: FloatProperty(name="Persons Per Household", default=2.6, min=0.1, max=20.0, precision=1,
                                  description="Average number of people in each household")
    stat_mortgage_rate: FloatProperty(name="Mortgage Rate", default=7.0, min=0.0, max=30.0, precision=2,
                                      subtype='PERCENTAGE',
                                      description="Annual mortgage interest rate. The loan is a 30-year fixed mortgage")
    status: bpy.props.StringProperty(name="Status", default="Not built yet")


class ARCOLOGY_OT_rebuild(Operator):
    bl_idname = "arcology.rebuild"
    bl_label = "Rebuild"
    bl_description = "Rebuild the proxy arcology now"

    def execute(self, context):
        try:
            rebuild(context.scene)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class ARCOLOGY_OT_clear(Operator):
    bl_idname = "arcology.clear"
    bl_label = "Clear"
    bl_description = "Remove the proxy arcology"

    def execute(self, context):
        clear_output()
        context.scene.arcology_props.status = "Cleared"
        return {'FINISHED'}


class ARCOLOGY_OT_reseed(Operator):
    bl_idname = "arcology.reseed"
    bl_label = "New seed"
    bl_description = "Pick a random seed"

    def execute(self, context):
        context.scene.arcology_props.seed = random.randint(0, 999999)
        return {'FINISHED'}


COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Hexagons: no origin needed. The add-on uses each object's bounding box.",
    "Roof: no origin needed. The add-on uses each object's bounding box.",
    "Elevators: put the origin at the centre of the transport hex cell it sits in. One copy replaces all six shafts.",
    "Road Trapezoids: put the origin at the centre of the hex cell it sits in.",
    "Parks: put the origin at the centre of the park's first hex cell.",
    "Wall exteriors must be facing west, and wall lengths must be facing North/South.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


# The full description, shown in the add-on's Preferences panel. Blender's add-on list cuts
# the one-line description short, so the whole text is drawn here instead.
DESCRIPTION_PARAGRAPHS = (
    "Add your own custom-built cellular hexagons and other object data to the algorithm. "
    "All of this is intended to be built in real life, this is the future of urban living! "
    "At arkology.org, you will also find a vibe-coded new city-state government, intended to govern "
    "these arcologies. Open source and ready to be copied and re-hosted by anyone interested in building "
    "and leading the future!",
)
WEBSITE_TEXT = "www.arkology.org"
WEBSITE_URL = "https://www.arkology.org"
YOUTUBE_TEXT = "www.youtube.com/@arkology_city"
YOUTUBE_URL = "https://www.youtube.com/@arkology_city"


class ARCOLOGY_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        for paragraph in DESCRIPTION_PARAGRAPHS:
            for line in textwrap.wrap(paragraph, 90):
                layout.label(text=line)
            layout.label(text="")
        layout.label(text="Proudly made by Claude Haiku 5.5")
        row = layout.row()
        row.label(text="YouTube:")
        op = row.operator("wm.url_open", text=YOUTUBE_TEXT, icon='URL')
        op.url = YOUTUBE_URL
        row = layout.row()
        row.label(text="Website:")
        op = row.operator("wm.url_open", text=WEBSITE_TEXT, icon='URL')
        op.url = WEBSITE_URL


MANHATTAN_DENSITY = 75000.0          # people per square mile, for the Stats box comparison

FOOTPRINT_INFO = (
    "Footprint: sets the X/Y grid the arcology is built on.\n"
    "Roof plane: Sets the Z axis height above the arcology. I suggest using a subdivided sculpted plane."
)


class ARCOLOGY_OT_info(Operator):
    """Shows a short popup with the text it was given"""
    bl_idname = "arcology.info"
    bl_label = "Info"
    bl_options = {'INTERNAL'}
    text: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        col = self.layout.column(align=True)
        for paragraph in self.text.split("\n"):
            for line in textwrap.wrap(paragraph, 80):
                col.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


class ARCOLOGY_OT_collection_info(Operator):
    bl_idname = "arcology.collection_info"
    bl_label = "Collection origins"
    bl_description = "How to set the origin of the objects you pick"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        col = self.layout.column(align=True)
        for line in COLLECTION_INFO:
            col.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


class ARCOLOGY_PT_panel(Panel):
    bl_label = "Hex Arcology"
    bl_idname = "ARCOLOGY_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Arcology"

    def draw(self, context):
        layout = self.layout
        p = context.scene.arcology_props

        row = layout.row(align=True)
        row.operator("arcology.rebuild", icon='FILE_REFRESH')
        row.operator("arcology.clear", text="", icon='X')
        layout.prop(p, "live")
        layout.label(text=p.status)

        box = layout.box()
        row = box.row()
        row.label(text="Footprint and roof")
        row.operator("arcology.info", text="", icon='INFO').text = FOOTPRINT_INFO
        box.prop(p, "footprint")
        box.prop(p, "roof_plane")

        box = layout.box()
        row = box.row()
        row.label(text="Collections")
        row.operator("arcology.collection_info", text="", icon='INFO')
        box.prop(p, "coll_hexagons")
        box.prop(p, "coll_trapezoids")
        box.prop(p, "coll_parks")
        box.prop(p, "coll_walls")
        box.prop(p, "coll_roof")
        box.prop(p, "coll_elevators")

        box = layout.box()
        row = box.row()
        row.label(text="Size")
        row.prop(p, "size_in_feet", text="ft" if p.size_in_feet else "m", toggle=True)
        unit = "ft" if p.size_in_feet else "m"
        box.prop(p, "hex_edge_shown", text=f"Hex edge length ({unit})")
        box.prop(p, "level_shown", text=f"Level height ({unit})")
        box.prop(p, "roof_shown", text=f"Roof height ({unit})")

        box = layout.box()
        box.label(text="Height")
        box.prop(p, "max_layers")
        box.prop(p, "tower_min")
        box.prop(p, "tower_max")

        box = layout.box()
        box.label(text="Roads and seed")
        row = box.row(align=True)
        row.prop(p, "seed")
        row.operator("arcology.reseed", text="", icon='FILE_REFRESH')
        box.prop(p, "road_chance")

        box = layout.box()
        box.label(text="Show")
        grid = box.grid_flow(columns=2, align=True)
        grid.prop(p, "show_towers")
        grid.prop(p, "show_roads")
        grid.prop(p, "show_trapezoids")
        grid.prop(p, "show_parks")
        grid.prop(p, "show_elevators")
        grid.prop(p, "show_walls")
        grid.prop(p, "show_trees")
        grid.prop(p, "show_benches")
        grid.prop(p, "show_columns")
        grid.prop(p, "show_paths")
        box.prop(p, "park_density")
        box.prop(p, "trees_per_cell")
        box.prop(p, "benches_per_cell")

        box = layout.box()
        box.label(text="Stats")
        cells = p.last_hex_count
        if cells:
            box.label(text=f"Hexagon cells (every layer, excluding transport and parks): {cells:,}")
        else:
            box.label(text="Hexagon cells (every layer, excluding transport and parks): rebuild to count")
        box.prop(p, "stat_residential")
        box.prop(p, "stat_residents")
        box.prop(p, "stat_cost")
        population = cells * p.stat_residential * p.stat_residents
        cost_m = cells * p.stat_cost
        cost_text = f"${cost_m:,.1f} M" if cost_m < 1000 else f"${cost_m / 1000:,.2f} B"
        box.label(text=f"Estimated Population: {population:,.0f}")
        box.label(text=f"Estimated Cost: {cost_text}")
        if p.last_footprint:
            # Land area: the footprint hexagons, each with area (3*sqrt(3)/2) * edge^2 (edge in meters)
            land_m2 = p.last_footprint * (3.0 * math.sqrt(3.0) / 2.0) * p.hex_edge ** 2
            acres = land_m2 / 4046.8564224
            sq_miles = land_m2 / 2589988.11
            pop_per_sq_mile = population / sq_miles if sq_miles > 0 else 0.0
            box.label(text=f"Estimated Land Area: {acres:,.2f} acres")
            box.label(text=f"Population / square mile: {pop_per_sq_mile:,.0f}")
            box.label(text=f"Density Compared to Manhattan: {pop_per_sq_mile / MANHATTAN_DENSITY:,.2f}x")
        else:
            box.label(text="Estimated Land Area: rebuild to calculate")
        box.prop(p, "stat_household")
        box.prop(p, "stat_mortgage_rate")
        # Cost per household: the hexagon cost divided by (residents per hexagon / persons per household)
        households_per_hex = p.stat_residents / p.stat_household if p.stat_household > 0 else 0.0
        cost_per_household = (p.stat_cost * 1_000_000.0) / households_per_hex if households_per_hex > 0 else 0.0
        payment = monthly_mortgage_payment(cost_per_household, p.stat_mortgage_rate)
        box.label(text=f"Cost per household: ${cost_per_household:,.0f}")
        box.label(text=f"Estimated Mortgage Payment: ${payment:,.0f} / month")


CLASSES = (
    ArcologyProps,
    ARCOLOGY_OT_rebuild,
    ARCOLOGY_OT_clear,
    ARCOLOGY_OT_reseed,
    ARCOLOGY_OT_collection_info,
    ARCOLOGY_OT_info,
    ARCOLOGY_PT_panel,
    ARCOLOGY_Preferences,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.arcology_props = PointerProperty(type=ArcologyProps)


def unregister():
    global _pending
    try:
        bpy.app.timers.unregister(_timer_fire)
    except Exception:
        pass
    _pending = False
    del bpy.types.Scene.arcology_props
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
