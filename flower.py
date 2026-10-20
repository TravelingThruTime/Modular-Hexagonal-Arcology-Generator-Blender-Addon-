"""Flower of Life arcology builder: circle lattice, the eight Flower Parts, legend."""


from types import SimpleNamespace
import math
import random

import bmesh
import bpy

from mathutils import Matrix, Vector

from .constants import FT, PARK_SLAB_H, PROXY_TAG, ROAD_BASE, ROAD_H
from .geometry import add_bench, add_prism, add_tree, bench_spots, hex_ring, mesh_height_tester, tree_spots
from .output import bmesh_normals, material_for
from .records import hex_record, poly_area, road_strip_record, source_pool


# ---------------------------------------------------------------------------
# Output objects
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Flower: circles from the Flower of Life. The centre of each circle is a junction, and the radius of each circle is
# the road length. Junctions sit on a triangular lattice one radius apart, so every circle overlaps its six neighbours.
# The circle outlines cut the plane into faces, of five kinds. Each kind has a prefab roof and a prefab park, and every
# face of that kind gets a copy of each on its own place, so no roof or park overlaps its neighbours. Each face has a
# roof between every two layers and a park on the top layer, with trees and benches. Each circle outline is a wall on
# every layer. Each junction
# has a Junction Hexagon, one level tall, with three elevators in alternate corners of it, rising to the top of its
# stack. A Roof Plane sets the layers, and the top layer stretches so it meets the plane.
# ---------------------------------------------------------------------------
FLOWER_JUNCTION_EDGE_FT = 30.0                # the Junction Hexagon's edge, fixed


FLOWER_ELEV_ANGLES = (30.0, 150.0, 270.0)   # the alternate corners of the Junction Hexagon that hold an elevator


FLOWER_SEGMENTS = 48                        # corners in each circle outline


FLOWER_FACE_SEG = 64                        # arc samples per full circle, where the circles are cut into faces


# The Flower Parts: eight pieces, drawn with the origin at a junction. Each is stored for a circle radius of 240 ft and
# scaled to the radius in use. A part is placed where the circles around its junction are drawn (FLOWER_PART_RULES).
# Its boundary arcs lie on circles that must be drawn, and no drawn circle may cut through it.
# The rule for each Flower Part, at turn 0 (origin at a junction). Lattice indices (i, j) are the circle centres
# i * (R, 0) + j * (R / 2, R * sqrt(3) / 2) from the junction. A part is drawn at a junction on a layer when every
# circle in its 'req' list is drawn there (its boundary) and no circle in its 'cut' list is drawn there (a circle
# through the part would split it). Those are exactly the conditions for the part to be one face of the arrangement
# of the circles drawn at the junction. Turning a part by 60 degrees turns each offset by the same 60 degrees.
FLOWER_PART_RULES = (
    ("1", ((-1, 1), (1, 0), ), ()),
    ("2", ((-1, 0), (0, 0), (1, 0), ), ((-1, 2), )),
    ("3", ((-2, 1), (-1, 1), (0, 0), (0, 1), (1, 1), ), ((-2, 1), (-1, 0), (-1, 1), (-1, 2), (0, 1), (1, 0), (1, 1), )),
    ("4", ((-1, 0), (0, 0), (1, -1), (1, 0), ), ((-1, 1), (1, 1), )),
    ("5", ((0, -1), (0, 0), (1, -1), ), ((-2, 1), (-1, 0), (-1, 1), (-1, 2), (0, 1), (1, 0), (1, 1), )),
    ("6", ((-2, 2), (0, 0), (1, -1), ), ((-2, 1), (-1, -1), (-1, 0), (-1, 1), (-1, 2), (0, -1), (0, 1), (1, 0), (1, 1), )),
    ("7", ((-1, 1), (1, -1), (1, 1), ), ()),
    ("8", ((-1, 2), (0, 0), (0, 1), (1, 0), ), ((-1, 1), (-1, 2), (1, -1), (1, 0), (1, 1), )),
)


# The corners of each of the twelve faces around a junction, for a circle radius of 1, with the junction at the origin
# (the same order as FLOWER_FACE_CIRCLES). A face's corners seen from one of its own corners are one of these sets.
FLOWER_FACE_CORNERS = (
    ((0.5, 0.866), (0.0, 0.0), (1.0, 0.0)), ((0.0, 0.0), (0.5, 0.866)),
    ((-0.5, 0.866), (0.0, 0.0), (0.5, 0.866)), ((0.0, 0.0), (-0.5, 0.866)),
    ((-0.5, 0.866), (-1.0, 0.0), (0.0, 0.0)), ((-1.0, 0.0), (0.0, 0.0)),
    ((-1.0, 0.0), (-0.5, -0.866), (0.0, 0.0)), ((-0.5, -0.866), (0.0, 0.0)),
    ((0.0, 0.0), (-0.5, -0.866), (0.5, -0.866)), ((0.5, -0.866), (0.0, 0.0)),
    ((0.0, 0.0), (0.5, -0.866), (1.0, 0.0)), ((0.0, 0.0), (1.0, 0.0)),
)


# The area of each of those faces, for a circle radius of 1. A face made by merging neighbours has a larger area.
FLOWER_FACE_AREAS = (
    0.1636, 0.1796, 0.1636, 0.1796, 0.1636, 0.1796, 0.1636, 0.1796, 0.1636, 0.1796, 0.1636, 0.1796,
)


FLOWER_FACE_PART_NAMES = ("1", "7")            # the Flower Parts that are single faces: 1 is a lens, 7 a triangle


FLOWER_PART_HEIGHT_FT = 30.0                 # each part is one level tall, with its origin at the top


