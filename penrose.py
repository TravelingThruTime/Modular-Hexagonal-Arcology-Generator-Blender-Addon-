"""Penrose tiling arcology builder."""


from types import SimpleNamespace
import itertools
import math
import random

import bmesh
import numpy as np

from mathutils import Matrix, Vector

from .constants import ELEVATOR_H_FT, FT, PARK_SLAB_H, PATH_LIFT_FT, PATH_WIDTH_FT, ROAD_BASE, ROAD_H, TILE_BOT, TILE_TOP, TREE_RADIUS_FRAC, WALL_THICK_FRAC
from .geometry import add_bench, add_prism, add_tree, layers_under, mesh_height_tester, rot2
from .records import elevator_record, source_pool, stretch_record, wall_record


PEN_E = np.array([[math.cos(math.radians(72 * k)), math.sin(math.radians(72 * k))] for k in range(5)])


PEN_F = np.array([[math.cos(math.radians(144 * k)), math.sin(math.radians(144 * k)), 1.0] for k in range(5)])


PEN_SHIFT = np.array([0.0137, 0.0291, 0.0053])


PEN_MIN_EDGE_M = 6.0


PEN_MAX_EDGE_M = 60.0


# The Penrose elevator, copied from the master: feet, origin at the corner vertex of the thin tile, x along the road,
# y out from the corner toward the road. Its flat top lies on the road edge. Copies keep this size exactly.
ELEVATOR_OUTLINE_FT = [(0.0, 0.0), (10.85, 3.54), (10.78, 12.52), (-10.47, 12.52), (-10.47, 4.15)]


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


def piece_record(pool, rng, cen, theta, rel, z, height, layer, group, cat):
    """A copy of one of your objects turned to theta exactly, scaled evenly to fill the piece, its bottom centre on the
    piece's centre and stretched to height. Used for the thin half rhombs, which have one way round: base on the road,
    wide corner away from it."""
    src = pool[rng.randrange(len(pool))]
    s = fit_to_convex(src, theta, rel)
    kz = height / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((cen.x, cen.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s, s, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src, "height": height,
            "matrix": P_ @ Sc @ T @ src["xform"]}


_POSE_CACHE = {}


def rhomb_pose(src, rel):
    """The turn and even scale that fit a source into a tile. The source's own edges are laid along the tile's edges,
    both ways round, and the best fit is kept. Nothing is random, so every copy on a tile sits the same way."""
    key = (id(src), tuple((round(q.x, 4), round(q.y, 4)) for q in rel))
    hit = _POSE_CACHE.get(key)
    if hit is not None:
        return hit
    hull = _pen_ccw(src["hull"])
    m, k = len(hull), len(rel)
    best = (0.0, fit_to_convex(src, 0.0, rel))
    for i in range(m if m >= 2 else 0):
        a, b = hull[i], hull[(i + 1) % m]
        psi = math.atan2(b.y - a.y, b.x - a.x)
        for j in range(k):
            c, d = rel[j], rel[(j + 1) % k]
            phi = math.atan2(d.y - c.y, d.x - c.x)
            for flip in (0.0, math.pi):
                th = phi - psi + flip
                s = fit_to_convex(src, th, rel)
                if s > best[1] + 1e-9:
                    best = (th, s)
    if len(_POSE_CACHE) > 4096:
        _POSE_CACHE.clear()
    _POSE_CACHE[key] = best
    return best


def rhomb_record(pool, rng, cen, rel, z, height, layer, group, cat):
    """A copy of one of your objects, turned and scaled to fit its tile (see rhomb_pose), and stretched to the given
    height. height None keeps the object's own height."""
    src = pool[rng.randrange(len(pool))]
    theta, s = rhomb_pose(src, rel)
    h = src["height"] if height is None else height
    kz = h / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((cen.x, cen.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s, s, kz, 1.0)))
    T = Matrix.Translation(-src["bottom_center"])
    return {"group": group, "cat": cat, "layer": layer, "src": src, "height": h,
            "matrix": P_ @ Sc @ T @ src["xform"]}


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


def _pen_clip(pts, cen, n, off):
    """The part of the convex polygon pts at or beyond the line set off from cen along the unit vector n."""
    out = []
    m = len(pts)
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % m]
        da = (a - cen).dot(n) - off
        db = (b - cen).dot(n) - off
        if da >= 0.0:
            out.append(a)
        if (da >= 0.0) != (db >= 0.0):
            out.append(a + (b - a) * (da / (da - db)))
    clean = []
    for q in out:
        if not clean or (q - clean[-1]).length > 1e-6:
            clean.append(q)
    if len(clean) > 1 and (clean[0] - clean[-1]).length <= 1e-6:
        clean.pop()
    return clean


