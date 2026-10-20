"""Hexagon arcology builder."""


import math
import random

import bmesh

from mathutils import Vector

from .constants import COLUMN_R, ELEVATOR_H_FT, ELEVATOR_R_FRAC, ELEVATOR_WALL_FRAC, FT, PARK_SLAB_H, PATH_LIFT_FT, ROAD_BASE, ROAD_H, TILE_BOT, TILE_TOP, TRI_CELLS, WALL_EDGES
from .geometry import _hex_dir, add_bench, add_path, add_prism, add_tree, bench_spots, hex_ring, lattice_points, lattice_vec, layers_under, make_layout, mesh_height_tester, overlap_depth, place_parks, random_roads, rect_poly, rot2, trapezoid_spots, tree_spots
from .records import elevator_record, hex_record, park_record, place_picked_parks, poly_area, road_strip_record, source_pool, trap_record, wall_record


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
    road_src = source_pool(p.coll_road)
    road_rng = random.Random(seed * 7919 + 4)     # road strip picks have their own random sequence
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

    # Roads: one strip per hex the road passes through, on the ground and on each level up to that hex's top layer.
    # With Road picked, each strip is a copy of your Road object.
    if p.show_roads:
        for run in runs:
            d = (pts[run[-1]] - pts[run[0]]).normalized()
            theta = math.atan2(d.y, d.x)
            for key in run:
                c = pts[key]
                for k in [0] + list(range(1, hmax[key] - 1)):
                    z = ROAD_BASE + k * P
                    if road_src:
                        records.append(road_strip_record(road_src, road_rng, c, theta, L.S, road_w, z, ROAD_H, k + 1))
                    else:
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
