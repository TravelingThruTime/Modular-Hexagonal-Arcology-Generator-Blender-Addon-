"""
Hex Arcology Proxy Generator (Blender add-on)

Builds the hex arcology from ultra-low-poly proxy shapes (hex prisms, boxes and pyramids) instead of
your detailed modules, so it can rebuild live while you drag the sliders in the sidebar.

Install: Edit > Preferences > Add-ons > Install from Disk, pick this file, tick the add-on on.
Use: 3D Viewport > N sidebar > "Arcology" tab. The list holds one arcology per footprint: press + to add
one, then pick its footprint mesh and its shape (Hexagon or Octagon). Each arcology has its own settings,
so you can mix shapes and values. Build replaces the city in the scene, and Clear removes it.

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
    "version": (1, 4, 0),
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
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
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
    "road_octagons": (0.30, 0.30, 0.33, 1.0),
    "road_rectangles": (0.30, 0.30, 0.33, 1.0),
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
    "squares": (0.80, 0.80, 0.78, 1.0),
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
        TREE_SCALE=p.tree_scale,
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
def default_footprint(context):
    """The footprint a new arcology starts on: the active mesh (not one of the add-on's own), else the first
    of 'Ark Polygon' or '30 Acre Ark' found in the file. None when there is neither."""
    ob = context.active_object
    if ob is not None and ob.type == 'MESH' and not ob.get(PROXY_TAG):
        return ob
    for name in FOOTPRINT_NAMES:
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.type == 'MESH':
            return ob
    return None


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


def choose_lines(lines, rng, chance, max_gap, min_gap=0):
    """Decides which lines (rows, columns, diagonals, in order) are roads. Each line is a road with the
    road chance, but never more than max_gap lines in a row go without one, and at least min_gap lines
    must sit between two roads, so roads are never back to back."""
    chosen = {}
    run = min_gap                                   # lines since the last road; the first road is free
    for lk in sorted(lines):
        road = rng.random() < chance
        if run < min_gap:
            road = False
        elif not road and run >= max_gap:
            road = True
        chosen[lk] = road
        run = 0 if road else run + 1
    return chosen


def random_roads(pts, rng, chance, max_gap, min_gap=0):
    """Random straight roads of hex centres. Returns (runs, degree)."""
    chosen = {}
    edges = []
    degree = {k: 0 for k in pts}
    for f_index, (name, step, line_key) in enumerate(FAMILIES):
        lines = {line_key(*key) for key in pts if step(*key) in pts}
        for lk, road in choose_lines(lines, rng, chance, max_gap, min_gap).items():
            chosen[(f_index, lk)] = road
        for key in sorted(pts):
            n = step(*key)
            if n not in pts:
                continue
            line = (f_index, line_key(*key))
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


def _clamp_oct(p, c, lim):
    """Pulls p inside the octagon of apothem lim around c (its sides face 0, 45, 90 ... degrees)."""
    q = p - c
    for k in range(8):
        a = k * math.pi / 4.0
        n = Vector((math.cos(a), math.sin(a)))
        d = q.dot(n)
        if d > lim:
            q -= n * (d - lim)
    return c + q


def add_oct_path(bm, c, z0, rng, L, sides):
    """A winding roof path through one octagon cell, 4 ft wide, made of exactly six flat quads (six
    planes). sides: the axial sides (0 east, 2 north, 4 west, 6 south) whose neighbour also has a path,
    so the path runs edge midpoint to edge midpoint and meets the next cell there. With no neighbours
    it runs across the cell; with one it ends in the middle of the cell."""
    def dirv(m):
        a = m * math.pi / 4.0
        return Vector((math.cos(a), math.sin(a)))
    w = PATH_WIDTH_FT * FT
    lim = L.APO - w / 2.0
    if len(sides) >= 2:
        anchors = [c + dirv(m) * L.APO for m in sorted(sides)]
    elif sides:
        m = sides[0]
        anchors = [c + dirv(m) * L.APO, c + dirv((m + 4) % 8) * (L.APO * 0.5)]
    else:
        a = rng.random() * math.tau
        anchors = [c + dirv(a / (math.pi / 4.0)) * (L.APO * 0.6), c - dirv(a / (math.pi / 4.0)) * (L.APO * 0.6)]
    bends = 7 - len(anchors)                  # 6 quads = (points - 1), points = anchors + bends
    segs = len(anchors) - 1
    share = [bends // segs] * segs
    for _ in range(bends - sum(share)):
        share[rng.randrange(segs)] += 1
    pts = [anchors[0]]
    for i in range(segs):
        p0, p1, k = anchors[i], anchors[i + 1], share[i]
        d = p1 - p0
        across = Vector((-d.y, d.x)).normalized()
        sign = 1.0
        spacing = d.length / (k + 1.0)
        for j in range(1, k + 1):
            t = j / (k + 1.0)
            amp = rng.uniform(0.20, 0.30) * spacing * math.sin(math.pi * t)
            pts.append(_clamp_oct(p0.lerp(p1, t) + across * (sign * amp), c, lim))
            sign = -sign
        pts.append(p1)
    clean = [pts[0]]
    for q in pts[1:]:
        if (q - clean[-1]).length > 1e-6:
            clean.append(q)
    if len(clean) < 2:
        return
    n = len(clean)
    norms = []
    for i in range(n - 1):
        d = (clean[i + 1] - clean[i]).normalized()
        norms.append(Vector((-d.y, d.x)))
    offs = []
    for i in range(n):
        if i == 0:
            o = norms[0]
        elif i == n - 1:
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
    ts = getattr(L, "TREE_S", L.S)
    tr = TRUNK_R_FRAC * ts * scale
    th = TRUNK_H_FRAC * ts * scale
    cr = TREE_RADIUS_FRAC * ts * scale
    ring = [Vector((c.x + tr * math.cos(k * math.tau / 8), c.y + tr * math.sin(k * math.tau / 8)))
            for k in range(8)]
    add_prism(bm_trunk, ring, z0, z0 + th)
    centre = Vector((c.x, c.y, z0 + th + 0.8 * cr))
    add_sphere(bm_canopy, centre, cr)


def tree_spots(c, n, rng, L):
    """Up to n tree positions inside one hex (random, with canopies kept apart).
    Returns (position, size scale) pairs."""
    spots = []
    cr_max = TREE_RADIUS_FRAC * getattr(L, "TREE_S", L.S) * 1.15 * getattr(L, "TREE_SCALE", 1.0)
    reach = L.APO - cr_max - 0.5
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        a = rng.random() * math.tau
        r = reach * math.sqrt(rng.random())
        pos = c + Vector((math.cos(a) * r, math.sin(a) * r))
        scale = rng.uniform(0.85, 1.15) * getattr(L, "TREE_SCALE", 1.0)
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
        clear = all((pos - tp).length >= TREE_RADIUS_FRAC * getattr(L, "TREE_S", L.S) * sc + 2.5 for tp, sc in trees)
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


def elevator_record(pool, rng, c, z, height, layer, xy=1.0):
    """A copy of one Elevators object, replacing all six shafts of one transport hex on one level. Its
    origin sits at the centre of the hex cell; it keeps its own orientation, and is one level tall."""
    src = pool[rng.randrange(len(pool))]
    kz = height / src["height"] if src["height"] > 0 else 1.0
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((xy, xy, kz, 1.0)))            # xy scales the shaft with the octagon edge
    T = Matrix.Translation((c.x, c.y, z))
    return {"group": "Elevators", "cat": "elevators", "layer": layer, "src": src,
            "matrix": T @ Sz @ C @ src["obj"].matrix_world}


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


def write_copies(root, records):
    """Writes the copies of your source objects into the Hexagons, Road Trapezoids and Parks groups."""
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


def build_city(p, fp=None, index=0):
    L = make_layout(p)
    road_w = p.road_width_ft * FT                        # the road width set in the Size box
    seed = p.seed + index                       # each arcology on its own footprint gets its own layout
    if fp is None:
        raise RuntimeError("This arcology has no footprint mesh")
    height_at, lo, hi = mesh_height_tester(fp)
    inside = lambda x, y: height_at(x, y) is not None
    rng = random.Random(seed)
    copy_rng = random.Random(seed * 7919 + 1)     # separate, so picking objects never moves the layout
    records = []                                    # copies of your objects, written after the proxies
    hex_src = source_pool(p.coll_hexagons)
    trap_src = source_pool(p.coll_trapezoids)
    park_src = source_pool(p.coll_parks)
    wall_src = source_pool(p.coll_walls)
    roof_src = source_pool(p.coll_roof)
    roof_rng = random.Random(seed * 7919 + 2)     # roof picks have their own random sequence
    elevator_src = source_pool(p.coll_elevators)
    elev_rng = random.Random(seed * 7919 + 3)     # elevator picks have their own random sequence
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

    runs, degree = random_roads(pts, rng, p.road_chance, p.max_cells_between_roads, p.min_cells_between_roads)
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
                add_prism(bucket("roads", 1), rect_poly(c, d, L.S / 2.0, road_w / 2.0), ROAD_BASE, ROAD_BASE + ROAD_H)
                for k in range(1, hmax[key] - 1):
                    z = ROAD_BASE + k * P
                    add_prism(bucket("roads", k + 1), rect_poly(c, d, L.S / 2.0, road_w / 2.0), z, z + ROAD_H)

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
                trees = tree_spots(cell, p.trees_per_cell, rng, L) if p.show_trees and p.tree_scale > 0 else []
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
# Octagon city (its own tab). Octagons sit on a square grid with pitch D = edge * (1 + sqrt 2), so
# neighbours share sides across the axes, and the gap between four octagons is a square (side = edge)
# turned 45 degrees. Roads run through the octagon centres, along the rows and columns.
# ---------------------------------------------------------------------------
SHAPE_HEX = "HEXAGON"
SHAPE_OCT = "OCTAGON"
SHAPE_RECT = "RECTANGLE"
SHAPE_PEN = "PENROSE"
SQRT2 = math.sqrt(2.0)
OCT_ANGLES = tuple(22.5 + 45.0 * k for k in range(8))     # octagon corners; its sides face 0, 45, 90 ...
OCT_AXIAL = {0: (1, 0), 2: (0, 1), 4: (-1, 0), 6: (0, -1)} # side index -> neighbour step (sides 0,2,4,6 face axes)
OCT_DIAG_SQUARE = {1: (0, 0), 3: (-1, 0), 5: (-1, -1), 7: (0, -1)}   # diagonal side index -> gap square key
SQ_SIDE_OCT = {45: (1, 1), 135: (0, 1), 225: (0, 0), 315: (1, 0)}    # square side angle -> octagon key
# Elevator shafts in a transport octagon: eight 14 ft square shafts, turned 45 degrees, from the octagon centre
OCT_SHAFT_FT = 14.0
OCT_EDGE_DEFAULT = 33.35 / math.sqrt(3.0)     # the octagon edge the shafts were sized for
OCT_SHAFT_OFFSETS_FT = tuple(
    (sx * dx, sy * dy)
    for dx, dy in ((35.9, 62.6), (62.6, 35.9))
    for sx in (-1, 1) for sy in (-1, 1))


def oct_layout(p):
    """Octagon geometry from its own edge length (in metres)."""
    e = p.oct_edge
    apo = e * (1.0 + SQRT2) / 2.0                        # centre to side
    R = e / (2.0 * math.sin(math.radians(22.5)))         # centre to corner
    return SimpleNamespace(
        EDGE=e, APO=apo, R=R, S=2.0 * apo, D=e * (1.0 + SQRT2), TREE_SCALE=p.tree_scale,
        HEX_H=p.level_ft * FT, ROOF_H=p.roof_ft * FT, PITCH=p.level_ft * FT + p.roof_ft * FT,
        WALL_T=WALL_THICK_FRAC * 2.0 * apo, ROAD_W=p.road_width_ft * FT,
    )


def oct_ring(c, radius):
    return [Vector((c.x + radius * math.cos(math.radians(a)), c.y + radius * math.sin(math.radians(a))))
            for a in OCT_ANGLES]


def square_ring(c, edge):
    """A square turned 45 degrees, so each side faces a diagonal side of an octagon."""
    r = edge / SQRT2
    return [Vector((c.x + r * math.cos(k * math.tau / 4), c.y + r * math.sin(k * math.tau / 4)))
            for k in range(4)]


def fit_to_poly(piece, theta, apothem, sides, offset_deg=0.0):
    """Largest uniform XY scale at which the piece, turned by theta, stays inside a regular polygon
    with the given number of sides, apothem and side offset."""
    pts = [rot2(p, theta) for p in piece["hull"]]
    s = 1e6
    for k in range(sides):
        ang = math.radians(offset_deg + 360.0 * k / sides)
        nrm = Vector((math.cos(ang), math.sin(ang)))
        reach = max(p.dot(nrm) for p in pts)
        if reach > 1e-6:
            s = min(s, apothem / reach)
    return s


def poly_record(pool, rng, c, z, height, layer, apothem, sides, offset_deg, group, cat):
    """A copy of one of your objects, turned in steps of 360/sides degrees, filling a regular polygon
    and stretched to the given height. Used for octagons, squares and octagon roofs."""
    src = pool[rng.randrange(len(pool))]
    theta = rng.randrange(sides) * math.tau / sides
    s = fit_to_poly(src, theta, apothem, sides, offset_deg)
    kz = height / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((c.x, c.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s, s, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src,
            "matrix": P_ @ Sc @ T @ src["obj"].matrix_world}


def oct_lattice(inside, lo, hi, rng, L):
    """Octagon centres (inside the footprint) and gap-square centres (inside the footprint).
    The grid starts at a random offset, as the hexagon lattice does."""
    D = L.D
    ox = lo.x + rng.random() * D
    oy = lo.y + rng.random() * D
    octs, sqs = {}, {}
    i_min = int(math.floor((lo.x - ox) / D)) - 2
    i_max = int(math.ceil((hi.x - ox) / D)) + 2
    j_min = int(math.floor((lo.y - oy) / D)) - 2
    j_max = int(math.ceil((hi.y - oy) / D)) + 2
    for j in range(j_min, j_max + 1):
        for i in range(i_min, i_max + 1):
            c = Vector((ox + i * D, oy + j * D))
            if inside(c.x, c.y):
                octs[(i, j)] = c
            m = Vector((ox + (i + 0.5) * D, oy + (j + 0.5) * D))
            if inside(m.x, m.y):
                sqs[(i, j)] = m
    return octs, sqs


def oct_random_roads(octs, rng, chance, max_gap, min_gap=0):
    """Straight roads through octagon centres, along rows and columns. Each row or column is a road with
    the road chance, and no more than max_gap rows or columns in a row go without one. Returns (edges,
    degree): the road links, and how many roads meet each octagon."""
    chosen = {}
    edges = []
    degree = {k: 0 for k in octs}
    for family in (0, 1):
        lines = set()
        for key in octs:
            n = (key[0] + 1, key[1]) if family == 0 else (key[0], key[1] + 1)
            if n in octs:
                lines.add(key[1] if family == 0 else key[0])
        for lk, road in choose_lines(lines, rng, chance, max_gap, min_gap).items():
            chosen[(family, lk)] = road
        for key in sorted(octs):
            n = (key[0] + 1, key[1]) if family == 0 else (key[0], key[1] + 1)
            if n not in octs:
                continue
            line = (family, key[1]) if family == 0 else (family, key[0])
            if chosen[line]:
                edges.append((key, n))
                degree[key] += 1
                degree[n] += 1
    return edges, degree


HALLWAY_HALF_FT = 6.0                              # the centre hallway through a road octagon is 12 ft wide
EDGE_HALLWAY_HALF_FT = 6.0                         # the hallway along each octagon edge is 12 ft wide


def _clip(poly, n, a, keep_nonneg):
    """Sutherland-Hodgman clip of a convex polygon to one side of the line through a with normal n."""
    out = []
    m = len(poly)
    for i in range(m):
        p, q = poly[i], poly[(i + 1) % m]
        fp = n.dot(p - a) if keep_nonneg else -n.dot(p - a)
        fq = n.dot(q - a) if keep_nonneg else -n.dot(q - a)
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append(p + (q - p) * t)
    return out


def _polygon_area(poly):
    return abs(sum(poly[i].x * poly[(i + 1) % len(poly)].y - poly[(i + 1) % len(poly)].x * poly[i].y
                   for i in range(len(poly)))) / 2.0


def _subtract_convex(poly, rect):
    """The parts of a convex polygon that lie outside a convex rectangle (counter-clockwise), as convex polygons."""
    pieces = []
    cur = poly
    for i in range(len(rect)):
        a, b = rect[i], rect[(i + 1) % len(rect)]
        d = b - a
        n = Vector((-d.y, d.x)).normalized()           # inward normal of a counter-clockwise edge
        outside = _clip(cur, n, a, keep_nonneg=False)
        if len(outside) >= 3 and _polygon_area(outside) > 1e-6:    # skip slivers that have no area
            pieces.append(outside)
        cur = _clip(cur, n, a, keep_nonneg=True)
        if len(cur) < 3:
            break
    return pieces


def road_octagon_pieces(c, links, L):
    """The road octagon of one cell: the octagon with a 20 ft road strip cut out from its centre along each
    road it connects to, a 12 ft centre hallway across it at right angles to the road, and a 12 ft hallway along each of its two edges that lie across the road. links are the (x, y) steps
    of those roads. Returns convex pieces."""
    half_road = L.ROAD_W / 2.0
    half_hall = HALLWAY_HALF_FT * FT
    reach = L.APO + 0.1                                    # a little past the edge, so each cut goes all the way through
    cuts = []
    for dx, dy in links:
        u = Vector((dx, dy))
        cuts.append(rect_poly(c + u * (reach / 2.0), u, reach / 2.0, half_road))
    half_edge = EDGE_HALLWAY_HALF_FT * FT
    vertical = any(dx == 0 for dx, dy in links)            # a north-south road
    horizontal = any(dy == 0 for dx, dy in links)          # an east-west road
    if vertical and not horizontal:
        cuts.append(rect_poly(c, Vector((1.0, 0.0)), reach, half_hall))    # centre hallway runs east-west
        for side in (1.0, -1.0):                                           # edge hallways along the north and south edges
            cuts.append(rect_poly(c + Vector((0.0, side * L.APO)), Vector((1.0, 0.0)), reach, half_edge))
    if horizontal and not vertical:
        cuts.append(rect_poly(c, Vector((0.0, 1.0)), reach, half_hall))    # centre hallway runs north-south
        for side in (1.0, -1.0):                                           # edge hallways along the east and west edges
            cuts.append(rect_poly(c + Vector((side * L.APO, 0.0)), Vector((0.0, 1.0)), reach, half_edge))
    pieces = [oct_ring(c, L.R)]
    for cut in cuts:
        nxt = []
        for piece in pieces:
            nxt.extend(_subtract_convex(piece, cut))
        pieces = nxt
    return pieces


def road_blocked_sides(links):
    """Octagon side indices (0-7, as in the wall loop) that a road or hallway runs through, so no wall there."""
    index = {(1, 0): 0, (0, 1): 2, (-1, 0): 4, (0, -1): 6}
    out = set()
    for dx, dy in links:
        out.add(index[(dx, dy)])
        if dy == 0:
            out |= {2, 6}                                  # east-west road: hallway north-south
        if dx == 0:
            out |= {0, 4}                                  # north-south road: hallway east-west
    return out


def road_octagon_record(pool, rng, c, z, height, layer, horizontal, turn=0.0, src=None):
    """A copy of one Road Octagons object on a road or junction octagon. Its origin sits at the centre of
    the octagon cell, and it keeps its size. A road running north-south turns it 90 degrees. It is stretched
    to the road height."""
    if src is None:
        src = pool[rng.randrange(len(pool))]
    kz = height / src["height"] if src["height"] > 0 else 1.0
    theta = (0.0 if horizontal else math.pi / 2.0) + turn
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((1.0, 1.0, kz, 1.0)))
    T = Matrix.Translation((c.x, c.y, z))
    return {"group": "Road Octagons", "cat": "roads", "layer": layer, "src": src,
            "matrix": T @ Matrix.Rotation(theta, 4, 'Z') @ Sz @ C @ src["obj"].matrix_world}


def build_octagon_city(p, fp=None, index=0):
    """The octagon city, as the hexagon one: towers, roads, junctions with elevators, gap squares,
    walls, roofs and parks. Returns (buckets, stats, records) like build_city."""
    L = oct_layout(p)
    seed = p.seed + index                       # each arcology on its own footprint gets its own layout
    if fp is None:
        raise RuntimeError("This arcology has no footprint mesh")
    height_at, lo, hi = mesh_height_tester(fp)
    inside = lambda x, y: height_at(x, y) is not None
    rng = random.Random(seed)
    copy_rng = random.Random(seed * 7919 + 21)     # picks have their own sequences, so the layout is fixed
    roof_rng = random.Random(seed * 7919 + 22)
    elev_rng = random.Random(seed * 7919 + 23)
    records = []
    oct_src = source_pool(p.coll_octagons)
    sq_src = source_pool(p.coll_squares)
    roof_src = source_pool(p.coll_roof)
    sq_roof_src = source_pool(p.coll_square_roofs)
    wall_src = source_pool(p.coll_walls)
    park_src = source_pool(p.coll_parks)
    elev_src = source_pool(p.coll_elevators)
    road_src = source_pool(p.coll_road_octagons)
    road_rng = random.Random(seed * 7919 + 24)

    octs, sqs = oct_lattice(inside, lo, hi, rng, L)
    if not octs:
        raise RuntimeError("No octagons fall inside the footprint.")

    cap = max(1, p.max_layers)
    plane = None
    if p.roof_plane is not None and p.roof_plane.type == 'MESH':
        plane, _, _ = mesh_height_tester(p.roof_plane)
    hmax = {}
    for key, c in octs.items():
        if plane is None:
            n = cap
        else:
            z = plane(c.x, c.y)
            n = 1 if z is None else layers_under(z, L)
        hmax[key] = max(1, min(cap, n))

    edges, degree = oct_random_roads(octs, rng, p.road_chance, p.max_cells_between_roads, p.min_cells_between_roads)
    hubs = {k for k in octs if degree[k] >= 3}              # junctions: transport octagons
    roads = {k for k in octs if 1 <= degree[k] < 3}          # road octagons
    towers = {k for k in octs if degree[k] == 0}

    tmin, tmax = sorted((p.tower_min, p.tower_max))
    tower_heights = {}
    for key in sorted(towers):
        tower_heights[key] = min(rng.randint(max(1, tmin), max(1, tmax)), hmax[key])
    top = dict(hmax)
    for key, H in tower_heights.items():
        top[key] = H

    sq_hmax = {}
    for key, m in sqs.items():
        if plane is None:
            n = cap
        else:
            z = plane(m.x, m.y)
            n = 1 if z is None else layers_under(z, L)
        sq_hmax[key] = max(1, min(cap, n))
    sq_top = {}
    for key in sorted(sqs):
        sq_top[key] = min(rng.randint(max(1, tmin), max(1, tmax)), sq_hmax[key])

    buckets = {}

    def bucket(cat, layer):
        k = (cat, layer)
        if k not in buckets:
            buckets[k] = bmesh.new()
        return buckets[k]

    P = L.PITCH
    H_ = L.HEX_H

    # Ground: a tile under every octagon, and a block in every gap square.
    for key, c in octs.items():
        if key not in roads and key not in hubs:           # roads have the road octagon instead; intersections are open
            add_prism(bucket("tiles", 1), oct_ring(c, L.R), TILE_BOT, TILE_TOP)
    for key, m in sqs.items():
        add_prism(bucket("squares", 1), square_ring(m, L.EDGE), TILE_BOT, TILE_TOP)

    def roof(c, z0, z1, layer):
        if roof_src:
            records.append(poly_record(roof_src, roof_rng, c, z0, z1 - z0, layer, L.APO, 8, 0.0, "Roof", "roofs"))
        else:
            add_prism(bucket("roofs", layer), oct_ring(c, L.R), z0, z1)

    # Towers: a module on each level, a roof above each module.
    if p.show_towers:
        for key in sorted(towers):
            c = octs[key]
            for k in range(tower_heights[key] - 1):
                z = TILE_TOP + k * P
                if oct_src:
                    records.append(poly_record(oct_src, copy_rng, c, z, H_, k + 1, L.APO, 8, 0.0,
                                               "Octagons", "towers"))
                else:
                    add_prism(bucket("towers", k + 1), oct_ring(c, L.R), z, z + H_)
                roof(c, z + H_, z + P, k + 2)

    # Gap squares: a module on each level above the ground block, and a roof above each module.
    def sq_roof(m, z0, z1, layer):
        if sq_roof_src:
            records.append(poly_record(sq_roof_src, roof_rng, m, z0, z1 - z0, layer, L.EDGE / 2.0, 4, 45.0,
                                       "Roof", "roofs"))
        else:
            add_prism(bucket("roofs", layer), square_ring(m, L.EDGE), z0, z1)

    if p.show_squares:
        for key in sorted(sqs):
            m = sqs[key]
            for k in range(sq_top[key] - 1):
                z = TILE_TOP + k * P
                if sq_src:
                    records.append(poly_record(sq_src, copy_rng, m, z, H_, k + 1, L.EDGE / 2.0, 4, 45.0,
                                               "Squares", "squares"))
                else:
                    add_prism(bucket("squares", k + 1), square_ring(m, L.EDGE), z, z + H_)
                sq_roof(m, z + H_, z + P, k + 2)

    # Road octagons and junctions: floors and roofs, as the hexagon ground cells.
    for key in sorted(roads | hubs):
        c = octs[key]
        base = ROAD_BASE if key in roads else TILE_TOP
        for k in range(hmax[key] - 1):
            z = base + k * P
            roof(c, z + H_, z + P, k + 2)

    # Roads. With Road Octagons picked, each road octagon gets a copy, one per level. Without it, each one is an
    # octagon with a hallway through its centre along each road it connects to. Transport octagons (intersections)
    # get only the road strips and elevators, no road octagon.
    links = {}
    for a, b in edges:
        d = (b[0] - a[0], b[1] - a[1])
        links.setdefault(a, []).append(d)
        links.setdefault(b, []).append((-d[0], -d[1]))
    horizontal = set()
    for a, b in edges:
        if a[1] == b[1]:                                   # the two octagons share a row, so the road runs east-west
            horizontal.update((a, b))
    if p.show_roads:
        for key in sorted(roads | hubs):
            c = octs[key]
            dirs = links.get(key, [])
            for k in range(max(1, hmax[key] - 1)):
                z = ROAD_BASE + k * P
                for dx, dy in dirs:                       # the 20 ft road, from the centre out to each side it leaves
                    u = Vector((dx, dy))
                    add_prism(bucket("roads", k + 1), rect_poly(c + u * (L.APO / 2.0), u, L.APO / 2.0, L.ROAD_W / 2.0),
                              z, z + ROAD_H)
                if key not in roads:                      # a transport octagon is an intersection: no road octagon in it
                    continue
                if road_src:
                    # Two copies per road cell, 180 degrees apart about the cell centre, from the same object.
                    pick = road_src[road_rng.randrange(len(road_src))]
                    for turn in (0.0, math.pi):
                        records.append(road_octagon_record(road_src, road_rng, c, z, H_, k + 1, key in horizontal,
                                                           turn, pick))
                else:
                    for piece in road_octagon_pieces(c, dirs, L):
                        add_prism(bucket("road_octagons", k + 1), piece, z, z + H_)

    # Elevators: one per level in every junction.
    if p.show_elevators:
        elev_h = ELEVATOR_H_FT * FT
        shaft_s = L.EDGE / OCT_EDGE_DEFAULT                 # shafts shrink and grow in X and Y with the edge length
        for key in sorted(hubs):
            c = octs[key]
            for k in range(hmax[key]):
                z = TILE_TOP + k * P
                if elev_src:
                    records.append(elevator_record(elev_src, elev_rng, c, z, elev_h, k + 1, shaft_s))
                else:
                    for dx, dy in OCT_SHAFT_OFFSETS_FT:
                        centre = c + Vector((dx * FT * shaft_s, dy * FT * shaft_s))
                        add_prism(bucket("elevators", k + 1), square_ring(centre, OCT_SHAFT_FT * FT * shaft_s), z, z + elev_h)

    # Columns: a 3 ft octagonal column at every octagon corner, one per level of the stack on that corner.
    # Each column is one level plus one roof tall (36 ft). Corners shared by octagons take the taller stack.
    column_count = 0
    if p.show_columns:
        col_r = 1.5 * FT / math.cos(math.radians(22.5))      # octagon 3 ft wide, flat side to flat side
        col_site = {}                                        # rounded (x, y) -> (point, layers, base z)
        for key, c in octs.items():
            layers = top[key]
            base = ROAD_BASE if key in roads else TILE_TOP
            for v in oct_ring(c, L.R):
                site = (round(v.x, 3), round(v.y, 3))
                if site not in col_site or col_site[site][1] < layers:
                    col_site[site] = (v, layers, base)
        for v, layers, base in col_site.values():
            column_count += 1
            column = oct_ring(v, col_r)
            for k in range(max(0, layers - 1)):
                add_prism(bucket("columns", k + 1), column, base + k * P, base + (k + 1) * P)

    # Walls: on every side that is exposed, octagons and squares alike. A side is exposed where the cell
    # across it is shorter, as in the hexagon city.
    walls = 0

    def side_walls(c, ang, reach, tb, ta):
        """Walls on one side of a cell, from the level above the neighbour up to the cell's top."""
        n = Vector((math.cos(math.radians(ang)), math.sin(math.radians(ang))))
        centre = c + n * (reach + L.WALL_T / 2.0)
        tangent = Vector((-n.y, n.x))
        count = 0
        for k in range(max(tb - 1, 0), ta - 1):
            z = TILE_TOP + k * P
            if wall_src:
                records.append(wall_record(wall_src, copy_rng, centre, ang, z, H_, k + 1, L,
                                           length=L.EDGE, thick=L.WALL_T))
            else:
                add_prism(bucket("walls", k + 1), rect_poly(centre, tangent, L.EDGE / 2.0, L.WALL_T / 2.0),
                          z, z + H_)
            count += 1
        return count

    if p.show_walls:
        for key in sorted(octs):
            ta = top[key]
            blocked = road_blocked_sides(links.get(key, [])) if (key in roads or key in hubs) else set()
            for side in range(8):
                if side in blocked:
                    continue
                if side % 2 == 0:
                    da, db = OCT_AXIAL[side]
                    tb = top.get((key[0] + da, key[1] + db), 0)
                else:
                    da, db = OCT_DIAG_SQUARE[side]
                    tb = sq_top.get((key[0] + da, key[1] + db), 0)
                if tb >= ta:
                    continue
                walls += side_walls(octs[key], 45.0 * side, L.APO, tb, ta)
        if p.show_squares:
            for key in sorted(sqs):
                ts = sq_top[key]
                for ang, (da, db) in SQ_SIDE_OCT.items():
                    tb = top.get((key[0] + da, key[1] + db), 0)
                    if tb >= ts:
                        continue
                    walls += side_walls(sqs[key], ang, L.EDGE / 2.0, tb, ts)

    # Parks on the top level of each tower, road octagon (two levels or more), junction and gap square.
    parks = 0
    tree_count = 0
    bench_count = 0
    placed_oct = []                                                 # octagon park cells: (centre, layer, slab height, base z)
    path_rng = random.Random(seed * 7919 + 25)                    # its own sequence, so other parks do not move
    if p.show_parks:
        L_sq = SimpleNamespace(**{**vars(L), "APO": L.EDGE / 2.0})   # trees and benches fit a square
        sites = []                                                  # (centre, top layer, base z, is a square)
        for key in sorted(octs):
            if key in towers:
                sites.append((octs[key], top[key], TILE_TOP, False))
            elif key in roads:
                if hmax[key] >= 2:
                    sites.append((octs[key], hmax[key], ROAD_BASE, False))
            else:
                sites.append((octs[key], hmax[key], TILE_TOP, False))
        if p.show_squares:
            for key in sorted(sqs):
                sites.append((sqs[key], sq_top[key], TILE_TOP, True))
        for c, Hp, zb, is_sq in sites:
            if rng.random() > p.park_density:
                continue
            z_base = zb + (Hp - 1) * P
            if park_src:
                src = park_src[copy_rng.randrange(len(park_src))]
                th = copy_rng.randrange(8) * math.tau / 8.0
                records.append(park_record(src, [c], th, z_base, Hp))
                slab_h = src["height"]
            else:
                ring = square_ring(c, L.EDGE) if is_sq else oct_ring(c, L.R)
                add_prism(bucket("parks", Hp), ring, z_base, z_base + PARK_SLAB_H)
                slab_h = PARK_SLAB_H
            if not is_sq:
                placed_oct.append((c, Hp, slab_h, z_base))
            Lc = L_sq if is_sq else L
            trees = tree_spots(c, p.trees_per_cell, rng, Lc) if p.show_trees and p.tree_scale > 0 else []
            for pos, sc in trees:
                add_tree(bucket("trunks", Hp), bucket("canopies", Hp), pos, z_base + slab_h, sc, Lc)
                tree_count += 1
            if p.show_benches:
                for pos, th_b in bench_spots(c, p.benches_per_cell, trees, rng, Lc):
                    add_bench(bucket("benches", Hp), pos, th_b, z_base + slab_h, Lc)
                    bench_count += 1
            parks += 1

    # Roof paths: on every octagon park cell, joined to the park cell across each axial side that also has one.
    if p.show_paths and placed_oct:
        path_z = {}
        for c, Hp, slab, zb_ in placed_oct:
            path_z[(Hp, round(c.x, 1), round(c.y, 1))] = zb_ + slab + PATH_LIFT_FT * FT
        for c, Hp, slab, zb_ in placed_oct:
            z_path = zb_ + slab + PATH_LIFT_FT * FT
            sides = []
            for m, (dx, dy) in ((0, (1, 0)), (2, (0, 1)), (4, (-1, 0)), (6, (0, -1))):
                nb = c + Vector((dx, dy)) * L.D
                zn = path_z.get((Hp, round(nb.x, 1), round(nb.y, 1)))
                if zn is not None and abs(zn - z_path) < 1e-6:
                    sides.append(m)
            add_oct_path(bucket("paths", Hp), c, z_path, path_rng, L, sides)

    # Cells for the Stats box: every level of every octagon except junctions, less the top (park) level.
    cells_counted = sum(max(0, hmax[key] - 1) for key in octs if key not in hubs)
    if p.show_squares:
        cells_counted += sum(max(0, sq_hmax[key] - 1) for key in sqs)
    stats = {
        "footprint": len(octs),
        "squares": len(sqs),
        "cells_counted": cells_counted,
        "hexes": len(octs),
        "towers": len(towers),
        "transport": len(hubs),
        "walls": walls,
        "parks": parks,
        "trees": tree_count,
        "benches": bench_count,
        "top_layer": max(hmax.values()),
    }
    stats["copies"] = len(records)
    return buckets, stats, records


# ---------------------------------------------------------------------------
# The rectangle city: rectangles that touch edge to edge (no gap squares). Every road rectangle has
# a 12 ft hallway down the middle along its length, like the road octagons have their centre hallway.
# ---------------------------------------------------------------------------
RECT_SHAFT_FT = 14.0                                   # elevator shafts in a junction rectangle: 14 ft square
RECT_SIDES = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}      # side index: east, north, west, south
RECT_SIDE_INDEX = {v: k for k, v in RECT_SIDES.items()}