def _pen_halves(t, width):
    """The two pieces of a thin tile, one on each side of its road: the part beyond each road edge. Returns
    (centre, points relative to centre, side) triples, counter-clockwise. The centre is the middle of the piece's
    bounding box in the road's own frame, which is where a copy's bounding box is centred."""
    out = []
    u = t["road_u"]
    across = Vector((-u.y, u.x))
    cen = t["cen"]
    for side in (1.0, -1.0):
        n = across * side
        piece = _pen_clip(t["pts"], cen, n, width / 2.0)
        if len(piece) < 3:
            continue
        piece = _pen_ccw(piece)
        xs = [(q - cen).dot(u) for q in piece]
        ys = [(q - cen).dot(n) for q in piece]
        c = cen + u * ((max(xs) + min(xs)) / 2.0) + n * ((max(ys) + min(ys)) / 2.0)
        out.append((c, [q - c for q in piece], side))
    return out


def _elevator_at(t, ci, across):
    """The elevator at wide corner ci of a thin tile: its origin on the corner, its road side turned toward the road.
    Returns (origin, turn, outline corners in the scene)."""
    v = t["pts"][ci]
    side = 1.0 if (v - t["cen"]).dot(across) > 0 else -1.0
    n = -across * side                                  # from the corner toward the road
    turn = math.atan2(-n.x, n.y)                        # the master's +y (road side) runs along n
    c, s = math.cos(turn), math.sin(turn)
    pts = [v + Vector(((x * c - y * s) * FT, (x * s + y * c) * FT)) for x, y in ELEVATOR_OUTLINE_FT]
    return v, turn, pts


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
    t["road_u"] = u
    t["road_len"] = (b - a).length


def build_penrose_city(p, fp=None, index=0):
    """The Penrose city: thick tiles are towers, thin tiles are roads, with roofs, walls, parks, trees, benches,
    paths, columns and elevator shafts. Returns (buckets, stats, records) like the other shapes."""
    if fp is None:
        raise RuntimeError("This arcology has no footprint mesh")
    _POSE_CACHE.clear()
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
    road_src = source_pool(p.coll_road)            # Roads: stretched along the long diagonal of each thin tile
    half_src = source_pool(p.coll_thin_rhombs)     # Thin Half Rhombs: on each side of that road
    half_rng = random.Random(seed * 7919 + 25)
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
            t["halves"] = _pen_halves(t, p.road_width_ft * FT)

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
            records.append(rhomb_record(pool, roof_rng, t["cen"], t["rel"], z0, z1 - z0, layer, "Roof", "roofs"))
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
                    records.append(rhomb_record(tower_src, copy_rng, t["cen"], t["rel"], z, H_, k + 1,
                                                "Thick Rhombs", "towers"))
                else:
                    add_prism(bucket("towers", k + 1), t["pts"], z, z + H_)
                roof(t, z + H_, z + P, k + 2)

    # Elevators: two shafts in the wide corners of each chosen thin tile, one per level. Chosen tiles are at least
    # Elevator Distance apart, centre to centre, picked in a random order from the seed. Their cells get no half rhombs.
    chosen = []
    if p.show_elevators:
        cand = [i for i, t in enumerate(tiles) if t["kind"] == 'thin' and len(t["wide"]) == 2]
        elev_rng.shuffle(cand)
        for i in cand:
            c = tiles[i]["cen"]
            if all((c - tiles[j]["cen"]).length >= p.elevator_distance for j in chosen):
                chosen.append(i)
    elev_tiles = set(chosen)
    halves_skipped = 0

    # Roads: a surface on each thin tile at ground level, and a roof over each road level above it.
    if p.show_roads:
        for ti, t in enumerate(tiles):
            if t["kind"] != 'thin':
                continue
            for k in range(max(1, t["top"] - 1)):
                z = ROAD_BASE + k * P
                if road_src:
                    records.append(stretch_record(road_src, road_rng, t["road_cen"], t["road_theta"], t["road_len"],
                                                  p.road_width_ft * FT, z, ROAD_H, k + 1, "Road", "road_rectangles"))
                else:
                    add_prism(bucket("road_rectangles", k + 1), t["road_pts"], z, z + ROAD_H)
                for hc, hrel, side in t["halves"]:
                    if ti in elev_tiles:
                        halves_skipped += 1
                        continue
                    if half_src:
                        turn = t["road_theta"] + (0.0 if side > 0 else math.pi)     # base on the road, apex away from it
                        records.append(piece_record(half_src, half_rng, hc, turn, hrel, z, H_, k + 1,
                                                    "Thin Half Rhombs", "thin_halves"))
            for k in range(t["top"] - 1):
                roof(t, ROAD_BASE + k * P + H_, ROAD_BASE + (k + 1) * P, k + 2)

    elev_count = 0
    if p.show_elevators:
        elev_h = ELEVATOR_H_FT * FT
        for i in chosen:
            t = tiles[i]
            u = t["road_u"]
            across = Vector((-u.y, u.x))
            for ci in t["wide"]:
                v, turn, shaft = _elevator_at(t, ci, across)
                for k in range(t["top"]):
                    z = ROAD_BASE + k * P
                    if elev_src:
                        records.append(elevator_record(elev_src, elev_rng, v, z, elev_h, k + 1, turn=turn, spin=True))
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
                rec = rhomb_record(pool, copy_rng, t["cen"], t["rel"], z_base, None, Hp, "Parks", "parks")
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
        "halves_skipped": halves_skipped,
    }
    return buckets, stats, records
