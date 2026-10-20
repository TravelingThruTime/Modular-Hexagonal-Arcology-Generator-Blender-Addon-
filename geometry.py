"""Shared geometry: lattice maths, prisms, paths, trees, benches and park helpers."""


from types import SimpleNamespace
import math

import bpy

from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .constants import BENCH_BACK_H, BENCH_BACK_T, BENCH_DEPTH_FRAC, BENCH_LEN_FRAC, BENCH_SEAT_H, FAMILIES, FOOTPRINT_NAMES, FT, HEX_ANGLES, PATH_BENDS, PATH_WIDTH_FT, PROXY_TAG, TILE_TOP, TRAP_TOL_FRAC, TRAP_UV, TREE_RADIUS_FRAC, TRI_CELLS, TRUNK_H_FRAC, TRUNK_R_FRAC, WALL_THICK_FRAC


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