def rect_layout(p):
    """Rectangle geometry from its own width and length (in metres). The cells touch, so there are no gaps."""
    W, Lg = p.rect_width, p.rect_length
    HW, HL = W / 2.0, Lg / 2.0
    return SimpleNamespace(
        W=W, LG=Lg, HW=HW, HL=HL, APO=min(HW, HL), S=2.0 * min(HW, HL), R=math.hypot(HW, HL),
        TREE_SCALE=p.tree_scale,
        HEX_H=p.level_ft * FT, ROOF_H=p.roof_ft * FT, PITCH=p.level_ft * FT + p.roof_ft * FT,
        WALL_T=WALL_THICK_FRAC * min(W, Lg), ROAD_W=p.road_width_ft * FT,
    )


def rect_ring(c, L):
    return rect_poly(c, Vector((1.0, 0.0)), L.HW, L.HL)


def rect_lattice(inside, lo, hi, rng, L):
    """Rectangle centres inside the footprint, on a grid that touches edge to edge, from a random offset."""
    ox = lo.x + rng.random() * L.W
    oy = lo.y + rng.random() * L.LG
    cells = {}
    i_min = int(math.floor((lo.x - ox) / L.W)) - 2
    i_max = int(math.ceil((hi.x - ox) / L.W)) + 2
    j_min = int(math.floor((lo.y - oy) / L.LG)) - 2
    j_max = int(math.ceil((hi.y - oy) / L.LG)) + 2
    for j in range(j_min, j_max + 1):
        for i in range(i_min, i_max + 1):
            c = Vector((ox + i * L.W, oy + j * L.LG))
            if inside(c.x, c.y):
                cells[(i, j)] = c
    return cells