# The eight Flower Parts: the low-poly cap outlines of the Flower Parts blend file, taken as they are, unrotated,
# in units of the circle radius (R = 240 ft at the default). A part is scaled by the circle radius in use, and its
# roof and park are scaled in Z by the roof height and the park slab height.
FLOWER_PARTS = (
    {"name": "1", "faces": (5,), "poly": ((0.00006, -0.00046), (0.00159, 0.02301), (0.00307, 0.04553), (0.00780, 0.11774), (0.01472, 0.15252), (0.01913, 0.17466), (0.03324, 0.24563), (0.04464, 0.27921), (0.05190, 0.30058), (0.07516, 0.36911), (0.09084, 0.40092), (0.10083, 0.42116), (0.13283, 0.48606), (0.15253, 0.51555), (0.16507, 0.53431), (0.20528, 0.59448), (0.22866, 0.62115), (0.24354, 0.63811), (0.29125, 0.69252), (0.31792, 0.71590), (0.33489, 0.73078), (0.38929, 0.77850), (0.41878, 0.79820), (0.43754, 0.81074), (0.49256, 0.84750), (0.48482, 0.72930), (0.45938, 0.60141), (0.41746, 0.47793), (0.35979, 0.36098), (0.28734, 0.25256), (0.20136, 0.15452), (0.10333, 0.06854))},
    {"name": "2", "faces": (6,), "poly": ((-0.00011, -0.00021), (-0.00384, 0.05673), (-0.00858, 0.12894), (-0.03402, 0.25683), (-0.07593, 0.38031), (-0.13361, 0.49726), (-0.20605, 0.60568), (-0.29203, 0.70372), (-0.39007, 0.78970), (-0.43832, 0.82194), (-0.49667, 0.86093), (-0.38010, 0.91841), (-0.25663, 0.96032), (-0.12873, 0.98576), (0.00139, 0.99429), (0.13150, 0.98576), (0.25940, 0.96032), (0.38287, 0.91841), (0.49982, 0.86074), (0.50100, 0.85995), (0.50055, 0.85302), (0.39964, 0.78560), (0.30160, 0.69962), (0.21562, 0.60158), (0.14317, 0.49316), (0.08550, 0.37621), (0.04359, 0.25273), (0.01815, 0.12484), (0.01038, 0.00627), (0.00287, 0.00126))},
    {"name": "3", "faces": (2, 3, 4), "poly": ((-0.00106, -0.00013), (-0.00261, -0.00023), (-0.00330, 0.00116), (-0.04351, 0.06133), (-0.05604, 0.08010), (-0.07575, 0.10959), (-0.12346, 0.16399), (-0.13834, 0.18096), (-0.16172, 0.20762), (-0.21613, 0.25534), (-0.23310, 0.27022), (-0.25976, 0.29360), (-0.31993, 0.33381), (-0.33870, 0.34634), (-0.36818, 0.36605), (-0.43309, 0.39805), (-0.45333, 0.40804), (-0.48514, 0.42372), (-0.55366, 0.44698), (-0.57503, 0.45424), (-0.60861, 0.46564), (-0.67959, 0.47975), (-0.70172, 0.48416), (-0.73651, 0.49108), (-0.80872, 0.49581), (-0.83124, 0.49728), (-0.86316, 0.49938), (-0.86298, 0.49974), (-0.79053, 0.60817), (-0.70455, 0.70620), (-0.60651, 0.79218), (-0.49809, 0.86463), (-0.38114, 0.92230), (-0.25766, 0.96422), (-0.12977, 0.98966), (0.00035, 0.99818), (0.13047, 0.98966), (0.25836, 0.96422), (0.38184, 0.92230), (0.49879, 0.86463), (0.60721, 0.79218), (0.70525, 0.70620), (0.79122, 0.60817), (0.86367, 0.49974), (0.86514, 0.49677), (0.80820, 0.49303), (0.73599, 0.48830), (0.60809, 0.46286), (0.48462, 0.42095), (0.36767, 0.36327), (0.25924, 0.29083), (0.16121, 0.20485), (0.07523, 0.10681), (0.04299, 0.05856), (0.00357, -0.00043))},
    {"name": "4", "faces": (0, 1), "poly": ((0.00069, -0.00014), (-0.00409, 0.00222), (-0.00527, 0.00300), (0.00295, 0.12849), (0.02839, 0.25638), (0.07031, 0.37986), (0.12798, 0.49681), (0.20043, 0.60523), (0.28641, 0.70327), (0.38444, 0.78925), (0.49287, 0.86169), (0.49325, 0.86189), (0.55160, 0.82290), (0.59985, 0.79066), (0.69789, 0.70468), (0.78387, 0.60664), (0.85631, 0.49822), (0.91399, 0.38127), (0.95590, 0.25779), (0.98134, 0.12990), (0.98608, 0.05769), (0.98981, 0.00075), (0.98523, -0.00151), (0.92511, 0.02814), (0.90487, 0.03812), (0.87306, 0.05380), (0.80454, 0.07707), (0.78316, 0.08432), (0.74958, 0.09572), (0.67861, 0.10984), (0.65647, 0.11424), (0.62169, 0.12116), (0.54948, 0.12589), (0.52696, 0.12737), (0.52027, 0.12781), (0.49157, 0.12969), (0.41936, 0.12495), (0.39684, 0.12348), (0.36145, 0.12116), (0.29048, 0.10704), (0.26834, 0.10264), (0.23356, 0.09572), (0.16503, 0.07246), (0.14366, 0.06520), (0.11008, 0.05380), (0.04518, 0.02180), (0.02494, 0.01182))},
    {"name": "5", "faces": (0, 1, 2, 3, 4), "poly": ((0.00017, 0.00005), (-0.00165, 0.00126), (-0.11860, 0.05894), (-0.24208, 0.10085), (-0.36997, 0.12629), (-0.50009, 0.13482), (-0.63021, 0.12629), (-0.75810, 0.10085), (-0.88158, 0.05894), (-0.93363, 0.03327), (-0.99555, 0.00273), (-0.99562, 0.00370), (-0.98709, 0.13382), (-0.96165, 0.26171), (-0.91973, 0.38519), (-0.86206, 0.50214), (-0.78961, 0.61056), (-0.70364, 0.70860), (-0.60560, 0.79458), (-0.49718, 0.86702), (-0.38023, 0.92470), (-0.31170, 0.94796), (-0.25675, 0.96661), (-0.12886, 0.99205), (0.00126, 1.00058), (0.13138, 0.99205), (0.25927, 0.96661), (0.38275, 0.92470), (0.49970, 0.86702), (0.60812, 0.79458), (0.70616, 0.70860), (0.79214, 0.61056), (0.86458, 0.50214), (0.92226, 0.38519), (0.96417, 0.26171), (0.98961, 0.13382), (0.99814, 0.00370), (0.99784, -0.00093), (0.99666, -0.00014), (0.87971, 0.05753), (0.75623, 0.09945), (0.62834, 0.12489), (0.49822, 0.13341), (0.36810, 0.12489), (0.24021, 0.09945), (0.11673, 0.05753))},
    {"name": "6", "faces": (0, 1, 2, 3, 4, 5, 6), "poly": ((0.00035, -0.00028), (-0.10807, -0.07272), (-0.20611, -0.15870), (-0.29209, -0.25674), (-0.36454, -0.36516), (-0.42221, -0.48211), (-0.46412, -0.60559), (-0.48956, -0.73348), (-0.49761, -0.85624), (-0.49988, -0.85512), (-0.60830, -0.78268), (-0.70634, -0.69670), (-0.79231, -0.59866), (-0.86476, -0.49024), (-0.92243, -0.37329), (-0.96435, -0.24981), (-0.98979, -0.12192), (-0.99832, 0.00820), (-0.98979, 0.13832), (-0.96435, 0.26621), (-0.92243, 0.38969), (-0.86476, 0.50664), (-0.79231, 0.61506), (-0.70634, 0.71310), (-0.60830, 0.79908), (-0.49988, 0.87152), (-0.38293, 0.92920), (-0.25945, 0.97111), (-0.13155, 0.99655), (-0.00144, 1.00508), (0.12868, 0.99655), (0.25658, 0.97111), (0.38005, 0.92920), (0.49700, 0.87152), (0.60543, 0.79908), (0.70346, 0.71310), (0.78944, 0.61506), (0.86189, 0.50664), (0.91956, 0.38969), (0.96147, 0.26621), (0.98691, 0.13832), (0.99544, 0.00820), (0.99496, 0.00084), (0.88028, 0.05740), (0.75680, 0.09931), (0.62891, 0.12475), (0.49879, 0.13328), (0.36867, 0.12475), (0.24078, 0.09931), (0.11730, 0.05740))},
    {"name": "7", "faces": (0,), "poly": ((-0.00106, -0.00429), (-0.00028, 0.00762), (0.10299, 0.07663), (0.20103, 0.16260), (0.28700, 0.26064), (0.35945, 0.36906), (0.41712, 0.48601), (0.45904, 0.60949), (0.48448, 0.73739), (0.49215, 0.85453), (0.49551, 0.80335), (0.50048, 0.72748), (0.52592, 0.59959), (0.56784, 0.47611), (0.62551, 0.35916), (0.69795, 0.25074), (0.78393, 0.15270), (0.88197, 0.06672), (0.92718, 0.03652), (0.98677, -0.00330), (0.87578, 0.05143), (0.75230, 0.09335), (0.62441, 0.11879), (0.49429, 0.12732), (0.36417, 0.11879), (0.23628, 0.09335), (0.11280, 0.05143), (-0.00104, -0.00470))},
    {"name": "8", "faces": (1, 2, 3), "poly": ((-0.00013, -0.00009), (-0.00204, 0.02902), (0.00649, 0.15914), (0.03193, 0.28703), (0.07384, 0.41051), (0.13152, 0.52746), (0.20396, 0.63588), (0.28994, 0.73392), (0.38798, 0.81990), (0.47546, 0.87835), (0.49924, 0.86662), (0.53371, 0.84359), (0.54444, 0.83642), (0.60766, 0.79418), (0.63883, 0.76684), (0.64853, 0.75833), (0.70570, 0.70820), (0.73303, 0.67703), (0.74154, 0.66733), (0.79167, 0.61016), (0.81471, 0.57569), (0.82188, 0.56496), (0.86412, 0.50174), (0.88246, 0.46456), (0.88817, 0.45298), (0.92179, 0.38479), (0.93512, 0.34553), (0.93927, 0.33331), (0.96371, 0.26131), (0.97180, 0.22065), (0.97431, 0.20799), (0.98915, 0.13342), (0.99186, 0.09205), (0.99270, 0.07917), (0.99754, 0.00540), (0.99225, 0.00187), (0.87530, -0.05580), (0.75182, -0.09772), (0.62393, -0.12316), (0.49381, -0.13169), (0.36369, -0.12316), (0.23580, -0.09772), (0.11232, -0.05580), (0.04413, -0.02217), (0.00133, -0.00107))},
)


