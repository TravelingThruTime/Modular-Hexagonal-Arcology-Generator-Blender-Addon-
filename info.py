"""Help text used by the info popups and the Preferences panel."""


import textwrap


RECT_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Rectangles: no origin needed. The add-on uses each object's bounding box.",
    "Width Road Rectangles: put the origin at the centre of the rectangle. The road runs along X (the width) and "
    "the 12 ft hallway along Y. The object is stretched to the rectangle's width and length.",
    "Length Road Rectangles: put the origin at the centre of the rectangle. The road runs along Y (the length) and "
    "the 12 ft hallway along X. The object is stretched to the rectangle's width and length.",
    "Roads: put the origin at the centre of the road strip. The road runs along the object's X axis. The copy is "
    "stretched to the road width and from the rectangle centre to its edge.",
    "Roof: no origin needed. The add-on uses each object's bounding box.",
    "Parks: put the origin at the centre of the rectangle it sits on.",
    "Elevators: put the origin at the centre of the transport rectangle. One copy holds all four shafts.",
    "Wall exteriors must be facing west, and wall lengths must be facing North/South.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


PENROSE_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Thick Rhombs (towers): no origin needed. Each copy is turned to the tile's edge, scaled to fill it and stretched to one level tall.",
    "Roads: no origin needed. Each copy is stretched along the long diagonal of a thin tile, to the road's length and width, and to the road height.",
    "Thin Half Rhombs: no origin needed. Each copy is turned along the road and scaled evenly to fill the piece on one side of it, on each level of the road.",
    "Thick Roofs and Thin Roofs: no origin needed. Each copy is turned to the tile's edge, scaled to fill it and stretched to the roof height.",
    "Thick Parks and Thin Parks: no origin needed. Each copy is turned to the tile's edge, scaled to fill it, and sits on the top level.",
    "Walls: no origin needed. Each copy is turned out of the tile, its exterior facing west, and stretched to the edge length.",
    "Elevators, origin: put the origin on the bottom corner of the shaft's outline, the point where its two sides meet. Each copy sits on a corner of the thin tile at its real size, and is 36 ft tall per level.",
    "Elevators, rotation: lay the shaft out so its flat top runs west to east, with the flat side facing north. Set the object's rotation to 0 and do not apply it. The flat top sits on the road edge.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


FLOWER_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Junction Hexagon: put the origin at the centre of the hexagon. Each copy is turned and fitted to the Junction Edge, "
    "one level tall.",
    "Roof: put the origin at the centre of the hexagon. Each copy is turned and fitted to the junction hexagon, one roof "
    "tall, between every two layers.",
    "Parks: put the origin at the centre of the hexagon. Each copy is turned and fitted to the junction hexagon, on the "
    "top layer.",
    "Elevators: put the origin at the centre of the hexagon. Each copy is turned and fitted to the elevator edge, three "
    "in the corners of each Junction Hexagon.",
    "The circle outlines are concrete walls, the width in Size, on every layer. They are generated, with no picker.",
)


COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Hexagons: no origin needed. The add-on uses each object's bounding box.",
    "Roof: no origin needed. The add-on uses each object's bounding box.",
    "Elevators: put the origin at the centre of the transport hex cell it sits in. One copy replaces all six shafts.",
    "Road Trapezoids: put the origin at the centre of the hex cell it sits in.",
    "Roads: put the origin at the centre of the road hex. The road runs along the object's X axis. The copy is "
    "stretched to one hex across and to the road width.",
    "Parks: put the origin at the centre of the park's first hex cell.",
    "Wall exteriors must be facing west, and wall lengths must be facing North/South.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


OCT_COLLECTION_INFO = (
    "Where the origin of your objects matters:",
    "",
    "Octagons: no origin needed. The add-on uses each object's bounding box.",
    "Quarter Octagons: put the origin at the centre of the octagon cell. Your object is the quarter between +X and +Y. "
    "The add-on copies it four times, turned 90 degrees each time, so four quarters fill each road octagon.",
    "Roads: put the origin at the centre of the road strip. The road runs along the object's X axis. The copy is stretched "
    "to the road width and from the octagon centre to its edge.",
    "Squares: no origin needed. Each copy fills one level of a square tower.",
    "Octagon Roofs: no origin needed. The add-on uses each object's bounding box.",
    "Square Roofs: no origin needed. The add-on uses each object's bounding box.",
    "Square Parks: put the origin at the centre of the square it sits on. Empty = Octagon Parks objects are used on squares.",
    "Octagon Parks: put the origin at the centre of the octagon it sits on.",
    "Elevators: put the origin at the centre of the transport octagon. One copy holds all eight shafts.",
    "Wall exteriors must be facing west, and wall lengths must be facing North/South.",
    "Heights come from each object's lowest point, so origin height does not matter.",
)


# The full description, shown in the add-on's Preferences panel. Blender's add-on list cuts
# the one-line description short, so the whole text is drawn here instead.
DESCRIPTION_PARAGRAPHS = (
    "Add your own custom-built cellular hexagons and other object data to the algorithm. "
    "All of this is intended to be built in real life, this is the future of urban living! "
    "Open source and ready to be copied and re-hosted by anyone interested in building "
    "and leading the future!",
)


FOOTPRINT_INFO = (
    "Footprint: the mesh this arcology is built on. Each arcology in the list has its own footprint, shape "
    "and settings, so you can build an octagon and a hexagon side by side, or several hexagons with "
    "different settings. Press + to add one and - to remove the selected one.\n"
    "Shape: Hexagon or Octagon for this arcology.\n"
    "Roof plane: Sets the Z axis height above the arcology. I suggest using a subdivided sculpted plane."
)


INFO_WRAP_CHARS = 72          # Blender labels do not wrap, so popup text is wrapped here, to fit the popup


def _wrap_info(lines, width=INFO_WRAP_CHARS):
    """Wrap each line of popup text to the popup's width. Blank lines stay blank, and a wrapped line continues indented."""
    out = []
    for line in lines:
        if not line.strip():
            out.append("")
            continue
        out.extend(textwrap.wrap(line, width, subsequent_indent="    ") or [""])
    return out