def rect_blocked_sides(links):
    """Rectangle sides a road or hallway runs through, so no wall there. A Width road (across the width) has
    its hallway down the length, out through the north and south sides. A Length road (along the length) has
    its hallway across the width, out through the east and west sides. A road also leaves by its own side."""
    out = set()
    for dx, dy in links:
        out.add(RECT_SIDE_INDEX[(dx, dy)])
        if dx:
            out |= {1, 3}
        else:
            out |= {0, 2}
    return out


def rect_road_pieces(c, links, L):
    """A road rectangle with no road copies picked: the rectangle less the road strips to each side it
    connects to, and less the 12 ft hallway through its middle, across the road. Returns convex pieces."""
    half_road = L.ROAD_W / 2.0
    half_hall = HALLWAY_HALF_FT * FT
    margin = 0.1
    cuts = []
    for dx, dy in links:
        u = Vector((dx, dy))
        reach = (L.HW if dx else L.HL) + margin
        cuts.append(rect_poly(s_centre(c, u, reach), u, reach / 2.0, half_road))
    if any(dx for dx, dy in links):                                          # a Width road: hallway down the length
        cuts.append(rect_poly(c, Vector((0.0, 1.0)), L.HL + margin, half_hall))
    else:                                                                    # a Length road: hallway across the width
        cuts.append(rect_poly(c, Vector((1.0, 0.0)), L.HW + margin, half_hall))
    pieces = [rect_ring(c, L)]
    for cut in cuts:
        nxt = []
        for piece in pieces:
            nxt.extend(_subtract_convex(piece, cut))
        pieces = nxt
    return pieces


def s_centre(c, u, reach):
    """Centre of a strip that starts at c and runs reach along the unit vector u."""
    return c + u * (reach / 2.0)


def road_rect_record(src, c, z, height, layer, L, turn):
    """A copy of one Road Rectangles object on a road rectangle. Its origin sits at the centre of the
    rectangle. Its X axis is stretched to the rectangle's width and its Y axis to its length, and it is
    stretched to the road height. The second copy is turned 180 degrees."""
    xs = [v.x for v in src["hull"]]
    ys = [v.y for v in src["hull"]]
    ext_x = max(xs) - min(xs)
    ext_y = max(ys) - min(ys)
    kx = L.W / ext_x if ext_x > 1e-9 else 1.0
    ky = L.LG / ext_y if ext_y > 1e-9 else 1.0
    kz = height / src["height"] if src["height"] > 0 else 1.0
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((kx, ky, kz, 1.0)))
    T = Matrix.Translation((c.x, c.y, z))
    return {"group": "Road Rectangles", "cat": "roads", "layer": layer, "src": src,
            "matrix": T @ Matrix.Rotation(turn, 4, 'Z') @ Sz @ C @ src["obj"].matrix_world}


def fit_to_rect(piece, theta, half_w, half_l):
    """Largest uniform XY scale at which the piece, turned by theta, stays inside a rectangle of these
    half sizes centred on the origin."""
    pts = [rot2(p, theta) for p in piece["hull"]]
    reach_x = max(abs(p.x) for p in pts)
    reach_y = max(abs(p.y) for p in pts)
    s = 1e6
    if reach_x > 1e-6:
        s = min(s, half_w / reach_x)
    if reach_y > 1e-6:
        s = min(s, half_l / reach_y)
    return s