def flower_layout(p):
    R = p.circle_radius_ft * FT
    LEVEL = p.level_ft * FT
    ROOF = p.roof_ft * FT
    return SimpleNamespace(
        R=R, N=p.circle_vertexes, LEVEL=LEVEL, ROOF_H=ROOF, PITCH=LEVEL + ROOF,
        WALL_W=p.wall_width_ft * FT, JUNC_EDGE=FLOWER_JUNCTION_EDGE_FT * FT,
        JUNC_APO=FLOWER_JUNCTION_EDGE_FT * FT * math.sqrt(3.0) / 2.0,
        ELEV_EDGE=p.elevator_edge_ft * FT, ELEV_APO=p.elevator_edge_ft * FT * math.sqrt(3.0) / 2.0)


def _flower_stack(H, L, cap):
    """Layers and top height for a cell whose Roof Plane sits H above the ground. Every layer is one level tall, with a
    roof between every two, so the layers stop at the last one that fits under the plane. Any gap left under the plane
    stays open."""
    m = int(math.floor((H + L.ROOF_H) / L.PITCH + 1e-4))     # how many layers fit under the plane
    n = min(cap, max(1, m))
    return n, L.LEVEL


def _flower_hits(c1, c2, R):
    """The points where the circles of radius R around c1 and c2 cross. Empty when they do not cross."""
    v = c2 - c1
    d = v.length
    if d < 1e-9 or d >= 2.0 * R * (1.0 - 1e-5):      # touching circles do not cross; Vector is single precision
        return []
    mid = c1 + v * 0.5
    h = math.sqrt(max(0.0, R * R - (d * 0.5) ** 2))
    perp = Vector((-v.y, v.x)) * (h / d)
    return [mid + perp, mid - perp]


