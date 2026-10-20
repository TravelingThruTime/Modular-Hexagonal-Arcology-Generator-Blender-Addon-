"""Property groups for each shape and for the arcology list."""


import math

import bpy

from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .constants import FT, SHAPE_FLOWER, SHAPE_HEX, SHAPE_OCT, SHAPE_PEN, SHAPE_RECT
from .flower import FLOWER_SEGMENTS
from .penrose import PEN_MAX_EDGE_M, PEN_MIN_EDGE_M
from .rebuild import _auto_rebuild, _on_change, _on_oct_change


# ---------------------------------------------------------------------------
# Properties, operators and panel
# ---------------------------------------------------------------------------
def _is_mesh(self, obj):
    return obj.type == 'MESH'


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
    coll_road: PointerProperty(
        name="Roads", type=bpy.types.Collection, update=_on_change,
        description="Objects in this collection are copied onto the road hexes as road strips, at road height. "
                    "The road runs along the object's X axis. Empty = generated road strips")
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
    # The identifier keeps its old name, so the picks in saved files still load. The label is Quarter Octagons.
    coll_road_octagons: PointerProperty(
        name="Quarter Octagons", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the road and junction octagons, four to a cell, one in "
                    "each quarter, at road height. Each object is one quarter of the octagon with its origin at the centre, "
                    "filling the quarter between +X and +Y. Empty = generated quarters")
    coll_road: PointerProperty(
        name="Roads", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the roads as road strips, at road height, one from the "
                    "centre of each octagon to its edge. The road runs along the object's X axis. Empty = generated strips")
    coll_squares: PointerProperty(
        name="Squares", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied to fill the square towers. Empty = generated squares")
    coll_parks: PointerProperty(
        name="Octagon Parks", type=bpy.types.Collection, update=_on_oct_change,
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
    coll_square_parks: PointerProperty(
        name="Square Parks", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied onto the square roofs as parks. Empty = the Octagon Parks objects are used")
    coll_elevators: PointerProperty(
        name="Elevators", type=bpy.types.Collection, update=_on_oct_change,
        description="Objects in this collection are copied into the transport octagons. Empty = generated shafts")
    trees_per_cell: IntProperty(name="Trees per park octagon", default=5, min=0, max=30, update=_on_oct_change,
                                description="Random trees placed in each park octagon")
    benches_per_cell: IntProperty(name="Benches per park octagon", default=2, min=0, max=30, update=_on_oct_change,
                                  description="Random benches placed in each park octagon")
    last_cell_count: IntProperty(name="Octagon cells", default=0,
                                 description="Octagon cells in the last build, counted at every level, excluding junctions and the park layer (set by Build)")
    last_square_count: IntProperty(name="Square cells", default=0,
                                   description="Square cells in the last build, counted at every level, excluding the park layer (set by Build)")
    last_footprint: IntProperty(name="Footprint octagons", default=0,
                                description="Octagons across the footprint in the last build (set by Build)")
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of octagon cells that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per octagon", default=20, min=0,
                                description="Residents in each residential octagon cell")
    stat_residents_square: IntProperty(name="Residents per square", default=20, min=0,
                                       description="Residents in each residential square cell")
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
    coll_road: PointerProperty(
        name="Roads", type=bpy.types.Collection, update=_on_rect_change,
        description="Objects in this collection are copied onto the roads as road strips, at road height, one from the "
                    "centre of each rectangle to its edge. The road runs along the object's X axis. Empty = generated strips")
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
    coll_road: PointerProperty(
        name="Roads", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto the thin tiles as roads, stretched along the long "
                    "diagonal to the road width. Empty = generated roads")
    coll_thin_rhombs: PointerProperty(
        name="Thin Half Rhombs", type=bpy.types.Collection, update=_on_pen_change,
        description="Objects in this collection are copied onto each side of the road on the thin tiles, one per side "
                    "on each level of the road. Empty = nothing there")
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
    last_cell_count: IntProperty(name="Thick rhombi cells", default=0,
                                 description="Thick rhombus cells in the last build, counted at every level (set by Build)")
    last_footprint: IntProperty(name="Footprint tiles", default=0,
                                description="Tiles across the footprint in the last build (set by Build)")
    last_thick: IntProperty(name="Thick tiles", default=0,
                            description="Thick tiles in the last build (set by Build)")
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of thick rhombus cells that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per thick rhombi", default=20, min=0,
                                description="Residents in each residential thick rhombus cell. Thin rhombi hold no residents")
    stat_cost: FloatProperty(name="Cost Per Thick Rhombus ($M)", default=2.0, min=0.0, precision=2,
                             description="Build cost of one thick rhombus cell, in millions of dollars")
    stat_household: FloatProperty(name="Persons Per Household", default=2.6, min=0.1, max=20.0, precision=1,
                                  description="Average number of people in each household")
    stat_mortgage_rate: FloatProperty(name="Mortgage Rate", default=7.0, min=0.0, max=30.0, precision=2,
                                      subtype='PERCENTAGE',
                                      description="Annual mortgage interest rate. The loan is a 30-year fixed mortgage")


def _on_flower_change(self, context):
    _auto_rebuild()


class FlowerProps(PropertyGroup):
    """Settings for the Flower city. Kept apart from the other shapes' settings, so each arcology has its own values."""
    roof_plane: PointerProperty(
        name="Roof Plane", type=bpy.types.Object, poll=_is_mesh, update=_on_flower_change,
        description="Optional. Its height above each junction sets that junction's layers, and the top layer meets it. "
                    "Without one, every junction gets the layer cap")
    seed: IntProperty(name="Seed", default=42, min=0, max=999999, update=_on_flower_change,
                      description="Changes the turn of each copied object, and the trees and benches")
    circle_radius_ft: FloatProperty(
        name="Circle Radius (ft)", default=240.0, min=10.0, max=1000.0, update=_on_flower_change,
        description="Radius of each circle. It is the road length too, and neighbouring junctions are one radius apart")
    circle_vertexes: IntProperty(name="Circle Vertexes", default=FLOWER_SEGMENTS, min=8, max=256, update=_on_flower_change,
                                 description="Corners in each circle outline. Shown in the Size box below Circle Radius")
    road_width_ft: FloatProperty(name="Road width (ft)", default=12.0, min=2.0, max=60.0, update=_on_flower_change,
                                 description="Width of the road that runs through Flower part 1")
    wall_width_ft: FloatProperty(name="Wall width (ft)", default=1.0, min=0.1, max=10.0, update=_on_flower_change,
                                 description="Thickness of the concrete wall that follows each circle outline")
    elevator_edge_ft: FloatProperty(name="Elevator edge (ft)", default=8.0, min=2.0, max=100.0,
                                    update=_on_flower_change,
                                    description="Edge of each hexagonal elevator. Three sit in the corners of each Junction Hexagon")
    tree_scale: FloatProperty(
        name="Tree Scale", default=1.0, min=0.0, max=3.0, update=_on_flower_change,
        description="Scales every tree, its canopy and its trunk. 0 hides the trees, 3 is three times the size")
    park_density: FloatProperty(name="Park density", default=0.8, min=0.0, max=1.0, subtype='FACTOR',
                                update=_on_flower_change,
                                description="Chance that a junction's top layer gets a park")
    use_parts: BoolProperty(name="Flower Parts", description="Place the Flower Parts on the junctions, one level at a time", default=True, update=_on_flower_change)
    show_parks: BoolProperty(name="Parks", default=True, update=_on_flower_change)
    show_trees: BoolProperty(name="Trees", default=True, update=_on_flower_change)
    show_benches: BoolProperty(name="Benches", default=True, update=_on_flower_change)
    trees_per_cell: IntProperty(name="Trees per park hex", default=5, min=0, max=30, update=_on_flower_change,
                                description="Random trees placed in each park")
    benches_per_cell: IntProperty(name="Benches per park hex", default=2, min=0, max=30, update=_on_flower_change,
                                  description="Random benches placed in each park")
    coll_part_1: PointerProperty(name="Flower Part 1", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 1 (the lens), fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_2: PointerProperty(name="Flower Part 2", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 2, fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_3: PointerProperty(name="Flower Part 3", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 3, fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_4: PointerProperty(name="Flower Part 4", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 4, fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_5: PointerProperty(name="Flower Part 5", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 5, fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_6: PointerProperty(name="Flower Part 6", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 6, fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_7: PointerProperty(name="Flower Part 7", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 7 (the triangle), fitted to the circle radius and the roof height. Empty = the generated part")
    coll_part_8: PointerProperty(name="Flower Part 8", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied for Flower Part 8, fitted to the circle radius and the roof height. Empty = the generated part")
    coll_roads: PointerProperty(name="Roads", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied as the road through Flower Part 1, stretched to the road length and width. Empty = the generated road")
    coll_elevators: PointerProperty(
        name="Elevators", type=bpy.types.Collection, update=_on_flower_change,
        description="Objects in this collection are copied into the hexagonal elevators, three at each junction. Empty = generated shafts")
    level_ft: FloatProperty(name="Level height (ft)", default=30.0, min=8.0, max=60.0, update=_on_flower_change)
    roof_ft: FloatProperty(name="Roof height (ft)", default=6.0, min=0.5, max=30.0, update=_on_flower_change,
                           description="Height of the roof between two layers")
    max_layers: IntProperty(name="Max layers", default=12, min=1, max=24, update=_on_flower_change,
                            description="The most layers any junction gets")
    last_footprint: IntProperty(name="Junctions", default=0,
                                description="Junctions (circle centres) in the last build (set by Build)")
    last_cell_count: IntProperty(name="Circles", default=0,
                                 description="Circles in the last build (set by Build)")
    last_part_count: IntProperty(name="Flower Parts", default=0,
                                 description="Flower Part structures in the last build, one per part per layer (set by Build)")
    # Stats box. These only change the sums shown, so moving them does not rebuild the city.
    stat_residential: FloatProperty(name="Percent Residential", default=0.6, min=0.0, max=1.0, precision=2,
                                    description="Share of Flower Part structures that are residential, from 0 to 1")
    stat_residents: IntProperty(name="Residents per part", default=20, min=0,
                                description="Residents in each residential Flower Part structure")
    stat_cost: FloatProperty(name="Cost Per Flower Part ($M)", default=2.0, min=0.0, precision=2,
                             description="Build cost of one Flower Part structure, in millions of dollars")
    stat_household: FloatProperty(name="Persons Per Household", default=2.6, min=0.1, max=20.0, precision=1,
                                  description="Average persons in a household")
    stat_mortgage_rate: FloatProperty(name="Mortgage Rate", default=7.0, min=0.0, max=30.0, precision=2,
                                      description="Annual mortgage rate, in percent, for the monthly payment")


class ArcologyShared(PropertyGroup):
    """Settings every arcology shares: the Live switch and the status line."""
    live: BoolProperty(name="Live update", default=True,
                       description="Rebuild a moment after every change")
    status: StringProperty(name="Status", default="Not built yet")


SHAPE_LABEL = {SHAPE_HEX: "Hexagon", SHAPE_OCT: "Octagon", SHAPE_RECT: "Rectangle", SHAPE_PEN: "Penrose",
               SHAPE_FLOWER: "Flower"}


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
            (SHAPE_FLOWER, "Flower", "A city of Flower of Life circles: concrete outlines, three elevators at every junction"),
        ),
        default=SHAPE_HEX, update=_on_change,
        description="The city this footprint gets")
    uid: IntProperty(default=0, min=0,
                     description="Identifies this arcology's output, so it stays put when the list changes")
    hex_settings: PointerProperty(type=ArcologyProps)
    oct_settings: PointerProperty(type=OctagonProps)
    rect_settings: PointerProperty(type=RectProps)
    penrose_settings: PointerProperty(type=PenroseProps)
    flower_settings: PointerProperty(type=FlowerProps)