def rect_record(pool, rng, c, z, height, layer, half_w, half_l, group, cat):
    """A copy of one of your objects, turned 0 or 180 degrees, scaled evenly to fill the rectangle, and
    stretched to the given height. Used for towers, roofs and the like."""
    src = pool[rng.randrange(len(pool))]
    theta = rng.randrange(2) * math.pi
    s = fit_to_rect(src, theta, half_w, half_l)
    kz = height / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((c.x, c.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s, s, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src,
            "matrix": P_ @ Sc @ T @ src["obj"].matrix_world}


def add_rect_path(bm, c, z0, rng, L, sides):
    """A roof path through one park rectangle, 4 ft wide, as flat quads. sides are the rectangle sides whose
    neighbour also has a path (0 east, 1 north, 2 west, 3 south): the path runs from the centre out to each of
    them, and so meets the next rectangle at its edge. With no neighbours it runs straight across, along a
    random axis."""
    w = PATH_WIDTH_FT * FT
    strips = []
    if sides:
        for side in sorted(sides):
            u = Vector(RECT_SIDES[side])
            reach = L.HW if side in (0, 2) else L.HL
            strips.append(rect_poly(s_centre(c, u, reach), u, reach / 2.0, w / 2.0))
    else:
        u = Vector((1.0, 0.0)) if rng.randrange(2) == 0 else Vector((0.0, 1.0))
        reach = L.HW if u.x else L.HL
        strips.append(rect_poly(c, u, reach, w / 2.0))
    for poly in strips:
        verts = [bm.verts.new((p.x, p.y, z0)) for p in poly]
        try:
            bm.faces.new(verts)
        except ValueError:
            pass


def rect_tree_spots(c, n, rng, L):
    """Up to n tree positions spread over the whole rectangle, kept clear of its edges, with canopies kept
    apart. Returns (position, size scale) pairs."""
    spots = []
    cr_max = TREE_RADIUS_FRAC * getattr(L, "TREE_S", L.S) * 1.15 * getattr(L, "TREE_SCALE", 1.0)
    hx = L.HW - cr_max - 0.5
    hy = L.HL - cr_max - 0.5
    if hx <= 0 or hy <= 0:
        return spots
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        pos = c + Vector((rng.uniform(-hx, hx), rng.uniform(-hy, hy)))
        scale = rng.uniform(0.85, 1.15) * getattr(L, "TREE_SCALE", 1.0)
        if all((pos - q).length >= 2.2 * cr_max for q, _ in spots):
            spots.append((pos, scale))
    return spots


def rect_bench_spots(c, n, trees, rng, L):
    """Up to n bench positions spread over the whole rectangle, clear of the trees and of its edges.
    Returns (position, angle) pairs."""
    spots = []
    hx = L.HW - 2.0
    hy = L.HL - 2.0
    if hx <= 0 or hy <= 0:
        return spots
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        pos = c + Vector((rng.uniform(-hx, hx), rng.uniform(-hy, hy)))
        clear = all((pos - tp).length >= TREE_RADIUS_FRAC * getattr(L, "TREE_S", L.S) * sc + 2.5 for tp, sc in trees)
        clear = clear and all((pos - q).length >= 3.0 for q, _ in spots)
        if clear:
            spots.append((pos, rng.random() * math.tau))
    return spots


def build_rect_city(p, fp=None, index=0):
    """The rectangle city: touching rectangles, a 12 ft hallway down each road rectangle, junctions with
    elevators, towers, walls, roofs, parks, columns and roof paths. Returns (buckets, stats, records) like
    build_octagon_city."""
    L = rect_layout(p)
    seed = p.seed + index                       # each arcology on its own footprint gets its own layout
    if fp is None:
        raise RuntimeError("This arcology has no footprint mesh")
    height_at, lo, hi = mesh_height_tester(fp)
    inside = lambda x, y: height_at(x, y) is not None
    rng = random.Random(seed)
    copy_rng = random.Random(seed * 7919 + 21)
    roof_rng = random.Random(seed * 7919 + 22)
    elev_rng = random.Random(seed * 7919 + 23)
    road_rng = random.Random(seed * 7919 + 24)
    path_rng = random.Random(seed * 7919 + 25)
    records = []
    tower_src = source_pool(p.coll_rectangles)
    width_road_src = source_pool(p.coll_width_road_rectangles)
    length_road_src = source_pool(p.coll_length_road_rectangles)
    roof_src = source_pool(p.coll_roof)
    wall_src = source_pool(p.coll_walls)
    park_src = source_pool(p.coll_parks)
    elev_src = source_pool(p.coll_elevators)

    cells = rect_lattice(inside, lo, hi, rng, L)
    if not cells:
        raise RuntimeError("No rectangles fall inside the footprint.")

    cap = max(1, p.max_layers)
    plane = None
    if p.roof_plane is not None and p.roof_plane.type == 'MESH':
        plane, _, _ = mesh_height_tester(p.roof_plane)
    hmax = {}
    for key, c in cells.items():
        if plane is None:
            n = cap
        else:
            z = plane(c.x, c.y)
            n = 1 if z is None else layers_under(z, L)
        hmax[key] = max(1, min(cap, n))

    edges, degree = oct_random_roads(cells, rng, p.road_chance, p.max_cells_between_roads, p.min_cells_between_roads)
    hubs = {k for k in cells if degree[k] >= 3}              # junctions: transport rectangles
    roads = {k for k in cells if 1 <= degree[k] < 3}          # road rectangles
    towers = {k for k in cells if degree[k] == 0}

    tmin, tmax = sorted((p.tower_min, p.tower_max))
    tower_heights = {}
    for key in sorted(towers):
        tower_heights[key] = min(rng.randint(max(1, tmin), max(1, tmax)), hmax[key])
    top = dict(hmax)
    for key, H in tower_heights.items():
        top[key] = H

    buckets = {}

    def bucket(cat, layer):
        k = (cat, layer)
        if k not in buckets:
            buckets[k] = bmesh.new()
        return buckets[k]

    P = L.PITCH
    H_ = L.HEX_H

    # Ground: a tile under every rectangle, except roads and junctions, which have their own floors.
    for key, c in cells.items():
        if key not in roads and key not in hubs:
            add_prism(bucket("tiles", 1), rect_ring(c, L), TILE_BOT, TILE_TOP)

    def roof(c, z0, z1, layer):
        if roof_src:
            records.append(rect_record(roof_src, roof_rng, c, z0, z1 - z0, layer, L.HW, L.HL, "Roof", "roofs"))
        else:
            add_prism(bucket("roofs", layer), rect_ring(c, L), z0, z1)

    # Towers: a module on each level, a roof above each module.
    if p.show_towers:
        for key in sorted(towers):
            c = cells[key]
            for k in range(tower_heights[key] - 1):
                z = TILE_TOP + k * P
                if tower_src:
                    records.append(rect_record(tower_src, copy_rng, c, z, H_, k + 1, L.HW, L.HL,
                                               "Rectangles", "towers"))
                else:
                    add_prism(bucket("towers", k + 1), rect_ring(c, L), z, z + H_)
                roof(c, z + H_, z + P, k + 2)

    # Road rectangles and junctions: floors and roofs, as the octagon ones.
    for key in sorted(roads | hubs):
        c = cells[key]
        base = ROAD_BASE if key in roads else TILE_TOP
        for k in range(hmax[key] - 1):
            z = base + k * P
            roof(c, z + H_, z + P, k + 2)

    links = {}
    for a, b in edges:
        d = (b[0] - a[0], b[1] - a[1])
        links.setdefault(a, []).append(d)
        links.setdefault(b, []).append((-d[0], -d[1]))

    # Roads: a strip from the centre out to each side the road leaves by. With Road Rectangles picked, each road
    # rectangle gets two copies, 180 degrees apart. Without, it is the rectangle less the strips and the hallway.
    if p.show_roads:
        for key in sorted(roads | hubs):
            c = cells[key]
            dirs = links.get(key, [])
            for k in range(max(1, hmax[key] - 1)):
                z = ROAD_BASE + k * P
                for dx, dy in dirs:
                    u = Vector((dx, dy))
                    reach = L.HW if dx else L.HL
                    add_prism(bucket("roads", k + 1), rect_poly(s_centre(c, u, reach), u, reach / 2.0, L.ROAD_W / 2.0),
                              z, z + ROAD_H)
                if key not in roads:                  # a junction: road strips and elevators only
                    continue
                across = any(dx for dx, dy in dirs)               # a Width road runs across the width
                pool = width_road_src if across else length_road_src
                if pool:
                    pick = pool[road_rng.randrange(len(pool))]
                    for turn in (0.0, math.pi):
                        records.append(road_rect_record(pick, c, z, H_, k + 1, L, turn))
                else:
                    for piece in rect_road_pieces(c, dirs, L):
                        add_prism(bucket("road_rectangles", k + 1), piece, z, z + H_)

    # Elevators: one per level in every junction. Four shafts, one in each quarter, unless Elevators is picked.
    if p.show_elevators:
        elev_h = ELEVATOR_H_FT * FT
        for key in sorted(hubs):
            c = cells[key]
            for k in range(hmax[key]):
                z = TILE_TOP + k * P
                if elev_src:
                    records.append(elevator_record(elev_src, elev_rng, c, z, elev_h, k + 1))
                else:
                    half = RECT_SHAFT_FT * FT / 2.0
                    for sx in (-1.0, 1.0):
                        for sy in (-1.0, 1.0):
                            centre = c + Vector((sx * (L.HW - half), sy * (L.HL - half)))   # flush in each corner
                            add_prism(bucket("elevators", k + 1), rect_poly(centre, Vector((1.0, 0.0)), half, half),
                                      z, z + elev_h)

    # Columns: a 3 ft octagonal column at every rectangle corner, one per level of the stack on that corner.
    if p.show_columns:
        col_r = 1.5 * FT / math.cos(math.radians(22.5))
        col_site = {}
        for key, c in cells.items():
            layers = top[key]
            base = ROAD_BASE if key in roads else TILE_TOP
            for sx, sy in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
                v = c + Vector((sx * L.HW, sy * L.HL))
                site = (round(v.x, 3), round(v.y, 3))
                if site not in col_site or col_site[site][1] < layers:
                    col_site[site] = (v, layers, base)
        for v, layers, base in col_site.values():
            column = oct_ring(v, col_r)
            for k in range(max(0, layers - 1)):
                add_prism(bucket("columns", k + 1), column, base + k * P, base + (k + 1) * P)

    # Walls: on every side that is exposed, where the rectangle across it is shorter.
    walls = 0

    def side_walls(c, ang, reach, length, tb, ta):
        """Walls on one side of a rectangle, from the level above the neighbour up to the rectangle's top."""
        n = Vector((math.cos(math.radians(ang)), math.sin(math.radians(ang))))
        centre = c + n * (reach + L.WALL_T / 2.0)
        tangent = Vector((-n.y, n.x))
        count = 0
        for k in range(max(tb - 1, 0), ta - 1):
            z = TILE_TOP + k * P
            if wall_src:
                records.append(wall_record(wall_src, copy_rng, centre, ang, z, H_, k + 1, L,
                                           length=length, thick=L.WALL_T))
            else:
                add_prism(bucket("walls", k + 1), rect_poly(centre, tangent, length / 2.0, L.WALL_T / 2.0),
                          z, z + H_)
            count += 1
        return count

    if p.show_walls:
        for key in sorted(cells):
            ta = top[key]
            blocked = rect_blocked_sides(links.get(key, [])) if (key in roads or key in hubs) else set()
            for side in range(4):
                if side in blocked:
                    continue
                da, db = RECT_SIDES[side]
                tb = top.get((key[0] + da, key[1] + db), 0)
                if tb >= ta:
                    continue
                across_x = side in (0, 2)                  # east and west walls run north-south
                reach = L.HW if across_x else L.HL
                length = L.LG if across_x else L.W
                walls += side_walls(cells[key], 90.0 * side, reach, length, tb, ta)

    # Parks on the top level of each tower, road rectangle (two levels or more) and junction.
    parks = 0
    tree_count = 0
    bench_count = 0
    placed = []                                                     # (key, centre, layer, slab height, base z)
    if p.show_parks:
        sites = []                                                  # (key, centre, top layer, base z)
        for key in sorted(cells):
            c = cells[key]
            if key in towers:
                sites.append((key, c, top[key], TILE_TOP))
            elif key in roads:
                if hmax[key] >= 2:
                    sites.append((key, c, hmax[key], ROAD_BASE))
            else:
                sites.append((key, c, hmax[key], TILE_TOP))
        for key, c, Hp, zb in sites:
            if rng.random() > p.park_density:
                continue
            z_base = zb + (Hp - 1) * P
            if park_src:
                src = park_src[copy_rng.randrange(len(park_src))]
                th = copy_rng.randrange(2) * math.pi
                records.append(park_record(src, [c], th, z_base, Hp))
                slab_h = src["height"]
            else:
                add_prism(bucket("parks", Hp), rect_ring(c, L), z_base, z_base + PARK_SLAB_H)
                slab_h = PARK_SLAB_H
            placed.append((key, c, Hp, slab_h, z_base))
            trees = rect_tree_spots(c, p.trees_per_cell, rng, L) if p.show_trees and p.tree_scale > 0 else []
            for pos, sc in trees:
                add_tree(bucket("trunks", Hp), bucket("canopies", Hp), pos, z_base + slab_h, sc, L)
                tree_count += 1
            if p.show_benches:
                for pos, th_b in rect_bench_spots(c, p.benches_per_cell, trees, rng, L):
                    add_bench(bucket("benches", Hp), pos, th_b, z_base + slab_h, L)
                    bench_count += 1
            parks += 1

    # Roof paths: on every park rectangle, joined to the park rectangle across each side that also has one.
    if p.show_paths and placed:
        path_z = {}
        for key, c, Hp, slab, zb_ in placed:
            path_z[(Hp, key)] = zb_ + slab + PATH_LIFT_FT * FT
        for key, c, Hp, slab, zb_ in placed:
            z_path = zb_ + slab + PATH_LIFT_FT * FT
            sides = []
            for side, (da, db) in RECT_SIDES.items():
                zn = path_z.get((Hp, (key[0] + da, key[1] + db)))
                if zn is not None and abs(zn - z_path) < 1e-6:
                    sides.append(side)
            add_rect_path(bucket("paths", Hp), c, z_path, path_rng, L, sides)

    # Cells for the Stats box: every level of every rectangle except junctions, less the top (park) level.
    cells_counted = sum(max(0, hmax[key] - 1) for key in cells if key not in hubs)
    stats = {
        "footprint": len(cells),
        "cells_counted": cells_counted,
        "towers": len(towers),
        "transport": len(hubs),
        "walls": walls,
        "parks": parks,
        "trees": tree_count,
        "benches": bench_count,
        "top_layer": max(hmax.values()),
    }
    return buckets, stats, records


# ---------------------------------------------------------------------------
# Penrose city (its own tab, to the right of Rectangle). Penrose P3 rhombs, from de Bruijn's cut and project.
# Thick rhombs (72 and 108 degrees) are towers. Thin rhombs (36 and 144 degrees) are roads. Two wide corners of
# some thin rhombs hold elevator shafts, at least Elevator Distance apart, centre to centre.
# ---------------------------------------------------------------------------
import itertools
import numpy as np

PEN_E = np.array([[math.cos(math.radians(72 * k)), math.sin(math.radians(72 * k))] for k in range(5)])
PEN_F = np.array([[math.cos(math.radians(144 * k)), math.sin(math.radians(144 * k)), 1.0] for k in range(5)])
PEN_SHIFT = np.array([0.0137, 0.0291, 0.0053])
PEN_MIN_EDGE_M = 6.0
PEN_MAX_EDGE_M = 60.0
PEN_ELEV_SHAFT_FT = 14.0                 # elevator shaft: 14 ft along each edge of a wide corner
PEN_COLUMN_R = 1.5 * FT                  # column: a regular pentagon, 3 ft across the corners


def penrose_tiles(extent, shift=None, M=None):
    """Penrose tiles around the origin, out to `extent` tile edges. Returns (corners, kind) pairs: corners are four
    points in edge-length units, kind is 'thick' or 'thin'. Each tile is found once, from its own lattice corner."""
    shift = PEN_SHIFT if shift is None else np.asarray(shift, dtype=float)
    if M is None:
        M = int(math.ceil(0.4 * extent)) + 3
    rng = np.arange(-M, M + 1)
    rest = np.stack(np.meshgrid(rng, rng, rng, rng, indexing="ij"), axis=-1).reshape(-1, 4)
    pairs = []
    for a, b in itertools.combinations(range(5), 2):
        others = [k for k in range(5) if k not in (a, b)]
        pairs.append((a, b, np.linalg.inv(PEN_F[others].T)))
    out = []
    seen = set()
    for n0 in rng:
        grid = np.concatenate([np.full((len(rest), 1), n0), rest], axis=1)
        phys = grid @ PEN_E
        near = np.hypot(phys[:, 0], phys[:, 1]) <= extent + 2.0
        if not near.any():
            continue
        inte = grid @ PEN_F + shift
        for a, b, Minv in pairs:
            coef = inte @ Minv.T
            ok = near & np.all((coef >= 0.0) & (coef < 1.0), axis=1)
            kind = 'thick' if (b - a) in (1, 4) else 'thin'
            for i in np.nonzero(ok)[0]:
                p0 = phys[i]
                corners = np.array([p0, p0 + PEN_E[a], p0 + PEN_E[a] + PEN_E[b], p0 + PEN_E[b]])
                if np.hypot(*corners.mean(axis=0)) > extent:
                    continue
                key = tuple(sorted(tuple(np.round(p, 6)) for p in corners))
                if key in seen:
                    continue
                seen.add(key)
                out.append((corners, kind))
    return out


def pen_layout(p):
    return SimpleNamespace(EDGE=p.tile_edge, S=p.tile_edge, R=p.tile_edge, HEX_H=p.level_ft * FT,
                           TREE_SCALE=p.tree_scale, TREE_S=2.0 / TREE_RADIUS_FRAC,
                           ROOF_H=p.roof_ft * FT, PITCH=p.level_ft * FT + p.roof_ft * FT,
                           WALL_T=WALL_THICK_FRAC * p.tile_edge)


def fit_to_convex(piece, theta, rel):
    """Largest uniform XY scale at which the piece, turned by theta, stays inside the convex polygon rel
    (counter-clockwise, centred on the origin)."""
    pts = [rot2(p, theta) for p in piece["hull"]]
    s = 1e6
    n = len(rel)
    for i in range(n):
        a, b = rel[i], rel[(i + 1) % n]
        d = b - a
        nrm = Vector((d.y, -d.x)).normalized()
        off = nrm.dot(a)
        reach = max(q.dot(nrm) for q in pts)
        if reach > 1e-6 and off > 1e-9:
            s = min(s, off / reach)
    return s if s < 1e6 else 1.0


def rhomb_record(pool, rng, cen, theta_base, rel, z, height, layer, group, cat):
    """A copy of one of your objects, turned along the tile's edge (or half a turn), scaled evenly to fill the tile
    and stretched to the given height. height None keeps the object's own height."""
    src = pool[rng.randrange(len(pool))]
    theta = theta_base + rng.randrange(2) * math.pi
    s = fit_to_convex(src, theta, rel)
    h = src["height"] if height is None else height
    kz = h / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((cen.x, cen.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s, s, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src, "height": h,
            "matrix": P_ @ Sc @ T @ src["obj"].matrix_world}


def _pen_ccw(pts):
    n = len(pts)
    area2 = sum(pts[i].x * pts[(i + 1) % n].y - pts[(i + 1) % n].x * pts[i].y for i in range(n))
    return list(pts) if area2 >= 0 else list(reversed(pts))


def _pen_key(q):
    return (round(q.x, 4), round(q.y, 4))


def _pen_edge_key(a, b):
    return tuple(sorted((_pen_key(a), _pen_key(b))))


def _pen_angle(v, vn, vp):
    """Interior angle in degrees at corner v between its two edges."""
    u = (vn - v).normalized()
    w = (vp - v).normalized()
    return math.degrees(math.acos(max(-1.0, min(1.0, u.dot(w)))))


def _pen_inside(pts, q, margin=0.0):
    """True when q is inside the counter-clockwise convex polygon pts, at least margin from every edge."""
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        d = b - a
        nrm = Vector((d.y, -d.x)).normalized()
        if (q - a).dot(nrm) > -margin:
            return False
    return True


def _pen_ribbon(bm, a, b, w, z):
    """A flat strip of width w from a to b, at height z."""
    d = b - a
    if d.length < 1e-9:
        return
    n = Vector((-d.normalized().y, d.normalized().x)) * (w / 2.0)
    quad = [a + n, b + n, b - n, a - n]
    verts = [bm.verts.new((q.x, q.y, z)) for q in quad]
    try:
        bm.faces.new(verts)
    except ValueError:
        pass


def _pen_pentagon(c, r):
    return [Vector((c.x + r * math.cos(math.radians(90 + 72 * k)), c.y + r * math.sin(math.radians(90 + 72 * k))))
            for k in range(5)]


def _pen_shaft(t, ci, S):
    """A parallelogram shaft in the wide corner ci of a thin tile: S along each of the two edges that meet there."""
    pts = t["pts"]
    n = len(pts)
    V = pts[ci]
    u = (pts[(ci + 1) % n] - V).normalized() * S
    w = (pts[(ci - 1) % n] - V).normalized() * S
    return _pen_ccw([V, V + u, V + u + w, V + w])


def pen_tree_spots(t, n, rng, L):
    """Up to n tree positions inside one tile, kept clear of its edges, with canopies apart. Returns (position, scale)."""
    spots = []
    cr_max = TREE_RADIUS_FRAC * getattr(L, "TREE_S", L.S) * 1.15 * getattr(L, "TREE_SCALE", 1.0)
    xs = [q.x for q in t["pts"]]
    ys = [q.y for q in t["pts"]]
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        pos = Vector((rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys))))
        if not _pen_inside(t["pts"], pos, cr_max + 0.5):
            continue
        scale = rng.uniform(0.85, 1.15) * getattr(L, "TREE_SCALE", 1.0)
        if all((pos - q).length >= 2.2 * cr_max for q, _ in spots):
            spots.append((pos, scale))
    return spots


def pen_bench_spots(t, n, trees, rng, L):
    """Up to n bench positions inside one tile, clear of the trees and of its edges. Returns (position, angle)."""
    spots = []
    xs = [q.x for q in t["pts"]]
    ys = [q.y for q in t["pts"]]
    for _ in range(n * 40):
        if len(spots) >= n:
            break
        pos = Vector((rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys))))
        if not _pen_inside(t["pts"], pos, 2.0):
            continue
        clear = all((pos - tp).length >= TREE_RADIUS_FRAC * getattr(L, "TREE_S", L.S) * sc + 2.5 for tp, sc in trees)
        clear = clear and all((pos - q).length >= 3.0 for q, _ in spots)
        if clear:
            spots.append((pos, rng.random() * math.tau))
    return spots




def _pen_strip(t, width):
    """The road of a thin tile: a 12 ft strip along its long diagonal, from one 36-degree vertex to the other. It
    may cut a little into the towers beside it."""
    A = t["pts"][t["acute"][0]]
    B = t["pts"][t["acute"][1]]
    u = (B - A).normalized()
    off = 0.0                                  # the road runs from vertex to vertex
    a = A + u * off
    b = B - u * off
    n = Vector((-u.y, u.x)) * (width / 2.0)
    quad = _pen_ccw([a + n, b + n, b - n, a - n])
    cen = sum(quad, Vector((0.0, 0.0))) / 4.0
    rel = [q - cen for q in quad]
    t["road_pts"] = quad
    t["road_cen"] = cen
    t["road_rel"] = rel
    t["road_theta"] = math.atan2(rel[1].y - rel[0].y, rel[1].x - rel[0].x)