FLOWER_DIR_EPS = 1e-5       # radians: directions this close are the same direction (float32 noise is far smaller)


def _flower_ccw(hs, ang, bend):
    """The half-edges leaving one crossing, counter-clockwise. Arcs that leave in the same direction (circles that
    touch there) are ordered by how they bend: the one bending to the right comes first, then the one to the left."""
    order = sorted(hs, key=lambda h: ang[h])
    n = len(order)
    if n < 2:
        return order
    gaps = [(ang[order[(i + 1) % n]] - ang[order[i]]) % math.tau for i in range(n)]
    start = (max(range(n), key=lambda i: gaps[i]) + 1) % n    # begin after the widest gap, so no group wraps round
    order = order[start:] + order[:start]
    out, group = [], [order[0]]
    for h in order[1:]:
        if (ang[h] - ang[group[-1]]) % math.tau <= FLOWER_DIR_EPS:
            group.append(h)
        else:
            out.extend(sorted(group, key=lambda g: bend[g]))
            group = [h]
    out.extend(sorted(group, key=lambda g: bend[g]))
    return out


def _flower_faces(centres, R):
    """The faces the circles of radius R around the centres cut the plane into. Each face is a counter-clockwise outline
    of 2D points that follows the arcs between the crossings. Only bounded faces are returned."""
    return [poly for poly, _ in _flower_faces_corners(centres, R)]


def _flower_faces_corners(centres, R):
    """As _flower_faces, but each face comes with its corners: the crossings where its arcs meet, counter-clockwise."""
    tol = 1e-4 * R                  # Vector is single precision, so crossings made by different pairs differ slightly
    cell = 1e-2 * R
    verts = []
    grid = {}

    def vertex(p):
        """The index of the crossing at p, made once and shared by every circle that passes through it."""
        i = int(math.floor(p.x / cell))
        j = int(math.floor(p.y / cell))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for k in grid.get((i + di, j + dj), ()):
                    if (verts[k] - p).length <= tol:
                        return k
        verts.append(p)
        grid.setdefault((i, j), []).append(len(verts) - 1)
        return len(verts) - 1

    on = [set() for _ in centres]
    for a in range(len(centres)):
        for b in range(a + 1, len(centres)):
            for P in _flower_hits(centres[a], centres[b], R):
                k = vertex(P)
                on[a].add(k)
                on[b].add(k)

    # Half-edges: each arc between two crossings, once in each direction. Forward arcs run counter-clockwise round their
    # circle, so the inside of the circle is on their left. Half-edge 2k is the forward arc, 2k+1 its reverse.
    H_orig, H_dest, H_pts, H_ang, H_bend = [], [], [], [], []
    for a, c in enumerate(centres):
        angle = {k: math.atan2(verts[k].y - c.y, verts[k].x - c.x) for k in on[a]}
        ids = sorted(angle, key=lambda k: angle[k])
        m = len(ids)
        if m < 2:
            continue
        for s in range(m):
            v0, v1 = ids[s], ids[(s + 1) % m]
            t0 = angle[v0]
            t1 = angle[v1] + (math.tau if s == m - 1 else 0.0)
            span = t1 - t0
            nseg = max(1, int(math.ceil(span / math.tau * FLOWER_FACE_SEG)))
            pts = [c + Vector((math.cos(t0 + span * q / nseg), math.sin(t0 + span * q / nseg))) * R
                   for q in range(nseg + 1)]
            pts[0] = verts[v0]
            pts[-1] = verts[v1]
            tan0 = Vector((-math.sin(t0), math.cos(t0)))
            tan1 = Vector((-math.sin(t1), math.cos(t1)))
            H_orig.append(v0)
            H_dest.append(v1)
            H_pts.append(pts)
            H_ang.append(math.atan2(tan0.y, tan0.x) % math.tau)
            H_bend.append(1.0)                      # a forward arc curves to the left of its tangent
            H_orig.append(v1)
            H_dest.append(v0)
            H_pts.append(pts[::-1])
            H_ang.append(math.atan2(-tan1.y, -tan1.x) % math.tau)
            H_bend.append(-1.0)                     # and its reverse curves to the right

    nh = len(H_orig)
    out = {}
    for h in range(nh):
        out.setdefault(H_orig[h], []).append(h)
    pos = [0] * nh
    for v in list(out):
        # Counter-clockwise around the crossing. Circles that touch there leave in the same direction, so ties are
        # broken by the way each arc bends: the one bending to the right comes first.
        hs = _flower_ccw(out[v], H_ang, H_bend)
        out[v] = hs
        for i, h in enumerate(hs):
            pos[h] = i

    # Walk each face: from a half-edge, take the next arc clockwise from its twin at the crossing, which keeps the face
    # on the left. Faces with a positive area are bounded; the outside of the whole figure has a negative one.
    used = [False] * nh
    faces = []
    for h0 in range(nh):
        if used[h0]:
            continue
        poly = []
        corners = []
        h = h0
        for _ in range(nh + 1):
            used[h] = True
            poly.extend(H_pts[h][:-1])
            corners.append(verts[H_orig[h]])
            t = h ^ 1
            hs = out[H_orig[t]]
            h = hs[(pos[t] - 1) % len(hs)]
            if h == h0:
                break
        n = len(poly)
        area = 0.5 * sum(poly[i].x * poly[(i + 1) % n].y - poly[(i + 1) % n].x * poly[i].y for i in range(n))
        if area > 1e-6 * R * R:
            faces.append((poly, corners))
    return faces


FLOWER_TYPES = ("lens 0", "lens 60", "lens 120", "triangle up", "triangle down")


def _flower_face_type(corners, R):
    """Which of the five kinds of face a face is, from its corners (crossings, which are junctions of the lattice).
    Two corners make a lens, and its chord, the line between the corners, sets which of the three lens kinds it is.
    Three corners make a triangle: one corner on top is a triangle pointing up, two on top is one pointing down.
    Each junction has three lenses and two triangles, and faces of one kind are copies of one another."""
    if len(corners) == 2:
        d = corners[1] - corners[0]
        a = math.degrees(math.atan2(d.y, d.x)) % 180.0
        return int(round(a / 60.0)) % 3
    top = max(c.y for c in corners)
    on_top = sum(1 for c in corners if c.y >= top - 1e-4 * R)
    return 3 if on_top == 1 else 4


