"""Octagon arcology builder, with its road octagons."""


from types import SimpleNamespace
import math
import random

import bmesh

from mathutils import Matrix, Vector

from .constants import ELEVATOR_H_FT, FT, PARK_SLAB_H, PATH_LIFT_FT, ROAD_BASE, ROAD_H, SQRT2, TILE_BOT, TILE_TOP, WALL_THICK_FRAC
from .geometry import add_bench, add_oct_path, add_prism, add_tree, bench_spots, choose_lines, layers_under, mesh_height_tester, rect_poly, rot2, tree_spots
from .records import elevator_record, park_record, road_strip_record, source_pool, wall_record


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
            "matrix": P_ @ Sc @ T @ src["xform"]}


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


def road_octagon_record(pool, rng, c, z, height, layer, turn=0.0, src=None):
    """A copy of one Quarter Octagons object on a road or junction octagon. Its origin sits at the centre of the
    octagon cell, and it keeps its size. Your object is the quarter between +X and +Y. turn rotates it about the
    centre, so the four copies (0, 90, 180 and 270 degrees) fill the cell. It is stretched to the road height."""
    if src is None:
        src = pool[rng.randrange(len(pool))]
    kz = height / src["height"] if src["height"] > 0 else 1.0
    C = Matrix.Translation((-src["c0"].x, -src["c0"].y, -src["zb"]))
    Sz = Matrix.Diagonal(Vector((1.0, 1.0, kz, 1.0)))
    T = Matrix.Translation((c.x, c.y, z))
    return {"group": "Quarter Octagons", "cat": "roads", "layer": layer, "src": src,
            "matrix": T @ Matrix.Rotation(turn, 4, 'Z') @ Sz @ C @ src["xform"]}


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
    sq_park_src = source_pool(p.coll_square_parks) or park_src     # Square Parks, else the Parks objects
    elev_src = source_pool(p.coll_elevators)
    quarter_src = source_pool(p.coll_road_octagons)
    road_src = source_pool(p.coll_road)
    road_rng = random.Random(seed * 7919 + 24)       # quarter picks
    strip_rng = random.Random(seed * 7919 + 26)      # road strip picks

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

    # Roads. Each road and junction octagon gets its road strips, one from the centre out to each side it leaves
    # by. With Road picked, each strip is a copy of your Road object. With Quarter Octagons picked, each road octagon
    # gets four copies per level, one in each quarter. Without it, each one is the octagon less its strips and
    # hallways. Transport octagons (intersections) get only the road strips and elevators, no quarters.
    links = {}
    for a, b in edges:
        d = (b[0] - a[0], b[1] - a[1])
        links.setdefault(a, []).append(d)
        links.setdefault(b, []).append((-d[0], -d[1]))
    if p.show_roads:
        for key in sorted(roads | hubs):
            c = octs[key]
            dirs = links.get(key, [])
            for k in range(max(1, hmax[key] - 1)):
                z = ROAD_BASE + k * P
                for dx, dy in dirs:                       # the 20 ft road, from the centre out to each side it leaves
                    u = Vector((dx, dy))
                    centre = c + u * (L.APO / 2.0)
                    if road_src:
                        records.append(road_strip_record(road_src, strip_rng, centre, math.atan2(dy, dx), L.APO,
                                                         L.ROAD_W, z, ROAD_H, k + 1))
                    else:
                        add_prism(bucket("roads", k + 1), rect_poly(centre, u, L.APO / 2.0, L.ROAD_W / 2.0), z, z + ROAD_H)
                if key not in roads:                      # a transport octagon is an intersection: no quarters in it
                    continue
                if quarter_src:
                    # Four copies per road cell, one in each quarter about the cell centre, all from the same object.
                    pick = quarter_src[road_rng.randrange(len(quarter_src))]
                    for turn in (0.0, math.pi / 2.0, math.pi, 1.5 * math.pi):
                        records.append(road_octagon_record(quarter_src, road_rng, c, z, H_, k + 1, turn, pick))
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
            pool = sq_park_src if is_sq else park_src
            if pool:
                src = pool[copy_rng.randrange(len(pool))]
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

    # Cells for the Stats box, every level, less the top (park) level. Octagons and squares are counted apart, and
    # transport octagons (junctions) are left out of both.
    octagon_cells = sum(max(0, hmax[key] - 1) for key in octs if key not in hubs)
    square_cells = sum(max(0, sq_hmax[key] - 1) for key in sqs) if p.show_squares else 0
    stats = {
        "footprint": len(octs),
        "squares": len(sqs),
        "octagon_cells": octagon_cells,
        "square_cells": square_cells,
        "cells_counted": octagon_cells + square_cells,
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
