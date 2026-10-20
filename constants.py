"""Shared constants: collection names, sizes, colours and the shape names."""


import math


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
    "parts": (0.80, 0.55, 0.30, 1.0),
    "canopies": (0.12, 0.35, 0.12, 1.0),
    "trunks": (0.36, 0.25, 0.14, 1.0),
    "benches": (0.50, 0.34, 0.20, 1.0),
    "columns": (0.80, 0.78, 0.72, 1.0),
    "paths": (0.80, 0.76, 0.66, 1.0),
    "walls": (0.85, 0.85, 0.80, 1.0),
    "squares": (0.80, 0.80, 0.78, 1.0),
    "halves": (0.78, 0.74, 0.66, 1.0),
    "junctions": (0.55, 0.56, 0.60, 1.0),
    "legend": (0.95, 0.85, 0.30, 1.0),
}


# ---------------------------------------------------------------------------
# Octagon city (its own tab). Octagons sit on a square grid with pitch D = edge * (1 + sqrt 2), so
# neighbours share sides across the axes, and the gap between four octagons is a square (side = edge)
# turned 45 degrees. Roads run through the octagon centres, along the rows and columns.
# ---------------------------------------------------------------------------
SHAPE_HEX = "HEXAGON"


SHAPE_OCT = "OCTAGON"


SHAPE_RECT = "RECTANGLE"


SHAPE_PEN = "PENROSE"


SHAPE_FLOWER = "FLOWER"


SQRT2 = math.sqrt(2.0)


MANHATTAN_DENSITY = 75000.0          # people per square mile, for the Stats box comparison