def _flower_point_in_poly(q, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a.y > q.y) != (b.y > q.y):
            x = a.x + (q.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if q.x < x:
                inside = not inside
    return inside


def _flower_seg_dist(q, a, b):
    ab = b - a
    L2 = ab.length_squared
    t = 0.0 if L2 < 1e-30 else max(0.0, min(1.0, (q - a).dot(ab) / L2))
    return (q - (a + ab * t)).length


def _flower_pole(poly, grid=12):
    """A point inside the face, as far from its outline as the search finds, and that distance: the place a park, its
    trees and benches, and a copied roof or park are centred. Faces can curve, so the centroid may be outside them."""
    xs = [q.x for q in poly]
    ys = [q.y for q in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    best, best_d = None, -1.0
    n = len(poly)
    for i in range(grid):
        for j in range(grid):
            q = Vector((x0 + (x1 - x0) * (i + 0.5) / grid, y0 + (y1 - y0) * (j + 0.5) / grid))
            if not _flower_point_in_poly(q, poly):
                continue
            d = min(_flower_seg_dist(q, poly[k], poly[(k + 1) % n]) for k in range(n))
            if d > best_d:
                best, best_d = q, d
    if best is None:
        return sum(poly, Vector((0.0, 0.0))) / n, 0.0
    return best, best_d


def _flower_fit(hull, outline):
    """The largest uniform scale, at most 1, at which a piece with this hull (centred on the origin) stays inside the
    outline of a face kind, which is centred on the origin too."""
    def fits(s):
        return all(_flower_point_in_poly(q * s, outline) for q in hull)
    if fits(1.0):
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        if fits(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _flower_prefab(name, outline, height, cat):
    """A prefab: one prism over the outline of a face kind, from the ground to height. It is built once, is never linked
    to the scene, and its copies are placed on each face of the kind. drop_prefabs removes it after the copies."""
    bm = bmesh.new()
    add_prism(bm, [Vector((q.x, q.y)) for q in outline], 0.0, height)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    bmesh_normals(me)
    ob = bpy.data.objects.new(name, me)
    ob[PROXY_TAG] = True
    me.materials.append(material_for(cat))
    return {"src": {"obj": ob, "prototype": True}, "scale": 1.0, "height": height}


def _flower_part_outline(pt, turn, R):
    """The outline of a Flower Part, turned by turn sixtieths of a full turn about its origin, at circle radius R."""
    th = math.radians(60.0 * turn)
    c, s_ = math.cos(th), math.sin(th)
    return [Vector(((c * x - s_ * y) * R, (s_ * x + c * y) * R)) for x, y in pt["poly"]]


def _flower_face_cover():
    """For each of the twelve faces around a junction, the single-face Flower Part and turn that cover it. Turning a part
    by 60 degrees moves the face it covers two places on, so the lens and triangle parts cover all twelve."""
    cover = {}
    for name in FLOWER_FACE_PART_NAMES:
        pt = next(q for q in FLOWER_PARTS if q["name"] == name)
        f0 = pt["faces"][0]
        for turn in range(6):
            cover.setdefault((f0 + 2 * turn) % 12, (name, turn))
    return cover


def _flower_region_spots(poly, n, rng, R, scale):
    """Up to n points inside a part outline (relative to its origin, Vectors), clear of its edges and of each other.
    Returns (point, size) pairs, the size taken from scale."""
    xs = [q.x for q in poly]
    ys = [q.y for q in poly]
    margin = 0.12 * R
    spots = []
    for _ in range(n * 60):
        if len(spots) >= n:
            break
        q = Vector((rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys))))
        if not _flower_point_in_poly(q, poly):
            continue
        if min(_flower_seg_dist(q, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))) < margin:
            continue
        if all((q - o).length >= 0.3 * R for o, _ in spots):
            spots.append((q, rng.uniform(0.85, 1.15) * scale))
    return spots


def _flower_piece_record(pool, rng, J, theta, R, z, height, cat, group, layer):
    """A copy of one of your objects for a Flower Part: turned by theta about the junction J, its outline scaled to the
    circle radius R, its base at z and its height set by height. Its bottom centre sits on the junction."""
    src = pool[rng.randrange(len(pool))]
    bc = src["bottom_center"]
    rad = max(math.hypot(v.x - bc.x, v.y - bc.y) for v in src["hull"]) or 1.0
    s_xy = R / rad
    kz = height / src["height"] if src["height"] > 0 else 1.0
    P_ = Matrix.Translation((J.x, J.y, z)) @ Matrix.Rotation(theta, 4, 'Z')
    Sc = Matrix.Diagonal(Vector((s_xy, s_xy, kz, 1.0)))
    T = Matrix.Translation(-bc)
    return {"group": group, "cat": cat, "layer": layer, "src": src, "matrix": P_ @ Sc @ T @ src["xform"]}