def build_penrose_city(p, fp=None, index=0):
    """The Penrose city: thick tiles are towers, thin tiles are roads, with roofs, walls, parks, trees, benches,
    paths, columns and elevator shafts. Returns (buckets, stats, records) like the other shapes."""
    if fp is None:
        raise RuntimeError("This arcology has no footprint mesh")
    L = pen_layout(p)
    seed = p.seed + index
    height_at, lo, hi = mesh_height_tester(fp)
    inside = lambda x, y: height_at(x, y) is not None
    rng = random.Random(seed)
    copy_rng = random.Random(seed * 7919 + 21)
    roof_rng = random.Random(seed * 7919 + 22)
    road_rng = random.Random(seed * 7919 + 24)
    elev_rng = random.Random(seed * 7919 + 23)
    tower_src = source_pool(p.coll_thick_rhombs)
    road_src = source_pool(p.coll_thin_rhombs)
    thick_roof_src = source_pool(p.coll_thick_roofs)
    thin_roof_src = source_pool(p.coll_thin_roofs)
    thick_park_src = source_pool(p.coll_thick_parks)
    thin_park_src = source_pool(p.coll_thin_parks)
    wall_src = source_pool(p.coll_walls)
    elev_src = source_pool(p.coll_elevators)
    records = []

    # Each seed gives a slightly different placement of the tiling on the footprint.
    shift = PEN_SHIFT + np.array([rng.random(), rng.random(), rng.random()]) * 0.05
    cx, cy = (lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0
    reach = math.hypot(hi.x - lo.x, hi.y - lo.y) / 2.0
    raw = penrose_tiles(reach / L.EDGE + 2.0, shift=shift)

    cap = max(1, p.max_layers)
    plane = None
    if p.roof_plane is not None and p.roof_plane.type == 'MESH':
        plane, _, _ = mesh_height_tester(p.roof_plane)
    tiles = []
    for corners, kind in raw:
        pts_u = corners * L.EDGE + np.array([cx, cy])
        pts = _pen_ccw([Vector((float(x), float(y))) for x, y in pts_u])
        cen = sum(pts, Vector((0.0, 0.0))) / 4.0
        if not inside(cen.x, cen.y):
            continue
        rel = [q - cen for q in pts]
        theta_base = math.atan2(rel[1].y - rel[0].y, rel[1].x - rel[0].x)
        wide = [i for i in range(4) if _pen_angle(pts[i], pts[(i + 1) % 4], pts[i - 1]) > 90.0]
        acute = [i for i in range(4) if _pen_angle(pts[i], pts[(i + 1) % 4], pts[i - 1]) < 90.0]
        if plane is None:
            n = cap
        else:
            z = plane(cen.x, cen.y)
            n = 1 if z is None else layers_under(z, L)
        tiles.append({"pts": pts, "rel": rel, "cen": cen, "theta": theta_base, "kind": kind,
                      "hmax": max(1, min(cap, n)), "wide": wide, "acute": acute})
    if not tiles:
        raise RuntimeError("No Penrose tiles fall inside the footprint.")
    for t in tiles:
        if t["kind"] == 'thin' and len(t["wide"]) == 2 and len(t["acute"]) == 2:
            _pen_strip(t, p.road_width_ft * FT)

    tmin, tmax = sorted((p.tower_min, p.tower_max))
    for t in tiles:
        if t["kind"] == 'thick':
            t["top"] = min(rng.randint(max(1, tmin), max(1, tmax)), t["hmax"])
        else:
            t["top"] = t["hmax"]

    buckets = {}

    def bucket(cat, layer):
        k = (cat, layer)
        if k not in buckets:
            buckets[k] = bmesh.new()
        return buckets[k]

    P = L.PITCH
    H_ = L.HEX_H

    def roof(t, z0, z1, layer):
        pool = thick_roof_src if t["kind"] == 'thick' else thin_roof_src
        if pool:
            records.append(rhomb_record(pool, roof_rng, t["cen"], t["theta"], t["rel"], z0, z1 - z0, layer,
                                        "Roof", "roofs"))
        else:
            add_prism(bucket("roofs", layer), t["pts"], z0, z1)

    # Ground: a tile under every tile. The roads of the thin tiles are raised strips on top of it.
    for t in tiles:
        add_prism(bucket("tiles", 1), t["pts"], TILE_BOT, TILE_TOP)

    # Towers: a module on each level of every thick tile, with a roof above each module.
    if p.show_towers:
        for t in tiles:
            if t["kind"] != 'thick':
                continue
            for k in range(t["top"] - 1):
                z = TILE_TOP + k * P
                if tower_src:
                    records.append(rhomb_record(tower_src, copy_rng, t["cen"], t["theta"], t["rel"], z, H_, k + 1,
                                                "Thick Rhombs", "towers"))
                else:
                    add_prism(bucket("towers", k + 1), t["pts"], z, z + H_)
                roof(t, z + H_, z + P, k + 2)

    # Roads: a surface on each thin tile at ground level, and a roof over each road level above it.
    if p.show_roads:
        for t in tiles:
            if t["kind"] != 'thin':
                continue
            for k in range(max(1, t["top"] - 1)):
                z = ROAD_BASE + k * P
                if road_src:
                    records.append(rhomb_record(road_src, road_rng, t["road_cen"], t["road_theta"], t["road_rel"],
                                                z, ROAD_H, k + 1, "Thin Rhombs", "road_rectangles"))
                else:
                    add_prism(bucket("road_rectangles", k + 1), t["road_pts"], z, z + ROAD_H)
            for k in range(t["top"] - 1):
                roof(t, ROAD_BASE + k * P + H_, ROAD_BASE + (k + 1) * P, k + 2)

    # Elevators: two shafts in the wide corners of each chosen thin tile, one per level. Chosen tiles are at least
    # Elevator Distance apart, centre to centre, picked in a random order from the seed.
    elev_count = 0
    if p.show_elevators:
        cand = [i for i, t in enumerate(tiles) if t["kind"] == 'thin' and len(t["wide"]) == 2]
        elev_rng.shuffle(cand)
        chosen = []
        for i in cand:
            c = tiles[i]["cen"]
            if all((c - tiles[j]["cen"]).length >= p.elevator_distance for j in chosen):
                chosen.append(i)
        elev_h = ELEVATOR_H_FT * FT
        S = PEN_ELEV_SHAFT_FT * FT
        for i in chosen:
            t = tiles[i]
            for ci in t["wide"]:
                shaft = _pen_shaft(t, ci, S)
                scen = sum(shaft, Vector((0.0, 0.0))) / 4.0
                srel = [q - scen for q in shaft]
                sth = math.atan2(srel[1].y - srel[0].y, srel[1].x - srel[0].x)
                for k in range(t["top"]):
                    z = ROAD_BASE + k * P
                    if elev_src:
                        records.append(rhomb_record(elev_src, elev_rng, scen, sth, srel, z, elev_h, k + 1,
                                                    "Elevators", "elevators"))
                    else:
                        add_prism(bucket("elevators", k + 1), shaft, z, z + elev_h)
                elev_count += 1

    # Walls: on every edge of a thick tile that has no neighbour, or a neighbour with fewer levels.
    edge_map = {}
    for idx, t in enumerate(tiles):
        for i in range(4):
            edge_map.setdefault(_pen_edge_key(t["pts"][i], t["pts"][(i + 1) % 4]), []).append(idx)
    walls = 0
    if p.show_walls:
        for idx, t in enumerate(tiles):
            ta = t["top"]
            base = TILE_TOP if t["kind"] == 'thick' else ROAD_BASE
            for i in range(4):
                a, b = t["pts"][i], t["pts"][(i + 1) % 4]
                others = [j for j in edge_map[_pen_edge_key(a, b)] if j != idx]
                tb = tiles[others[0]]["top"] if others else 0
                if others and t["kind"] == 'thin':
                    continue    # roads open onto the tiles beside them
                if tb >= ta:
                    continue
                d = b - a
                nout = Vector((d.y, -d.x)).normalized()
                if nout.dot(t["cen"] - a) > 0:
                    nout = -nout
                quad = [a, b, b - nout * L.WALL_T, a - nout * L.WALL_T]
                wc = (a + b) / 2.0 - nout * (L.WALL_T / 2.0)
                wang = math.degrees(math.atan2(nout.y, nout.x))
                for k in range(max(tb - 1, 0), ta - 1):
                    z = base + k * P
                    if wall_src:
                        records.append(wall_record(wall_src, copy_rng, wc, wang, z, H_, k + 1, L,
                                                   length=d.length, thick=L.WALL_T))
                    else:
                        add_prism(bucket("walls", k + 1), quad, z, z + H_)
                    walls += 1

    # Parks: on the top level of each thick tile, and on each thin road tile with two levels or more.
    parks = 0
    placed = {}                                     # tile index -> (top layer, surface height)
    if p.show_parks:
        for idx, t in enumerate(tiles):
            if t["kind"] == 'thick':
                zb, Hp = TILE_TOP, t["top"]
            else:
                if t["top"] < 2:
                    continue
                zb, Hp = ROAD_BASE, t["top"]
            if rng.random() > p.park_density:
                continue
            z_base = zb + (Hp - 1) * P
            pool = thick_park_src if t["kind"] == 'thick' else thin_park_src
            if pool:
                rec = rhomb_record(pool, copy_rng, t["cen"], t["theta"], t["rel"], z_base, None, Hp,
                                   "Parks", "parks")
                records.append(rec)
                slab_h = rec["height"]
            else:
                add_prism(bucket("parks", Hp), t["pts"], z_base, z_base + PARK_SLAB_H)
                slab_h = PARK_SLAB_H
            placed[idx] = (Hp, z_base + slab_h)
            parks += 1

    # Trees and benches on the parks.
    tree_count = 0
    bench_count = 0
    if placed and (p.show_trees or p.show_benches):
        for idx, (Hp, zs) in placed.items():
            t = tiles[idx]
            trees = pen_tree_spots(t, p.trees_per_cell, rng, L) if p.show_trees and p.tree_scale > 0 else []
            for pos, sc in trees:
                add_tree(bucket("trunks", Hp), bucket("canopies", Hp), pos, zs, sc, L)
                tree_count += 1
            if p.show_benches:
                for pos, th_b in pen_bench_spots(t, p.benches_per_cell, trees, rng, L):
                    add_bench(bucket("benches", Hp), pos, th_b, zs, L)
                    bench_count += 1

    # Roof paths: from each park's centre out to the edge midpoint of every neighbouring park on the same level.
    if p.show_paths and placed:
        w = PATH_WIDTH_FT * FT
        for idx, (Hp, zs) in placed.items():
            t = tiles[idx]
            zp = zs + PATH_LIFT_FT * FT
            bk = bucket("paths", Hp)
            sides = []
            for i in range(4):
                a, b = t["pts"][i], t["pts"][(i + 1) % 4]
                others = [j for j in edge_map[_pen_edge_key(a, b)] if j != idx]
                j = others[0] if others else None
                if j is not None and j in placed and placed[j][0] == Hp and abs(placed[j][1] - zs) < 1e-6:
                    sides.append(i)
            if sides:
                for i in sides:
                    _pen_ribbon(bk, t["cen"], (t["pts"][i] + t["pts"][(i + 1) % 4]) / 2.0, w, zp)
            else:
                i = rng.randrange(4)
                for i2 in (i, (i + 2) % 4):
                    a, b = t["pts"][i2], t["pts"][(i2 + 1) % 4]
                    _pen_ribbon(bk, t["cen"], (a + b) / 2.0, w, zp)

    # Columns: a 3 ft pentagon at every tile corner, one per level of the tallest tile on that corner.
    if p.show_columns:
        corner_site = {}
        for t in tiles:
            base = TILE_TOP if t["kind"] == 'thick' else ROAD_BASE
            for q in t["pts"]:
                key = _pen_key(q)
                cur = corner_site.get(key)
                if cur is None or t["top"] > cur[1]:
                    corner_site[key] = (q, t["top"], base)
        for q, layers, base in corner_site.values():
            pent = _pen_pentagon(q, PEN_COLUMN_R)
            for k in range(max(0, layers - 1)):
                add_prism(bucket("columns", k + 1), pent, base + k * P, base + (k + 1) * P)

    thick = [t for t in tiles if t["kind"] == 'thick']
    stats = {
        "footprint": len(tiles),
        "thick": len(thick),
        "cells_counted": sum(max(0, t["top"] - 1) for t in thick),
        "towers": len(thick),
        "transport": elev_count,
        "elevators": elev_count,
        "walls": walls,
        "parks": parks,
        "trees": tree_count,
        "benches": bench_count,
        "top_layer": max(t["top"] for t in tiles),
    }
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


def rebuild(scene, entry):
    """Clears and rebuilds one arcology: the selected one. The other arcologies in the list are not touched."""
    shared = scene.arcology_shared               # also holds the shared Live switch and status line
    t0 = time.perf_counter()
    if entry is None:
        shared.status = "Add an arcology with +"
        return None
    ensure_uids(scene)
    number = next((i + 1 for i, e in enumerate(scene.arcologies) if e.uid == entry.uid), 0)
    clear_arcology(entry)
    fp = entry.footprint
    if fp is None or fp.type != 'MESH':
        shared.status = f"Arcology {number}: no footprint. Pick a mesh for it first"
        return None
    is_rect = entry.shape == SHAPE_RECT
    is_oct = entry.shape == SHAPE_OCT
    props = {SHAPE_PEN: entry.penrose_settings, SHAPE_RECT: entry.rect_settings,
             SHAPE_OCT: entry.oct_settings}.get(entry.shape, entry.hex_settings)
    target = child_collection(output_collection(scene), arcology_role(entry))
    if entry.shape == SHAPE_PEN:
        buckets, stats, records = build_penrose_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_thick = stats["thick"]
        props.last_cell_count = stats["cells_counted"]
        footprint, unit = stats["footprint"], "tiles"
    elif is_rect:
        buckets, stats, records = build_rect_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_cell_count = stats["cells_counted"]
        footprint, unit = stats["footprint"], "rectangles"
    elif is_oct:
        buckets, stats, records = build_octagon_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_cell_count = stats["cells_counted"]
        footprint, unit = stats["footprint"], "octagons"
    else:
        buckets, stats, records = build_city(props, fp, number - 1)
        props.last_footprint = stats["hexes"]
        props.last_hex_count = stats["cells_counted"]
        footprint, unit = stats["hexes"], "hexes"
    n_objects = write_output(target, buckets) + write_copies(target, records)
    prune_output_collections()
    dt = (time.perf_counter() - t0) * 1000.0
    shared.status = (f"Arcology {number}: {footprint} {unit} across the footprint, top layer {stats['top_layer']}, "
                     f"{stats['towers']} towers, {stats['walls']} walls, {stats['parks']} park cells, "
                     f"{stats['trees']} trees, {stats['benches']} benches, {n_objects} objects, {dt:.0f} ms")
    return None


def _timer_fire():
    global _pending
    _pending = False
    scene = bpy.context.scene
    try:
        rebuild(scene, active_entry(scene))    # live: only the arcology the panel shows
    except Exception as exc:                       # keep the add-on alive; show the error in the panel
        traceback.print_exc()
        try:
            scene.arcology_shared.status = f"Error: {exc}"
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


def _auto_rebuild():
    """Schedules a live rebuild while the Live switch is on. Any arcology's change rebuilds them all."""
    scene = bpy.context.scene
    if scene is not None and scene.arcology_shared.live:
        schedule_rebuild()


def _on_change(self, context):
    _auto_rebuild()


def _on_oct_change(self, context):
    _auto_rebuild()


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
    roof_plane: PointerProperty(
        name="Roof Plane", type=bpy.types.Object, poll=_is_mesh, update=_on_change,
        description="Optional. Its height above each hex sets that hex's layer count, so it can be sculpted")
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
    road_width_ft: FloatProperty(name="Road width (ft)", default=20.0, min=6.0, max=60.0, update=_on_change)
    road_width_shown: FloatProperty(
        name="Road width", precision=2, min=1.8, max=18.0, update=_on_change,
        get=lambda self: self.road_width_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "road_width_ft", value, 6.0, 60.0),
        description="Width of a road. Shown in the unit picked by the button beside Size")
    max_layers: IntProperty(name="Max layers", default=12, min=1, max=24, update=_on_change,
                            description="The most layers any hex can have")
    tower_min: IntProperty(name="Tower min layers", default=10, min=1, max=24, update=_on_change)
    tower_max: IntProperty(name="Tower max layers", default=12, min=1, max=24, update=_on_change)
    road_chance: FloatProperty(name="Road chance", default=0.12, min=0.0, max=1.0, subtype='FACTOR',
                               update=_on_change, description="Chance that a straight line of hexes is a road")
    max_cells_between_roads: IntProperty(
        name="Max Cells Between Roads", default=3, min=1, max=12, update=_on_change,
        description="The most rows or columns of hexes that can sit between two roads. A road is forced when reached")
    min_cells_between_roads: IntProperty(
        name="Min Cells Between Roads", default=1, min=0, max=12, update=_on_change,
        description="The fewest rows or columns of hexes that must sit between two roads, so roads are never back to back")
    tree_scale: FloatProperty(
        name="Tree Scale", default=1.0, min=0.0, max=3.0, update=_on_change,
        description="Scales every tree, its canopy and its trunk. 0 hides the trees, 3 is three times the size")
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


def _set_oct_edge_shown(props, value):
    """Store the typed or dragged octagon edge in metres, clamped to 5 to 80 m."""
    metres = value * (FT if props.size_in_feet else 1.0)
    props["oct_edge"] = min(max(metres, 5.0), 80.0)


