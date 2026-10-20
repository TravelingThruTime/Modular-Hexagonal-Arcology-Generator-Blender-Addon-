"""Rectangle arcology builder, with road rectangles."""


from types import SimpleNamespace
import math
import random

import bmesh

from mathutils import Matrix, Vector

from .constants import ELEVATOR_H_FT, FT, PARK_SLAB_H, PATH_LIFT_FT, PATH_WIDTH_FT, ROAD_BASE, ROAD_H, TILE_BOT, TILE_TOP, TREE_RADIUS_FRAC, WALL_THICK_FRAC
from .geometry import add_bench, add_prism, add_tree, layers_under, mesh_height_tester, rect_poly, rot2
from .octagon import HALLWAY_HALF_FT, _subtract_convex, oct_random_roads, oct_ring
from .records import elevator_record, park_record, road_strip_record, source_pool, wall_record


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
            "matrix": T @ Matrix.Rotation(turn, 4, 'Z') @ Sz @ C @ src["xform"]}


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
            "matrix": P_ @ Sc @ T @ src["xform"]}


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
    road_src = source_pool(p.coll_road)
    strip_rng = random.Random(seed * 7919 + 27)      # road strip picks have their own random sequence
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

    # Roads: a strip from the centre out to each side the road leaves by. With Road picked, each strip is a copy of
    # your Road object. With Road Rectangles picked, each road rectangle gets two copies, 180 degrees apart. Without,
    # it is the rectangle less the strips and the hallway.
    if p.show_roads:
        for key in sorted(roads | hubs):
            c = cells[key]
            dirs = links.get(key, [])
            for k in range(max(1, hmax[key] - 1)):
                z = ROAD_BASE + k * P
                for dx, dy in dirs:
                    u = Vector((dx, dy))
                    reach = L.HW if dx else L.HL
                    if road_src:
                        records.append(road_strip_record(road_src, strip_rng, s_centre(c, u, reach), math.atan2(dy, dx),
                                                         reach, L.ROAD_W, z, ROAD_H, k + 1))
                    else:
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