def build_flower_city(p, fp=None, index=0):
    """The Flower city: circle walls on every layer, a roof and a park on each face the circles cut (both Flower Parts), a Junction Hexagon
    with three elevators at each junction, and trees and benches on the parks. Returns (buckets, stats, records)."""
    if fp is None:
        raise RuntimeError("This arcology has no footprint mesh")
    L = flower_layout(p)
    R = L.R
    seed = p.seed + index
    height_at, lo, hi = mesh_height_tester(fp)
    inside = lambda q: height_at(q.x, q.y) is not None
    cap = max(1, p.max_layers)
    plane = None
    if p.roof_plane is not None and p.roof_plane.type == 'MESH':
        plane, _, _ = mesh_height_tester(p.roof_plane)
    elev_rng = random.Random(seed * 7919 + 33)
    junc_rng = random.Random(seed * 7919 + 36)
    park_rng = random.Random(seed * 7919 + 39)      # park density, and the trees and benches on the parks
    elev_pool = source_pool(p.coll_elevators)
    junc_pool = []                                  # the Junction Hexagon is generated
    buckets = {}
    records = []

    def bucket(cat, layer, face=None):
        k = (cat, layer) if face is None else (cat, layer, face)
        if k not in buckets:
            buckets[k] = bmesh.new()
        return buckets[k]

    # Lattice of circle centres, one radius apart, with the footprint centre on a lattice point.
    lat_a = Vector((R, 0.0))
    lat_b = Vector((R / 2.0, R * math.sqrt(3.0) / 2.0))
    origin = Vector(((lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0))
    reach = math.hypot(hi.x - lo.x, hi.y - lo.y) / 2.0
    ext = reach + 2.5 * R                               # the circles that cut the faces at the footprint's edge
    n_range = int(2.0 * ext / R) + 3
    lattice = []
    for i in range(-n_range, n_range + 1):
        for j in range(-n_range, n_range + 1):
            J = origin + lat_a * float(i) + lat_b * float(j)
            if (J - origin).length <= ext:
                lattice.append(J)
    junctions = {}
    for J in lattice:
        if inside(J):
            junctions.setdefault((round(J.x, 3), round(J.y, 3)), J)
    if not junctions:
        raise RuntimeError("No Flower circles fall inside the footprint.")

    # Stacks of the junctions: walls, Junction Hexagons and elevators.
    stacks = {}
    for key, J in junctions.items():
        if plane is None:
            stacks[key] = (cap, L.LEVEL)
            continue
        z = plane(J.x, J.y)
        stacks[key] = (1, L.LEVEL) if z is None else _flower_stack(z - ROAD_BASE, L, cap)

    def stack_top(key):
        n, top_h = stacks[key]
        return (n - 1) * L.PITCH + top_h                # height above the ground to the top of the stack

    # Circle outlines: a wall of WALL_W around each circle, on every layer of its junction.
    walls = 0
    r_out = R + L.WALL_W / 2.0
    r_in = R - L.WALL_W / 2.0
    for key, J in junctions.items():
        n, top_h = stacks[key]
        for k in range(1, n + 1):
            z = ROAD_BASE + (k - 1) * L.PITCH
            h = top_h if k == n else L.LEVEL
            for m in range(L.N):
                t0 = 2.0 * math.pi * m / L.N
                t1 = 2.0 * math.pi * (m + 1) / L.N
                u0 = Vector((math.cos(t0), math.sin(t0)))
                u1 = Vector((math.cos(t1), math.sin(t1)))
                add_prism(bucket("walls", k), [J + u0 * r_out, J + u1 * r_out, J + u1 * r_in, J + u0 * r_in],
                          z, z + h)
                walls += 1

    # Junction Hexagons: one level tall, fitted to the Junction Edge.
    for key, J in junctions.items():
        if junc_pool:
            records.append(hex_record(junc_pool, junc_rng, J, ROAD_BASE, L.LEVEL, 1,
                                      SimpleNamespace(APO=L.JUNC_APO), "Junction Hexagon", "junctions"))
        else:
            add_prism(bucket("junctions", 1), hex_ring(J, L.JUNC_EDGE), ROAD_BASE, ROAD_BASE + L.LEVEL)

    # Elevators: three per junction, in alternate corners of its Junction Hexagon, inside the corner so their outer
    # edges stay within the hexagon. They rise to the top of the junction's stack.
    corner_r = max(0.0, L.JUNC_EDGE - L.ELEV_EDGE)
    for key, J in junctions.items():
        height = stack_top(key)
        for ang in FLOWER_ELEV_ANGLES:
            d = Vector((math.cos(math.radians(ang)), math.sin(math.radians(ang))))
            c = J + d * corner_r
            if elev_pool:
                records.append(hex_record(elev_pool, elev_rng, c, ROAD_BASE, height, 1,
                                          SimpleNamespace(APO=L.ELEV_APO), "Elevators", "elevators"))
            else:
                add_prism(bucket("elevators", 1), hex_ring(c, L.ELEV_EDGE), ROAD_BASE, ROAD_BASE + height)

    # Faces: the regions the circles cut the footprint into. Every face is one of the twelve around a junction, so its
    # roof and park are a Flower Part: the part that covers that face, placed on the junction it belongs to.
    cells = []
    for poly, corners in _flower_faces_corners(lattice, R):
        pole, apo = _flower_pole(poly)
        if apo < 0.5 * FT or not inside(pole):
            continue
        cells.append((poly, pole, apo, _flower_face_type(corners, R), corners))
    cover = _flower_face_cover()

    def face_anchor(poly, corners):
        """The junction and the index of the face around it (0 to 11), from a face's corners: seen from one of its
        corners, the face has the corners of one of the twelve faces around a junction, and the same area. None if not."""
        area = poly_area(poly) / (R * R)
        for c in corners:
            rel = [((q.x - c.x) / R, (q.y - c.y) / R) for q in corners]
            for g, canon in enumerate(FLOWER_FACE_CORNERS):
                if len(canon) == len(rel) and abs(area - FLOWER_FACE_AREAS[g]) < 2e-3 \
                        and all(any(math.hypot(a - x, b - y) < 1e-3 for x, y in canon) for a, b in rel):
                    return c, g
        return None, None

    part_prefabs = {}
    park_pieces = {}

    def part_prefab(g, height, cat):
        """The prefab of the Flower Part that covers face g, one prism of this height. Built once per height."""
        name, turn = cover[g]
        key = (name, turn, round(height, 6), cat)
        if key not in part_prefabs:
            pt = next(q for q in FLOWER_PARTS if q["name"] == name)
            part_prefabs[key] = _flower_prefab(f"Flower {cat} {name}.{turn}", _flower_part_outline(pt, turn, R),
                                               height, cat)
        return part_prefabs[key]

    parks = trees = benches = roofs = cells_counted = 0
    top_layer = 1
    for f, (poly, pole, apo, t, corners) in enumerate(cells):
        J, g = face_anchor(poly, corners)
        if J is None:
            continue
        z_h = plane(pole.x, pole.y) if plane is not None else None
        if plane is None:
            n, top_h = cap, L.LEVEL
        elif z_h is None:
            n, top_h = 1, L.LEVEL
        else:
            n, top_h = _flower_stack(z_h - ROAD_BASE, L, cap)
        top_layer = max(top_layer, n)
        cells_counted += n - 1
        cell_L = SimpleNamespace(APO=apo, S=2.0 * apo, R=2.0 * apo / math.sqrt(3.0), TREE_SCALE=p.tree_scale)
        for k in (range(1, n) if not p.use_parts else []):
            z0 = ROAD_BASE + (k - 1) * L.PITCH + L.LEVEL
            records.append({"group": "Roof", "cat": "roofs", "layer": k + 1,
                            "src": part_prefab(g, L.ROOF_H, "roofs")["src"],
                            "matrix": Matrix.Translation((J.x, J.y, z0))})
            roofs += 1
        if not p.use_parts and p.show_parks and park_rng.random() <= p.park_density:
            z_base = ROAD_BASE + (n - 1) * L.PITCH + top_h
            slab_h = PARK_SLAB_H
            records.append({"group": "Parks", "cat": "parks", "layer": n,
                            "src": part_prefab(g, slab_h, "parks")["src"],
                            "matrix": Matrix.Translation((J.x, J.y, z_base))})
            trees_here = tree_spots(pole, p.trees_per_cell, park_rng, cell_L) if p.show_trees and p.tree_scale > 0 else []
            for pos, sc in trees_here:
                add_tree(bucket("trunks", n, f), bucket("canopies", n, f), pos, z_base + slab_h, sc, cell_L)
                trees += 1
            if p.show_benches:
                for pos, th_b in bench_spots(pole, p.benches_per_cell, trees_here, park_rng, cell_L):
                    add_bench(bucket("benches", n, f), pos, th_b, z_base + slab_h, cell_L)
                    benches += 1
            parks += 1

    # Flower Parts. At each junction and layer, the circles drawn at the lattice points around it decide which faces
    # are drawn (FLOWER_FACE_CIRCLES). The part whose faces that set is, turned as needed, is placed with its origin on
    # the junction, one level up on that layer. Parts that match no drawn set are not placed.
    parts_placed = 0
    pieces = {}
    if p.use_parts:
        H = FLOWER_PART_HEIGHT_FT * FT          # the unit block each part is built at; its copies are scaled in Z
        SLAB = p.roof_ft * FT                   # the roof height: a 0.2 Z scale of the 30 ft block
        # Your objects, from the collections picked for each Flower Part and for the roads (empty = generated)
        piece_pools = {nm: source_pool(getattr(p, f"coll_part_{nm}")) for nm in "12345678"}
        road_pool = source_pool(p.coll_roads)
        piece_rng = random.Random(seed * 104729 + 17)
        lat_a = Vector((R, 0.0))
        lat_b = Vector((R / 2.0, R * math.sqrt(3.0) / 2.0))
        def turn_offset(o, t):
            i, j = o
            for _ in range(t % 6):
                i, j = -j, i + j
            return (i, j)

        turned = []
        for name, req, cut in FLOWER_PART_RULES:
            for turn in range(6):
                turned.append((name, turn,
                               frozenset(turn_offset(o, turn) for o in req),
                               frozenset(turn_offset(o, turn) for o in cut)))
        ring = sorted({o for _, _, req_t, cut_t in turned for o in req_t | cut_t})

        def drawn_at(J, offset, k):
            """Whether the circle centred at the lattice point offset from junction J is drawn on layer k."""
            c = J + lat_a * float(offset[0]) + lat_b * float(offset[1])
            st = stacks.get((round(c.x, 3), round(c.y, 3)))
            return st is not None and st[0] >= k

        region_done = []
        for key, J in junctions.items():
            for k in range(1, cap + 1):
                drawn = frozenset(o for o in ring if drawn_at(J, o, k))
                for name, turn, req_t, cut_t in turned:
                    if not req_t <= drawn or cut_t & drawn:
                        continue
                    pt = next(q for q in FLOWER_PARTS if q["name"] == name)
                    th = math.radians(60.0 * turn)
                    c_, s_ = math.cos(th), math.sin(th)
                    # The same region is matched from each of its corners: place it once, keyed by its centre.
                    mx = J.x + R * sum(c_ * x - s_ * y for x, y in pt["poly"]) / len(pt["poly"])
                    my = J.y + R * sum(s_ * x + c_ * y for x, y in pt["poly"]) / len(pt["poly"])
                    # Same name, layer and place within a twelfth of a radius: the same region, already placed.
                    if any(kk == k and nn == name and math.hypot(mx - ux, my - uy) < R / 12.0
                           for kk, nn, ux, uy in region_done):
                        continue
                    region_done.append((k, name, mx, my))
                    pname = f"{name}.{turn}"
                    rel = [Vector(((c_ * x - s_ * y) * R, (s_ * x + c_ * y) * R)) for x, y in pt["poly"]]
                    if pname not in pieces:
                        pieces[pname] = _flower_prefab(f"Flower part {pname}", rel, H, "parts")
                    pick = pieces[pname]
                    z_top = ROAD_BASE + (k - 1) * L.PITCH + L.LEVEL
                    # Nothing drawn above the part means it is the top of its stack: no roof above it (the Roof
                    # Plane caps the city), only a park on the layer.
                    nxt = frozenset(o for o in ring if drawn_at(J, o, k + 1))
                    top = not (req_t <= nxt and not (cut_t & nxt))
                    # The structure of this layer: the part, one layer tall, from the layer's base. It is your object
                    # when a Flower Part collection is picked, otherwise the generated part.
                    z_base = ROAD_BASE + (k - 1) * L.PITCH
                    if piece_pools[name]:
                        records.append(_flower_piece_record(piece_pools[name], piece_rng, J,
                                                            math.radians(60.0 * turn), R, z_base, L.LEVEL,
                                                            "parts", "Flower Parts", k))
                    else:
                        records.append({"group": "Flower Parts", "cat": "parts", "layer": k, "src": pick["src"],
                                        "matrix": Matrix.Translation((J.x, J.y, z_base))
                                                  @ Matrix.Diagonal((1.0, 1.0, L.LEVEL / H, 1.0))})
                    parts_placed += 1
                    # The roof between this layer and the next: a generated slab of the region's outline.
                    if not top:
                        if ("roof", pname) not in pieces:
                            pieces[("roof", pname)] = _flower_prefab(f"Flower roof {pname}", rel, H, "roofs")
                        records.append({"group": "Roof", "cat": "roofs", "layer": k + 1,
                                        "src": pieces[("roof", pname)]["src"],
                                        "matrix": Matrix.Translation((J.x, J.y, z_top))
                                                  @ Matrix.Diagonal((1.0, 1.0, SLAB / H, 1.0))})

                    # The top of a stack gets a park on its layer, with trees and benches inside its own outline.
                    if top and p.show_parks and park_rng.random() <= p.park_density:
                        if pname not in park_pieces:
                            park_pieces[pname] = _flower_prefab(f"Flower park {pname}", rel, H, "parks")
                        z_park = z_top
                        records.append({"group": "Parks", "cat": "parks", "layer": k + 1,
                                        "src": park_pieces[pname]["src"],
                                        "matrix": Matrix.Translation((J.x, J.y, z_park))
                                                  @ Matrix.Diagonal((1.0, 1.0, PARK_SLAB_H / H, 1.0))})
                        pid = 100000 + len(region_done)
                        cell_L = SimpleNamespace(APO=R * 0.5, S=R, R=R / math.sqrt(3.0), TREE_SCALE=p.tree_scale)
                        trees_here = _flower_region_spots(rel, p.trees_per_cell, park_rng, R, p.tree_scale) \
                            if p.show_trees and p.tree_scale > 0 else []
                        for q_, sc in trees_here:
                            add_tree(bucket("trunks", k + 1, pid), bucket("canopies", k + 1, pid), J + q_,
                                     z_park + PARK_SLAB_H, sc, cell_L)
                            trees += 1
                        if p.show_benches:
                            for q_, _ in _flower_region_spots(rel, p.benches_per_cell, park_rng, R, 1.0):
                                add_bench(bucket("benches", k + 1, pid), J + q_, park_rng.uniform(0.0, math.tau),
                                          z_park + PARK_SLAB_H, cell_L)
                                benches += 1
                        parks += 1
                    if name == "1":
                        # The road runs through part 1 along its chord, from this junction to the far corner.
                        road_w = p.road_width_ft * FT
                        vx, vy = -c_, -s_                                     # the far corner, turned
                        nx, ny = -vy, vx
                        ex, ey = vx * R, vy * R
                        hw = road_w / 2.0
                        rect = [Vector((nx * hw, ny * hw)), Vector((ex + nx * hw, ey + ny * hw)),
                                Vector((ex - nx * hw, ey - ny * hw)), Vector((-nx * hw, -ny * hw))]
                        rname = f"road.{turn}"
                        if rname not in pieces:
                            pieces[rname] = _flower_prefab(f"Flower road {turn}", rect, ROAD_H, "roads")
                        z_road = z_top + (SLAB if not top else PARK_SLAB_H)
                        if road_pool:
                            records.append(road_strip_record(road_pool, piece_rng, J + Vector((vx, vy)) * (R / 2.0),
                                                             math.atan2(vy, vx), R, road_w, z_road, ROAD_H, k))
                        else:
                            records.append({"group": "Flower Roads", "cat": "roads", "layer": k,
                                            "src": pieces[rname]["src"],
                                            "matrix": Matrix.Translation((J.x, J.y, z_road))})

    # A prefab that no copy was made from is not needed: remove it now, so nothing is left unlinked.
    used = {id(rec["src"]) for rec in records}
    for pick in list(part_prefabs.values()) + list(pieces.values()):
        src = pick["src"]
        if src.get("prototype") and id(src) not in used:
            me = src["obj"].data
            bpy.data.objects.remove(src["obj"])
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)

    count = len(junctions)
    stats = {"footprint": count, "cells_counted": cells_counted, "top_layer": top_layer, "towers": 0,
             "walls": walls, "roofs": roofs, "faces": len(cells), "parks": parks, "trees": trees, "benches": benches,
             "centres": count, "junctions": count, "elevators": count * len(FLOWER_ELEV_ANGLES),
             "junction_hexagons": count, "parts": parts_placed}

    # Legend: the eight Flower Parts, flat, in a row with their numbers, 300 ft in +Y above every arc of the city.
    # The highest arc is a circle's outline, R above its centre, so the row starts 300 ft above that.
    if junctions:
        y_top = max(J.y for J in junctions.values()) + R
    else:
        y_top = origin.y + R
    min_py = min(y for pt in FLOWER_PARTS for _, y in pt["poly"])      # the lowest point of any part, below its origin
    y_leg = y_top + 300.0 * FT - min_py * R                           # so the lowest point is 300 ft above the arcs
    leg_z0, leg_z1 = ROAD_BASE, ROAD_BASE + L.ROOF_H
    seg_w, seg_h, stroke = 0.35 * R, 0.6 * R, 0.06 * R
    SEG = {"a": (0, seg_h - stroke, seg_w, seg_h), "d": (0, 0, seg_w, stroke),
           "g": (0, seg_h / 2 - stroke / 2, seg_w, seg_h / 2 + stroke / 2),
           "b": (seg_w - stroke, seg_h / 2, seg_w, seg_h), "c": (seg_w - stroke, 0, seg_w, seg_h / 2),
           "f": (0, seg_h / 2, stroke, seg_h), "e": (0, 0, stroke, seg_h / 2)}
    DIGIT = {"1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgecd",
             "7": "abc", "8": "abcdefg"}
    leg = bucket("legend", 1)
    for i, pt in enumerate(FLOWER_PARTS):
        ox = origin.x + ((i % 4) - 1.5) * 2.4 * R                  # four to a row, two rows
        oy = y_leg + (i // 4) * 3.4 * R
        outline = [(ox + x * R, oy + y * R) for x, y in pt["poly"]]
        add_prism(leg, [Vector(q) for q in outline], leg_z0, leg_z1)
        lx, ly = ox - seg_w / 2.0, oy + 1.3 * R
        for sg in DIGIT[pt["name"]]:
            x0, y0, x1, y1 = SEG[sg]
            add_prism(leg, [Vector((lx + x0, ly + y0)), Vector((lx + x1, ly + y0)),
                            Vector((lx + x1, ly + y1)), Vector((lx + x0, ly + y1))], leg_z0, leg_z1)

    return buckets, stats, records