class OctagonProps(PropertyGroup):
    """Settings for the octagon city. Kept apart from the hexagon settings, so each city has its own values."""
    roof_plane: PointerProperty(
        name="Roof Plane", type=bpy.types.Object, poll=_is_mesh, update=_on_oct_change,
        description="Optional. Its height above each octagon sets that octagon's layer count, so it can be sculpted")
    seed: IntProperty(name="Seed", default=42, min=0, max=999999, update=_on_oct_change)
    oct_edge: FloatProperty(name="Octagon edge length", default=33.35 / math.sqrt(3.0), min=5.0, max=80.0,
                            update=_on_oct_change,
                            description="Length of one side of an octagon, in metres. The gap squares have the same side")
    size_in_feet: BoolProperty(name="Feet", default=False,
                               description="Show the octagon edge length and heights in feet instead of meters")
    oct_edge_shown: FloatProperty(
        name="Octagon edge length", precision=2, min=0.5, max=300.0, update=_on_oct_change,
        get=lambda self: self.oct_edge / (FT if self.size_in_feet else 1.0),
        set=lambda self, value: _set_oct_edge_shown(self, value),
        description="Length of one side of an octagon. Shown in the unit picked by the button beside Size")
    level_ft: FloatProperty(name="Level height (ft)", default=30.0, min=8.0, max=60.0, update=_on_oct_change)
    roof_ft: FloatProperty(name="Roof height (ft)", default=6.0, min=0.5, max=20.0, update=_on_oct_change)
    level_shown: FloatProperty(
        name="Level height", precision=2, min=2.0, max=60.0, update=_on_oct_change,
        get=lambda self: self.level_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "level_ft", value, 8.0, 60.0),
        description="Height of one level. Shown in the unit picked by the button beside Size")
    roof_shown: FloatProperty(
        name="Roof height", precision=2, min=0.15, max=20.0, update=_on_oct_change,
        get=lambda self: self.roof_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "roof_ft", value, 0.5, 20.0),
        description="Height of the roof slab. Shown in the unit picked by the button beside Size")
    road_width_ft: FloatProperty(name="Road width (ft)", default=20.0, min=6.0, max=60.0, update=_on_oct_change)
    road_width_shown: FloatProperty(
        name="Road width", precision=2, min=1.8, max=18.0, update=_on_oct_change,
        get=lambda self: self.road_width_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "road_width_ft", value, 6.0, 60.0),
        description="Width of a road. Shown in the unit picked by the button beside Size")
    max_layers: IntProperty(name="Max layers", default=12, min=1, max=24, update=_on_oct_change,
                            description="The most layers any octagon or square can have")
    tower_min: IntProperty(name="Tower min layers", default=10, min=1, max=24, update=_on_oct_change)
    tower_max: IntProperty(name="Tower max layers", default=12, min=1, max=24, update=_on_oct_change)
    road_chance: FloatProperty(name="Road chance", default=0.12, min=0.0, max=1.0, subtype='FACTOR',
                               update=_on_oct_change,
                               description="Chance that a row or column of octagons is a road")
    max_cells_between_roads: IntProperty(
        name="Max Cells Between Roads", default=3, min=1, max=12, update=_on_oct_change,
        description="The most rows or columns of octagons that can sit between two roads. A road is forced when reached")
    min_cells_between_roads: IntProperty(
        name="Min Cells Between Roads", default=1, min=0, max=12, update=_on_oct_change,
        description="The fewest rows or columns of octagons that must sit between two roads, so roads are never back to back")
    tree_scale: FloatProperty(
        name="Tree Scale", default=1.0, min=0.0, max=3.0, update=_on_oct_change,
        description="Scales every tree, its canopy and its trunk. 0 hides the trees, 3 is three times the size")
    park_density: FloatProperty(name="Park density", default=0.8, min=0.0, max=1.0, subtype='FACTOR',
                                update=_on_oct_change, description="Chance that a roof site gets a park")
    show_towers: BoolProperty(name="Towers", default=True, update=_on_oct_change)
    show_roads: BoolProperty(name="Roads", default=True, update=_on_oct_change)
    show_squares: BoolProperty(name="Squares", default=True, update=_on_oct_change,
                               description="Square towers in the gaps between the octagons")
    show_parks: BoolProperty(name="Parks", default=True, update=_on_oct_change)
    show_elevators: BoolProperty(name="Elevators", default=True, update=_on_oct_change)
    show_walls: BoolProperty(name="Walls", default=True, update=_on_oct_change)
    show_trees: BoolProperty(name="Trees", default=True, update=_on_oct_change)
    show_benches: BoolProperty(name="Benches", default=True, update=_on_oct_change)
    show_columns: BoolProperty(name="Columns", default=True, update=_on_oct_change,
                               description="3 ft columns at every octagon corner, one 36 ft column per level")
    show_paths: BoolProperty(name="Paths", default=True, update=_on_oct_change,
                             description="4 ft roof paths on the park cells, six flat planes per cell, joined to neighbours")
    coll_octagons: PointerProperty(
        name="Octagons", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied to fill the octagon towers. Empty = generated octagons")
    coll_road_octagons: PointerProperty(
        name="Road Octagons", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the road and junction octagons, at road height. "
                    "The road runs along the object's X axis. Empty = generated road strips")
    coll_squares: PointerProperty(
        name="Squares", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied to fill the square towers. Empty = generated squares")
    coll_parks: PointerProperty(
        name="Parks", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the roofs as parks. Empty = generated parks")
    coll_walls: PointerProperty(
        name="Walls", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the exposed sides as walls. Empty = generated walls")
    coll_roof: PointerProperty(
        name="Octagon Roofs", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the octagon roofs. Empty = generated roofs")
    coll_square_roofs: PointerProperty(
        name="Square Roofs", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the square roofs. Empty = generated roofs")
    coll_elevators: PointerProperty(
        name="Elevators", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied into the transport octagons. Empty = generated shafts")
    trees_per_cell: IntProperty(name="Trees per park octagon", default=5, min=0, max=30, update=_on_oct_change,
                                description="Random trees placed in each park octagon")
    benches_per_cell: IntProperty(name="Benches per park octagon", default=2, min=0, max=30, update=_on_oct_change,
                                  description="Random benches placed in each park octagon")
    last_cell_count: IntProperty(name="Octagon cells", default=0,
                                 description="Octagon cells in the last build, counted at every level, excluding junctions and the park layer (set by Build)")
    last_footprint: IntProperty(name="Footprint octagons", default=0,
                                description="Octagons across the footprint in the last build (set by Build)")
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of octagon cells that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per octagon", default=20, min=0,
                                description="Residents in each residential octagon cell")
    stat_cost: FloatProperty(name="Cost Per Octagon ($M)", default=2.0, min=0.0, precision=2,
                             description="Build cost of one octagon cell, in millions of dollars")
    stat_household: FloatProperty(name="Persons Per Household", default=2.6, min=0.1, max=20.0, precision=1,
                                  description="Average number of people in each household")
    stat_mortgage_rate: FloatProperty(name="Mortgage Rate", default=7.0, min=0.0, max=30.0, precision=2,
                                      subtype='PERCENTAGE',
                                      description="Annual mortgage interest rate. The loan is a 30-year fixed mortgage")


def _set_rect_size_shown(props, key, value):
    """Store a typed or dragged width or length in metres, clamped to 6 to 120 m (20 to 394 ft)."""
    metres = value * (FT if props.size_in_feet else 1.0)
    props[key] = min(max(metres, 6.0), 120.0)


def _on_rect_change(self, context):
    _auto_rebuild()


class RectProps(PropertyGroup):
    """Settings for the rectangle city. Kept apart from the other shapes' settings, so each arcology has its own values."""
    roof_plane: PointerProperty(
        name="Roof Plane", type=bpy.types.Object, poll=_is_mesh, update=_on_rect_change,
        description="Optional. Its height above each rectangle sets that rectangle's layer count, so it can be sculpted")
    seed: IntProperty(name="Seed", default=42, min=0, max=999999, update=_on_rect_change)
    rect_width: FloatProperty(name="Rectangle width", default=80.0 * FT, min=6.0, max=120.0,
                              update=_on_rect_change,
                              description="Width of one rectangle, across the hallway, in metres")
    rect_length: FloatProperty(name="Rectangle length", default=120.0 * FT, min=6.0, max=120.0,
                               update=_on_rect_change,
                               description="Length of one rectangle, along the hallway, in metres")
    size_in_feet: BoolProperty(name="Feet", default=False,
                               description="Show the width, length and heights in feet instead of meters")
    rect_width_shown: FloatProperty(
        name="Rectangle width", precision=2, min=1.0, max=400.0, update=_on_rect_change,
        get=lambda self: self.rect_width / (FT if self.size_in_feet else 1.0),
        set=lambda self, value: _set_rect_size_shown(self, "rect_width", value),
        description="Width of one rectangle. Shown in the unit picked by the button beside Size")
    rect_length_shown: FloatProperty(
        name="Rectangle length", precision=2, min=1.0, max=400.0, update=_on_rect_change,
        get=lambda self: self.rect_length / (FT if self.size_in_feet else 1.0),
        set=lambda self, value: _set_rect_size_shown(self, "rect_length", value),
        description="Length of one rectangle, along the hallway. Shown in the unit picked by the button beside Size")
    level_ft: FloatProperty(name="Level height (ft)", default=30.0, min=8.0, max=60.0, update=_on_rect_change)
    roof_ft: FloatProperty(name="Roof height (ft)", default=6.0, min=0.5, max=20.0, update=_on_rect_change)
    level_shown: FloatProperty(
        name="Level height", precision=2, min=2.0, max=60.0, update=_on_rect_change,
        get=lambda self: self.level_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "level_ft", value, 8.0, 60.0),
        description="Height of one level. Shown in the unit picked by the button beside Size")
    roof_shown: FloatProperty(
        name="Roof height", precision=2, min=0.15, max=20.0, update=_on_rect_change,
        get=lambda self: self.roof_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "roof_ft", value, 0.5, 20.0),
        description="Height of the roof slab. Shown in the unit picked by the button beside Size")
    road_width_ft: FloatProperty(name="Road width (ft)", default=20.0, min=6.0, max=60.0, update=_on_rect_change)
    road_width_shown: FloatProperty(
        name="Road width", precision=2, min=1.8, max=18.0, update=_on_rect_change,
        get=lambda self: self.road_width_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "road_width_ft", value, 6.0, 60.0),
        description="Width of a road. Shown in the unit picked by the button beside Size")
    max_layers: IntProperty(name="Max layers", default=12, min=1, max=24, update=_on_rect_change,
                            description="The most layers any rectangle can have")
    tower_min: IntProperty(name="Tower min layers", default=10, min=1, max=24, update=_on_rect_change)
    tower_max: IntProperty(name="Tower max layers", default=12, min=1, max=24, update=_on_rect_change)
    road_chance: FloatProperty(name="Road chance", default=0.12, min=0.0, max=1.0, subtype='FACTOR',
                               update=_on_rect_change,
                               description="Chance that a row or column of rectangles is a road")
    max_cells_between_roads: IntProperty(
        name="Max Cells Between Roads", default=3, min=1, max=12, update=_on_rect_change,
        description="The most rows or columns of rectangles that can sit between two roads. A road is forced when reached")
    min_cells_between_roads: IntProperty(
        name="Min Cells Between Roads", default=1, min=0, max=12, update=_on_rect_change,
        description="The fewest rows or columns of rectangles that must sit between two roads, so roads are never back to back")
    tree_scale: FloatProperty(
        name="Tree Scale", default=1.0, min=0.0, max=3.0, update=_on_rect_change,
        description="Scales every tree, its canopy and its trunk. 0 hides the trees, 3 is three times the size")
    park_density: FloatProperty(name="Park density", default=0.8, min=0.0, max=1.0, subtype='FACTOR',
                                update=_on_rect_change, description="Chance that a roof site gets a park")
    show_towers: BoolProperty(name="Towers", default=True, update=_on_rect_change)
    show_roads: BoolProperty(name="Roads", default=True, update=_on_rect_change)
    show_parks: BoolProperty(name="Parks", default=True, update=_on_rect_change)
    show_elevators: BoolProperty(name="Elevators", default=True, update=_on_rect_change)
    show_walls: BoolProperty(name="Walls", default=True, update=_on_rect_change)
    show_trees: BoolProperty(name="Trees", default=True, update=_on_rect_change)
    show_benches: BoolProperty(name="Benches", default=True, update=_on_rect_change)
    show_columns: BoolProperty(name="Columns", default=True, update=_on_rect_change,
                               description="3 ft columns at every rectangle corner, one 36 ft column per level")
    show_paths: BoolProperty(name="Paths", default=True, update=_on_rect_change,
                             description="4 ft roof paths on the park rectangles, joined to neighbours")
    coll_rectangles: PointerProperty(
        name="Rectangles", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied to fill the rectangle towers. Empty = generated towers")
    coll_width_road_rectangles: PointerProperty(
        name="Width Road Rectangles", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied onto the rectangles whose road runs across the width. "
                    "The road runs along X and the hallway along Y. Stretched to the rectangle. Empty = generated")
    coll_length_road_rectangles: PointerProperty(
        name="Length Road Rectangles", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied onto the rectangles whose road runs along the length. "
                    "The road runs along Y and the hallway along X. Stretched to the rectangle. Empty = generated")
    coll_parks: PointerProperty(
        name="Parks", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied onto the roofs as parks. Empty = generated parks")
    coll_walls: PointerProperty(
        name="Walls", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied onto the exposed sides as walls. Empty = generated walls")
    coll_roof: PointerProperty(
        name="Roof", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied onto the roofs. Empty = generated roofs")
    coll_elevators: PointerProperty(
        name="Elevators", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied into the transport rectangles. Empty = generated shafts")
    trees_per_cell: IntProperty(name="Trees per park rectangle", default=5, min=0, max=30, update=_on_rect_change,
                                description="Random trees placed in each park rectangle")
    benches_per_cell: IntProperty(name="Benches per park rectangle", default=2, min=0, max=30, update=_on_rect_change,
                                  description="Random benches placed in each park rectangle")
    last_cell_count: IntProperty(name="Rectangle cells", default=0,
                                 description="Rectangle cells in the last build, counted at every level, excluding junctions and the park layer (set by Build)")
    last_footprint: IntProperty(name="Footprint rectangles", default=0,
                                description="Rectangles across the footprint in the last build (set by Build)")
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of rectangle cells that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per rectangle", default=20, min=0,
                                description="Residents in each residential rectangle cell")
    stat_cost: FloatProperty(name="Cost Per Rectangle ($M)", default=2.0, min=0.0, precision=2,
                             description="Build cost of one rectangle cell, in millions of dollars")
    stat_household: FloatProperty(name="Persons Per Household", default=2.6, min=0.1, max=20.0, precision=1,
                                  description="Average number of people in each household")
    stat_mortgage_rate: FloatProperty(name="Mortgage Rate", default=7.0, min=0.0, max=30.0, precision=2,
                                      subtype='PERCENTAGE',
                                      description="Annual mortgage interest rate. The loan is a 30-year fixed mortgage")


RECT_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Rectangles: no origin needed. The add-on uses each object's bounding box.",
    "Width Road Rectangles: put the origin at the centre of the rectangle. The road runs along X (the width) and "
    "the 12 ft hallway along Y. The object is stretched to the rectangle's width and length.",
    "Length Road Rectangles: put the origin at the centre of the rectangle. The road runs along Y (the length) and "
    "the 12 ft hallway along X. The object is stretched to the rectangle's width and length.",
    "Roof: no origin needed. The add-on uses each object's bounding box.",
    "Parks: put the origin at the centre of the rectangle it sits on.",
    "Elevators: put the origin at the centre of the transport rectangle. One copy holds all four shafts.",
    "Wall exteriors must be facing west, and wall lengths must be facing North/South.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


def _draw_rect(layout, entry):
    """The rectangle arcology: width and length, with its own values and collections."""
    q = entry.rect_settings
    unit = "ft" if q.size_in_feet else "m"
    box = layout.box()
    row = box.row()
    row.label(text="Footprint and roof")
    row.operator("arcology.info", text="", icon='INFO').text = FOOTPRINT_INFO
    box.prop(entry, "footprint")
    box.prop(q, "roof_plane")

    box = layout.box()
    row = box.row()
    row.label(text="Collections")
    row.operator("arcology.collection_info", text="", icon='INFO')
    box.prop(q, "coll_rectangles")
    box.prop(q, "coll_width_road_rectangles")
    box.prop(q, "coll_length_road_rectangles")
    box.prop(q, "coll_parks")
    box.prop(q, "coll_walls")
    box.prop(q, "coll_roof")
    box.prop(q, "coll_elevators")

    box = layout.box()
    row = box.row()
    row.label(text="Size")
    row.prop(q, "size_in_feet", text="ft" if q.size_in_feet else "m", toggle=True)
    box.prop(q, "rect_width_shown", text=f"Width ({unit})")
    box.prop(q, "rect_length_shown", text=f"Length ({unit})")
    box.prop(q, "level_shown", text=f"Level height ({unit})")
    box.prop(q, "roof_shown", text=f"Roof height ({unit})")

    box = layout.box()
    box.label(text="Height")
    box.prop(q, "max_layers")
    box.prop(q, "tower_min")
    box.prop(q, "tower_max")

    box = layout.box()
    box.label(text="Roads and seed")
    row = box.row(align=True)
    row.prop(q, "seed")
    row.operator("arcology.reseed", text="", icon='FILE_REFRESH')
    box.prop(q, "road_width_shown", text=f"Road width ({unit})")
    box.prop(q, "road_chance")
    box.prop(q, "max_cells_between_roads")
    box.prop(q, "min_cells_between_roads")

    box = layout.box()
    box.label(text="Show")
    grid = box.grid_flow(columns=2, align=True)
    grid.prop(q, "show_towers")
    grid.prop(q, "show_roads")
    grid.prop(q, "show_parks")
    grid.prop(q, "show_elevators")
    grid.prop(q, "show_walls")
    grid.prop(q, "show_trees")
    grid.prop(q, "show_benches")
    grid.prop(q, "show_columns")
    grid.prop(q, "show_paths")
    box.prop(q, "park_density")
    box.prop(q, "tree_scale")
    box.prop(q, "trees_per_cell")
    box.prop(q, "benches_per_cell")

    box = layout.box()
    box.label(text="Stats")
    cells = q.last_cell_count
    if cells:
        box.label(text=f"Cells, rectangles (every layer, excluding junctions and parks): {cells:,}")
    else:
        box.label(text="Cells, rectangles (every layer, excluding junctions and parks): build to count")
    box.prop(q, "stat_residential")
    box.prop(q, "stat_residents")
    box.prop(q, "stat_cost")
    population = cells * q.stat_residential * q.stat_residents
    cost_m = cells * q.stat_cost
    cost_text = f"${cost_m:,.1f} M" if cost_m < 1000 else f"${cost_m / 1000:,.2f} B"
    box.label(text=f"Estimated Population: {population:,.0f}")
    box.label(text=f"Estimated Cost: {cost_text}")
    if q.last_footprint:
        # Land area: each footprint rectangle, width times length
        land_m2 = q.last_footprint * q.rect_width * q.rect_length
        acres = land_m2 / 4046.8564224
        sq_miles = land_m2 / 2589988.11
        pop_per_sq_mile = population / sq_miles if sq_miles > 0 else 0.0
        box.label(text=f"Estimated Land Area: {acres:,.2f} acres")
        box.label(text=f"Population / square mile: {pop_per_sq_mile:,.0f}")
        box.label(text=f"Density Compared to Manhattan: {pop_per_sq_mile / MANHATTAN_DENSITY:,.2f}x")
    else:
        box.label(text="Estimated Land Area: build to calculate")
    box.prop(q, "stat_household")
    box.prop(q, "stat_mortgage_rate")
    households_per_cell = q.stat_residents / q.stat_household if q.stat_household > 0 else 0.0
    cost_per_household = (q.stat_cost * 1_000_000.0) / households_per_cell if households_per_cell > 0 else 0.0
    payment = monthly_mortgage_payment(cost_per_household, q.stat_mortgage_rate)
    box.label(text=f"Cost per household: ${cost_per_household:,.0f}")
    box.label(text=f"Estimated Mortgage Payment: ${payment:,.0f} / month")


PEN_ELEV_DIST_MAX_M = 1000.0


def _set_pen_edge_shown(props, value):
    """Store a typed or dragged tile edge in metres, clamped to 6 to 60 m."""
    metres = value * (FT if props.size_in_feet else 1.0)
    props.tile_edge = min(max(metres, PEN_MIN_EDGE_M), PEN_MAX_EDGE_M)


def _set_pen_elev_shown(props, value):
    """Store a typed or dragged elevator distance in metres, clamped to 0 to 1000 m."""
    metres = value * (FT if props.size_in_feet else 1.0)
    props.elevator_distance = min(max(metres, 0.0), PEN_ELEV_DIST_MAX_M)


def _on_pen_change(self, context):
    _auto_rebuild()


class PenroseProps(PropertyGroup):
    """Settings for the Penrose city. Kept apart from the other shapes' settings, so each arcology has its own values."""
    roof_plane: PointerProperty(
        name="Roof Plane", type=bpy.types.Object, poll=_is_mesh, update=_on_pen_change,
        description="Optional. Its height above each tile sets that tile's layer count, so it can be sculpted")
    seed: IntProperty(name="Seed", default=42, min=0, max=999999, update=_on_pen_change,
                      description="Changes the tower heights, the placement of the tiling, the elevators and the parks")
    tile_edge: FloatProperty(name="Tile edge", default=60.0 * FT, min=PEN_MIN_EDGE_M, max=PEN_MAX_EDGE_M,
                             update=_on_pen_change, description="Length of one edge of a Penrose tile, in metres")
    elevator_distance: FloatProperty(
        name="Elevator Distance", default=150.0 * FT, min=0.0, max=PEN_ELEV_DIST_MAX_M, update=_on_pen_change,
        description="Least distance, in metres, between two thin tiles that both have elevator shafts, measured "
                    "centre to centre. 0 puts elevators in every thin tile")
    size_in_feet: BoolProperty(name="Feet", default=False,
                               description="Show the tile edge, distances and heights in feet instead of metres")
    tile_edge_shown: FloatProperty(
        name="Tile edge", precision=2, min=1.0, max=200.0, update=_on_pen_change,
        get=lambda self: self.tile_edge / (FT if self.size_in_feet else 1.0),
        set=lambda self, value: _set_pen_edge_shown(self, value),
        description="Length of one tile edge. Shown in the unit picked by the button beside Size")
    elevator_distance_shown: FloatProperty(
        name="Elevator Distance", precision=2, min=0.0, max=3300.0, update=_on_pen_change,
        get=lambda self: self.elevator_distance / (FT if self.size_in_feet else 1.0),
        set=lambda self, value: _set_pen_elev_shown(self, value),
        description="Least centre-to-centre distance between thin tiles with elevators. Shown in the unit picked "
                    "by the button beside Size")
    road_width_ft: FloatProperty(name="Road width (ft)", default=12.0, min=6.0, max=60.0, update=_on_pen_change,
                                 description="Width of each road, along the long diagonal of a thin tile")
    road_width_shown: FloatProperty(
        name="Road width", precision=2, min=1.8, max=18.0, update=_on_pen_change,
        get=lambda self: self.road_width_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "road_width_ft", value, 6.0, 60.0),
        description="Width of each road. Shown in the unit picked by the button beside Size")
    level_ft: FloatProperty(name="Level height (ft)", default=30.0, min=8.0, max=60.0, update=_on_pen_change)
    roof_ft: FloatProperty(name="Roof height (ft)", default=6.0, min=0.5, max=20.0, update=_on_pen_change)
    level_shown: FloatProperty(
        name="Level height", precision=2, min=2.0, max=60.0, update=_on_pen_change,
        get=lambda self: self.level_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "level_ft", value, 8.0, 60.0),
        description="Height of one level. Shown in the unit picked by the button beside Size")
    roof_shown: FloatProperty(
        name="Roof height", precision=2, min=0.15, max=20.0, update=_on_pen_change,
        get=lambda self: self.roof_ft * (1.0 if self.size_in_feet else FT),
        set=lambda self, value: _set_height_shown(self, "roof_ft", value, 0.5, 20.0),
        description="Height of the roof slab. Shown in the unit picked by the button beside Size")
    max_layers: IntProperty(name="Max layers", default=12, min=1, max=24, update=_on_pen_change,
                            description="The most layers any tile can have")
    tower_min: IntProperty(name="Tower min layers", default=10, min=1, max=24, update=_on_pen_change)
    tower_max: IntProperty(name="Tower max layers", default=12, min=1, max=24, update=_on_pen_change)
    tree_scale: FloatProperty(
        name="Tree Scale", default=1.0, min=0.0, max=3.0, update=_on_pen_change,
        description="Scales every tree, its canopy and its trunk. 0 hides the trees, 3 is three times the size")
    park_density: FloatProperty(name="Park density", default=0.8, min=0.0, max=1.0, subtype='FACTOR',
                                update=_on_pen_change, description="Chance that a roof site gets a park")
    trees_per_cell: IntProperty(name="Trees per park tile", default=5, min=0, max=30, update=_on_pen_change,
                                description="Random trees placed in each park tile")
    benches_per_cell: IntProperty(name="Benches per park tile", default=2, min=0, max=30, update=_on_pen_change,
                                  description="Random benches placed in each park tile")
    show_towers: BoolProperty(name="Towers", default=True, update=_on_pen_change)
    show_roads: BoolProperty(name="Roads", default=True, update=_on_pen_change)
    show_parks: BoolProperty(name="Parks", default=True, update=_on_pen_change)
    show_walls: BoolProperty(name="Walls", default=True, update=_on_pen_change)
    show_elevators: BoolProperty(name="Elevators", default=True, update=_on_pen_change)
    show_trees: BoolProperty(name="Trees", default=True, update=_on_pen_change)
    show_benches: BoolProperty(name="Benches", default=True, update=_on_pen_change)
    show_columns: BoolProperty(name="Columns", default=True, update=_on_pen_change,
                               description="3 ft pentagonal columns at every tile corner, one per level")
    show_paths: BoolProperty(name="Paths", default=True, update=_on_pen_change,
                             description="4 ft roof paths on the parks, joined to neighbouring parks")
    coll_thick_rhombs: PointerProperty(
        name="Thick Rhombs", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied into the thick tiles as towers. Empty = generated towers")
    coll_thin_rhombs: PointerProperty(
        name="Thin Rhombs", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the thin tiles as road surfaces. Empty = generated roads")
    coll_thick_roofs: PointerProperty(
        name="Thick Roofs", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the roofs of the thick tiles. Empty = generated roofs")
    coll_thin_roofs: PointerProperty(
        name="Thin Roofs", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the roofs of the thin tiles. Empty = generated roofs")
    coll_thick_parks: PointerProperty(
        name="Thick Parks", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the thick tiles as parks. Empty = generated parks")
    coll_thin_parks: PointerProperty(
        name="Thin Parks", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the thin tiles as parks. Empty = generated parks")
    coll_walls: PointerProperty(
        name="Walls", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the exposed edges as walls. Empty = generated walls")
    coll_elevators: PointerProperty(
        name="Elevators", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied into the elevator shafts. Empty = generated shafts")
    last_cell_count: IntProperty(name="Tile cells", default=0,
                                 description="Thick tile cells in the last build, counted at every level (set by Build)")
    last_footprint: IntProperty(name="Footprint tiles", default=0,
                                description="Tiles across the footprint in the last build (set by Build)")
    last_thick: IntProperty(name="Thick tiles", default=0,
                            description="Thick tiles in the last build (set by Build)")
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of thick tile cells that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per tile", default=20, min=0,
                                description="Residents in each residential tile cell")
    stat_cost: FloatProperty(name="Cost Per Tile ($M)", default=2.0, min=0.0, precision=2,
                             description="Build cost of one tile cell, in millions of dollars")
    stat_household: FloatProperty(name="Persons Per Household", default=2.6, min=0.1, max=20.0, precision=1,
                                  description="Average number of people in each household")
    stat_mortgage_rate: FloatProperty(name="Mortgage Rate", default=7.0, min=0.0, max=30.0, precision=2,
                                      subtype='PERCENTAGE',
                                      description="Annual mortgage interest rate. The loan is a 30-year fixed mortgage")


PENROSE_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Thick Rhombs (towers): no origin needed. Each copy is turned to the tile's edge, scaled to fill it and stretched to one level tall.",
    "Thin Rhombs (roads): no origin needed. Each copy is fitted to the road along the long diagonal, and stretched to the road height.",
    "Thick Roofs and Thin Roofs: no origin needed. Each copy is turned to the tile's edge, scaled to fill it and stretched to the roof height.",
    "Thick Parks and Thin Parks: no origin needed. Each copy is turned to the tile's edge, scaled to fill it, and sits on the top level.",
    "Walls: no origin needed. Each copy is turned out of the tile, its exterior facing west, and stretched to the edge length.",
    "Elevators: no origin needed. Each copy is fitted to the 14 ft corner shaft and stretched to 36 ft per level.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


def _draw_penrose(layout, entry):
    """The Penrose arcology: tile edge and elevator distance, with its own values and collections."""
    q = entry.penrose_settings
    unit = "ft" if q.size_in_feet else "m"
    box = layout.box()
    row = box.row()
    row.label(text="Footprint and roof")
    row.operator("arcology.info", text="", icon='INFO').text = FOOTPRINT_INFO
    box.prop(entry, "footprint")
    box.prop(q, "roof_plane")

    box = layout.box()
    row = box.row()
    row.label(text="Collections")
    row.operator("arcology.collection_info", text="", icon='INFO')
    box.prop(q, "coll_thick_rhombs")
    box.prop(q, "coll_thin_rhombs")
    box.prop(q, "coll_thick_roofs")
    box.prop(q, "coll_thin_roofs")
    box.prop(q, "coll_thick_parks")
    box.prop(q, "coll_thin_parks")
    box.prop(q, "coll_walls")
    box.prop(q, "coll_elevators")

    box = layout.box()
    row = box.row()
    row.label(text="Size")
    row.prop(q, "size_in_feet", text="ft" if q.size_in_feet else "m", toggle=True)
    box.prop(q, "tile_edge_shown", text=f"Tile edge ({unit})")
    box.prop(q, "elevator_distance_shown", text=f"Elevator Distance ({unit})")
    box.prop(q, "level_shown", text=f"Level height ({unit})")
    box.prop(q, "roof_shown", text=f"Roof height ({unit})")
    box.prop(q, "road_width_shown", text=f"Road width ({unit})")

    box = layout.box()
    box.label(text="Height")
    box.prop(q, "max_layers")
    box.prop(q, "tower_min")
    box.prop(q, "tower_max")

    box = layout.box()
    box.label(text="Seed")
    row = box.row(align=True)
    row.prop(q, "seed")
    row.operator("arcology.reseed", text="", icon='FILE_REFRESH')

    box = layout.box()
    box.label(text="Show")
    grid = box.grid_flow(columns=2, align=True)
    grid.prop(q, "show_towers")
    grid.prop(q, "show_roads")
    grid.prop(q, "show_parks")
    grid.prop(q, "show_walls")
    grid.prop(q, "show_elevators")
    grid.prop(q, "show_trees")
    grid.prop(q, "show_benches")
    grid.prop(q, "show_columns")
    grid.prop(q, "show_paths")
    box.prop(q, "park_density")
    box.prop(q, "tree_scale")
    box.prop(q, "trees_per_cell")
    box.prop(q, "benches_per_cell")

    box = layout.box()
    box.label(text="Stats")
    cells = q.last_cell_count
    if cells:
        box.label(text=f"Cells, thick tiles (every layer, excluding parks): {cells:,}")
    else:
        box.label(text="Cells, thick tiles (every layer, excluding parks): build to count")
    box.prop(q, "stat_residential")
    box.prop(q, "stat_residents")
    box.prop(q, "stat_cost")
    population = cells * q.stat_residential * q.stat_residents
    cost_m = cells * q.stat_cost
    cost_text = f"${cost_m:,.1f} M" if cost_m < 1000 else f"${cost_m / 1000:,.2f} B"
    box.label(text=f"Estimated Population: {population:,.0f}")
    box.label(text=f"Estimated Cost: {cost_text}")
    if q.last_footprint:
        # Land area: each thick tile is sin 72 of an edge squared, each thin tile sin 36
        thin = q.last_footprint - q.last_thick
        land_m2 = (q.last_thick * math.sin(math.radians(72.0)) + thin * math.sin(math.radians(36.0))) * q.tile_edge ** 2
        acres = land_m2 / 4046.8564224
        sq_miles = land_m2 / 2589988.11
        pop_per_sq_mile = population / sq_miles if sq_miles > 0 else 0.0
        box.label(text=f"Estimated Land Area: {acres:,.2f} acres")
        box.label(text=f"Population / square mile: {pop_per_sq_mile:,.0f}")
        box.label(text=f"Density Compared to Manhattan: {pop_per_sq_mile / MANHATTAN_DENSITY:,.2f}x")
    else:
        box.label(text="Estimated Land Area: build to calculate")
    box.prop(q, "stat_household")
    box.prop(q, "stat_mortgage_rate")
    households_per_cell = q.stat_residents / q.stat_household if q.stat_household > 0 else 0.0
    cost_per_household = (q.stat_cost * 1_000_000.0) / households_per_cell if households_per_cell > 0 else 0.0
    payment = monthly_mortgage_payment(cost_per_household, q.stat_mortgage_rate)
    box.label(text=f"Cost per household: ${cost_per_household:,.0f}")
    box.label(text=f"Estimated Mortgage Payment: ${payment:,.0f} / month")


class ArcologyShared(PropertyGroup):
    """Settings every arcology shares: the Live switch and the status line."""
    live: BoolProperty(name="Live update", default=True,
                       description="Rebuild a moment after every change")
    status: StringProperty(name="Status", default="Not built yet")


SHAPE_LABEL = {SHAPE_HEX: "Hexagon", SHAPE_OCT: "Octagon", SHAPE_RECT: "Rectangle", SHAPE_PEN: "Penrose"}


class ArcologyEntry(PropertyGroup):
    """One arcology: the footprint mesh it is linked to, its shape, and the settings of that shape."""
    footprint: PointerProperty(
        name="Footprint", type=bpy.types.Object, poll=_is_mesh, update=_on_change,
        description="The mesh this arcology is built on")
    shape: EnumProperty(
        name="Shape",
        items=(
            (SHAPE_HEX, "Hexagon", "A hexagon city on this footprint"),
            (SHAPE_OCT, "Octagon", "An octagon city, with square gaps, on this footprint"),
            (SHAPE_RECT, "Rectangle", "A city of touching rectangles, with a 12 ft hallway down each road rectangle"),
            (SHAPE_PEN, "Penrose", "A city of Penrose tiles: thick tiles are towers, thin tiles are roads"),
        ),
        default=SHAPE_HEX, update=_on_change,
        description="The city this footprint gets")
    uid: IntProperty(default=0, min=0,
                     description="Identifies this arcology's output, so it stays put when the list changes")
    hex_settings: PointerProperty(type=ArcologyProps)
    oct_settings: PointerProperty(type=OctagonProps)
    rect_settings: PointerProperty(type=RectProps)
    penrose_settings: PointerProperty(type=PenroseProps)


class ARCOLOGY_UL_list(bpy.types.UIList):
    """One row per arcology: its number, its footprint and its shape."""
    bl_idname = "ARCOLOGY_UL_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        fp = item.footprint
        row = layout.row()
        row.label(text=f"{index + 1}. {fp.name if fp is not None else 'No footprint'}", icon='MESH_DATA')
        row.label(text=SHAPE_LABEL.get(item.shape, item.shape))


class ARCOLOGY_OT_rebuild(Operator):
    bl_idname = "arcology.rebuild"
    bl_label = "Build"
    bl_description = "Rebuild the selected arcology. It takes the shape on the tabs"

    def execute(self, context):
        scene = context.scene
        entry = active_entry(scene)
        if entry is not None and entry.shape != scene.arcology_shape:
            entry.shape = scene.arcology_shape     # only Build changes an arcology's shape
        try:
            rebuild(scene, entry)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class ARCOLOGY_OT_clear(Operator):
    bl_idname = "arcology.clear"
    bl_label = "Clear"
    bl_description = "Remove the selected arcology's city. The others are left as they are"

    def execute(self, context):
        entry = active_entry(context.scene)
        if entry is not None:
            clear_arcology(entry)
        context.scene.arcology_shared.status = "Cleared" if entry is not None else "Add an arcology with +"
        return {'FINISHED'}


class ARCOLOGY_OT_reseed(Operator):
    bl_idname = "arcology.reseed"
    bl_label = "New seed"
    bl_description = "Pick a random seed for the selected arcology"

    def execute(self, context):
        entry = active_entry(context.scene)
        if entry is None:
            return {'CANCELLED'}
        props = {SHAPE_PEN: entry.penrose_settings, SHAPE_OCT: entry.oct_settings,
                 SHAPE_RECT: entry.rect_settings}.get(entry.shape, entry.hex_settings)
        props.seed = random.randint(0, 999999)
        return {'FINISHED'}


class ARCOLOGY_OT_add(Operator):
    bl_idname = "arcology.add"
    bl_label = "Add arcology"
    bl_description = "Add an arcology. It starts on the active mesh, or on 'Ark Polygon' or '30 Acre Ark'"

    def execute(self, context):
        scene = context.scene
        entry = scene.arcologies.add()
        ensure_uids(scene)
        entry.footprint = default_footprint(context)
        scene.arcology_index = len(scene.arcologies) - 1
        _auto_rebuild()
        return {'FINISHED'}


class ARCOLOGY_OT_remove(Operator):
    bl_idname = "arcology.remove"
    bl_label = "Remove arcology"
    bl_description = "Remove the selected arcology and its city. The others are left as they are"

    def execute(self, context):
        scene = context.scene
        if not scene.arcologies:
            return {'CANCELLED'}
        index = min(max(scene.arcology_index, 0), len(scene.arcologies) - 1)
        clear_arcology(scene.arcologies[index])
        scene.arcologies.remove(index)
        scene.arcology_index = max(0, min(index, len(scene.arcologies) - 1))
        scene.arcology_shared.status = (f"Removed arcology {index + 1}" if scene.arcologies
                                        else "Add an arcology with +")
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

OCT_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Octagons: no origin needed. The add-on uses each object's bounding box.",
    "Road Octagons: put the origin at the centre of the octagon cell. The road runs east-west along the object's X axis, and is turned 90 degrees for a north-south road.",
    "Squares: no origin needed. Each copy fills one level of a square tower.",
    "Octagon Roofs: no origin needed. The add-on uses each object's bounding box.",
    "Square Roofs: no origin needed. The add-on uses each object's bounding box.",
    "Parks: put the origin at the centre of the octagon it sits on.",
    "Elevators: put the origin at the centre of the transport octagon. One copy holds all eight shafts.",
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
    "Footprint: the mesh this arcology is built on. Each arcology in the list has its own footprint, shape "
    "and settings, so you can build an octagon and a hexagon side by side, or several hexagons with "
    "different settings. Press + to add one and - to remove the selected one.\n"
    "Shape: Hexagon or Octagon for this arcology.\n"
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
        entry = active_entry(context.scene)
        is_oct = entry is not None and entry.shape == SHAPE_OCT
        if entry is not None and entry.shape == SHAPE_PEN:
            lines = PENROSE_COLLECTION_INFO
        elif entry is not None and entry.shape == SHAPE_RECT:
            lines = RECT_COLLECTION_INFO
        else:
            lines = OCT_COLLECTION_INFO if is_oct else COLLECTION_INFO
        for line in lines:
            col.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


def _draw_oct(layout, entry):
    """The octagon arcology. Same layout as the hexagon one, with its own values."""
    q = entry.oct_settings
    unit = "ft" if q.size_in_feet else "m"
    box = layout.box()
    row = box.row()
    row.label(text="Footprint and roof")
    row.operator("arcology.info", text="", icon='INFO').text = FOOTPRINT_INFO
    box.prop(entry, "footprint")
    box.prop(q, "roof_plane")

    box = layout.box()
    row = box.row()
    row.label(text="Collections")
    row.operator("arcology.collection_info", text="", icon='INFO')
    box.prop(q, "coll_octagons")
    box.prop(q, "coll_road_octagons")
    box.prop(q, "coll_squares")
    box.prop(q, "coll_square_roofs")
    box.prop(q, "coll_parks")
    box.prop(q, "coll_walls")
    box.prop(q, "coll_roof")
    box.prop(q, "coll_elevators")

    box = layout.box()
    row = box.row()
    row.label(text="Size")
    row.prop(q, "size_in_feet", text="ft" if q.size_in_feet else "m", toggle=True)
    box.prop(q, "oct_edge_shown", text=f"Octagon edge length ({unit})")
    box.prop(q, "level_shown", text=f"Level height ({unit})")
    box.prop(q, "roof_shown", text=f"Roof height ({unit})")

    box = layout.box()
    box.label(text="Height")
    box.prop(q, "max_layers")
    box.prop(q, "tower_min")
    box.prop(q, "tower_max")

    box = layout.box()
    box.label(text="Roads and seed")
    row = box.row(align=True)
    row.prop(q, "seed")
    row.operator("arcology.reseed", text="", icon='FILE_REFRESH')
    box.prop(q, "road_width_shown", text=f"Road width ({unit})")
    box.prop(q, "road_chance")
    box.prop(q, "max_cells_between_roads")
    box.prop(q, "min_cells_between_roads")

    box = layout.box()
    box.label(text="Show")
    grid = box.grid_flow(columns=2, align=True)
    grid.prop(q, "show_towers")
    grid.prop(q, "show_roads")
    grid.prop(q, "show_squares")
    grid.prop(q, "show_parks")
    grid.prop(q, "show_elevators")
    grid.prop(q, "show_walls")
    grid.prop(q, "show_trees")
    grid.prop(q, "show_benches")
    grid.prop(q, "show_columns")
    grid.prop(q, "show_paths")
    box.prop(q, "park_density")
    box.prop(q, "tree_scale")
    box.prop(q, "trees_per_cell")
    box.prop(q, "benches_per_cell")

    box = layout.box()
    box.label(text="Stats")
    cells = q.last_cell_count
    if cells:
        box.label(text=f"Cells, octagons and squares (every layer, excluding junctions and parks): {cells:,}")
    else:
        box.label(text="Cells, octagons and squares (every layer, excluding junctions and parks): build to count")
    box.prop(q, "stat_residential")
    box.prop(q, "stat_residents")
    box.prop(q, "stat_cost")
    population = cells * q.stat_residential * q.stat_residents
    cost_m = cells * q.stat_cost
    cost_text = f"${cost_m:,.1f} M" if cost_m < 1000 else f"${cost_m / 1000:,.2f} B"
    box.label(text=f"Estimated Population: {population:,.0f}")
    box.label(text=f"Estimated Cost: {cost_text}")
    if q.last_footprint:
        # Land area: each footprint octagon with its share of the gap squares, a cell of side D = edge (1 + sqrt 2)
        D = q.oct_edge * (1.0 + SQRT2)
        land_m2 = q.last_footprint * D * D
        acres = land_m2 / 4046.8564224
        sq_miles = land_m2 / 2589988.11
        pop_per_sq_mile = population / sq_miles if sq_miles > 0 else 0.0
        box.label(text=f"Estimated Land Area: {acres:,.2f} acres")
        box.label(text=f"Population / square mile: {pop_per_sq_mile:,.0f}")
        box.label(text=f"Density Compared to Manhattan: {pop_per_sq_mile / MANHATTAN_DENSITY:,.2f}x")
    else:
        box.label(text="Estimated Land Area: build to calculate")
    box.prop(q, "stat_household")
    box.prop(q, "stat_mortgage_rate")
    households_per_cell = q.stat_residents / q.stat_household if q.stat_household > 0 else 0.0
    cost_per_household = (q.stat_cost * 1_000_000.0) / households_per_cell if households_per_cell > 0 else 0.0
    payment = monthly_mortgage_payment(cost_per_household, q.stat_mortgage_rate)
    box.label(text=f"Cost per household: ${cost_per_household:,.0f}")
    box.label(text=f"Estimated Mortgage Payment: ${payment:,.0f} / month")


def _draw_hex(layout, entry):
    """The hexagon arcology: the settings it has always had, with its own values."""
    p = entry.hex_settings
    box = layout.box()
    row = box.row()
    row.label(text="Footprint and roof")
    row.operator("arcology.info", text="", icon='INFO').text = FOOTPRINT_INFO
    box.prop(entry, "footprint")
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
    box.prop(p, "road_width_shown", text=f"Road width ({unit})")
    box.prop(p, "road_chance")
    box.prop(p, "max_cells_between_roads")
    box.prop(p, "min_cells_between_roads")

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
    box.prop(p, "tree_scale")
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


class ARCOLOGY_PT_panel(Panel):
    bl_label = "Arcology"
    bl_idname = "ARCOLOGY_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Arcology"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        shared = scene.arcology_shared

        row = layout.row(align=True)
        row.operator("arcology.rebuild", text="Build", icon='FILE_REFRESH')
        row.operator("arcology.clear", text="", icon='X')
        layout.prop(shared, "live")
        layout.label(text=shared.status)

        layout.prop(scene, "arcology_shape", expand=True)
        row = layout.row()
        row.template_list("ARCOLOGY_UL_list", "", scene, "arcologies", scene, "arcology_index", rows=3)
        col = row.column(align=True)
        col.operator("arcology.add", text="", icon='ADD')
        col.operator("arcology.remove", text="", icon='REMOVE')

        entry = active_entry(scene)
        if entry is None:
            layout.label(text="Press + to add an arcology on a footprint.")
            return
        if entry.shape == SHAPE_HEX:
            _draw_hex(layout, entry)
        elif entry.shape == SHAPE_OCT:
            _draw_oct(layout, entry)
        elif entry.shape == SHAPE_PEN:
            _draw_penrose(layout, entry)
        else:
            _draw_rect(layout, entry)


def _arcology_sidebar(context):
    """Return (area, region) for the first 3D Viewport that has a sidebar."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type == 'UI':
                    return area, region
    return None, None


def _arcology_sidebar_showing(context):
    area, region = _arcology_sidebar(context)
    if area is None:
        return False
    return bool(area.spaces.active.show_region_ui) and region.active_panel_category == "Arcology"


class ARCOLOGY_OT_open_sidebar(Operator):
    """Open the Arcology tab in the 3D Viewport sidebar, or close the sidebar if it is already showing it"""
    bl_idname = "arcology.open_sidebar"
    bl_label = "Open Arcology Generator"
    bl_description = "Open the Arcology tab in the 3D Viewport sidebar (N panel), or close it if it is already open"

    def execute(self, context):
        area, region = _arcology_sidebar(context)
        if area is None:
            self.report({'WARNING'}, "No 3D Viewport is open")
            return {'CANCELLED'}
        space = area.spaces.active
        if _arcology_sidebar_showing(context):
            space.show_region_ui = False
        else:
            space.show_region_ui = True
            region.active_panel_category = "Arcology"
        area.tag_redraw()
        return {'FINISHED'}


# The hexagon icon, embedded so it loads even if icon.png is not next to this file.
_ICON_PNG_B64 = (
    "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"
    "fCEXP4iWAAAAAElFTkSuQmCC"
)

_icon_coll = None
_ORIG_HEADER_DRAW = None


def _load_arcology_icon():
    """Load the embedded hexagon PNG as a custom icon."""
    global _icon_coll
    import base64
    import os
    import tempfile
    import bpy.utils.previews
    if _icon_coll is None:
        _icon_coll = bpy.utils.previews.new()
    if "hex" in _icon_coll:
        return
    try:
        folder = bpy.app.tempdir or tempfile.gettempdir()
        path = os.path.join(folder, "arcology_hex_icon.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(_ICON_PNG_B64))
        _icon_coll.load("hex", path, 'IMAGE')
    except Exception as exc:
        print("Arcology: could not load the hexagon icon:", exc)


def _release_arcology_icon():
    global _icon_coll
    import bpy.utils.previews
    if _icon_coll is not None:
        bpy.utils.previews.remove(_icon_coll)
        _icon_coll = None


def _draw_arcology_header_button(layout):
    """The Arcology button, drawn just left of the Properties search field."""
    if _icon_coll is not None and "hex" in _icon_coll:
        layout.operator("arcology.open_sidebar", text="",
                        icon_value=_icon_coll["hex"].icon_id)
    else:
        layout.operator("arcology.open_sidebar", text="", icon='VIEW3D')


class _ArcologyHeaderLayout:
    """Wraps the header layout and inserts the button right before the search field."""

    def __init__(self, layout):
        self._layout = layout
        self._done = False

    def __getattr__(self, name):
        return getattr(self._layout, name)

    def prop(self, data, property, *args, **kwargs):
        if not self._done and property == "search_filter":
            self._done = True
            _draw_arcology_header_button(self._layout)
        return self._layout.prop(data, property, *args, **kwargs)


class _ArcologyHeaderSelf:
    def __init__(self, owner, layout):
        self._owner = owner
        self.layout = layout

    def __getattr__(self, name):
        return getattr(self._owner, name)


def _arcology_header_draw(self, context):
    wrapped = _ArcologyHeaderSelf(self, _ArcologyHeaderLayout(self.layout))
    _ORIG_HEADER_DRAW(wrapped, context)


def _install_arcology_header():
    global _ORIG_HEADER_DRAW
    if _ORIG_HEADER_DRAW is None:
        _ORIG_HEADER_DRAW = bpy.types.PROPERTIES_HT_header.draw
        bpy.types.PROPERTIES_HT_header.draw = _arcology_header_draw


def _uninstall_arcology_header():
    global _ORIG_HEADER_DRAW
    if _ORIG_HEADER_DRAW is not None:
        bpy.types.PROPERTIES_HT_header.draw = _ORIG_HEADER_DRAW
        _ORIG_HEADER_DRAW = None


CLASSES = (
    ArcologyProps,
    OctagonProps,
    RectProps,
    PenroseProps,
    ArcologyShared,
    ArcologyEntry,
    ARCOLOGY_UL_list,
    ARCOLOGY_OT_rebuild,
    ARCOLOGY_OT_clear,
    ARCOLOGY_OT_reseed,
    ARCOLOGY_OT_add,
    ARCOLOGY_OT_remove,
    ARCOLOGY_OT_collection_info,
    ARCOLOGY_OT_info,
    ARCOLOGY_PT_panel,
    ARCOLOGY_OT_open_sidebar,
    ARCOLOGY_Preferences,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _load_arcology_icon()
    _install_arcology_header()
    bpy.types.Scene.arcology_shared = PointerProperty(type=ArcologyShared)
    bpy.types.Scene.arcologies = bpy.props.CollectionProperty(type=ArcologyEntry)
    bpy.types.Scene.arcology_index = IntProperty(
        name="Arcology", default=0, min=0,
        description="The arcology the panel is showing")
    bpy.types.Scene.arcology_shape = EnumProperty(
        name="Shape",
        items=(
            (SHAPE_HEX, "Hexagon", "The selected arcology is a hexagon city"),
            (SHAPE_OCT, "Octagon", "The selected arcology is an octagon city, with square gaps"),
            (SHAPE_RECT, "Rectangle", "A city of touching rectangles, with a 12 ft hallway down each road rectangle"),
            (SHAPE_PEN, "Penrose", "A city of Penrose tiles: thick tiles are towers, thin tiles are roads"),
        ),
        default=SHAPE_HEX,
        description="The shape Build gives the selected arcology")


def unregister():
    global _pending
    _uninstall_arcology_header()
    _release_arcology_icon()
    try:
        bpy.app.timers.unregister(_timer_fire)
    except Exception:
        pass
    _pending = False
    del bpy.types.Scene.arcology_shape
    del bpy.types.Scene.arcology_index
    del bpy.types.Scene.arcologies
    del bpy.types.Scene.arcology_shared
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
