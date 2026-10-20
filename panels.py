"""Sidebar panel drawing for each shape, and the arcology list."""


import math

import bpy

from bpy.types import Panel

from .constants import MANHATTAN_DENSITY, SHAPE_FLOWER, SHAPE_HEX, SHAPE_OCT, SHAPE_PEN, SQRT2
from .info import FOOTPRINT_INFO
from .output import active_entry
from .properties import SHAPE_LABEL


def monthly_mortgage_payment(principal, annual_rate_percent, years=30):
    """Standard fixed-rate mortgage payment per month."""
    n = years * 12
    r = annual_rate_percent / 100.0 / 12.0
    if principal <= 0:
        return 0.0
    if r <= 0:
        return principal / n
    return principal * r / (1.0 - (1.0 + r) ** (-n))


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
    box.prop(q, "coll_road")
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
    box.prop(q, "coll_road")
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
    if q.last_footprint:
        box.label(text=f"Cells, thick rhombi (every layer, excluding parks): {cells:,}")
    else:
        box.label(text="Cells, thick rhombi (every layer, excluding parks): build to count")
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


def _draw_flower(layout, entry):
    """The Flower arcology: its footprint and roof plane, its pickers, and its size, height, park and seed settings."""
    q = entry.flower_settings
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
    box.prop(q, "coll_elevators")
    for n in range(1, 9):
        box.prop(q, f"coll_part_{n}")
    box.prop(q, "coll_roads")

    box = layout.box()
    box.label(text="Size")
    box.prop(q, "circle_radius_ft")
    box.prop(q, "circle_vertexes")
    box.prop(q, "road_width_ft")
    box.prop(q, "wall_width_ft")
    box.prop(q, "elevator_edge_ft")

    box = layout.box()
    box.label(text="Height")
    box.prop(q, "level_ft")
    box.prop(q, "roof_ft")
    box.prop(q, "max_layers")

    box = layout.box()
    box.label(text="Flower Parts")
    box.prop(q, "use_parts")

    box = layout.box()
    box.label(text="Parks, trees and benches")
    box.prop(q, "show_parks")
    box.prop(q, "park_density")
    box.prop(q, "show_trees")
    box.prop(q, "tree_scale")
    box.prop(q, "trees_per_cell")
    box.prop(q, "show_benches")
    box.prop(q, "benches_per_cell")

    box = layout.box()
    box.label(text="Seed")
    box.prop(q, "seed")

    box = layout.box()
    box.label(text="Stats")
    parts = q.last_part_count
    if parts:
        box.label(text=f"Flower Part structures (one per part per layer): {parts:,}")
    else:
        box.label(text="Flower Part structures (one per part per layer): build to count")
    box.prop(q, "stat_residential")
    box.prop(q, "stat_residents")
    box.prop(q, "stat_cost")
    population = parts * q.stat_residential * q.stat_residents
    cost_m = parts * q.stat_cost
    cost_text = f"${cost_m:,.1f} M" if cost_m < 1000 else f"${cost_m / 1000:,.2f} B"
    box.label(text=f"Estimated Population: {population:,.0f}")
    box.label(text=f"Estimated Cost: {cost_text}")
    if q.last_footprint:
        # Land area: each junction owns one lattice cell, a rhombus of two triangles with sides one radius apart
        radius_m = q.circle_radius_ft * 0.3048
        land_m2 = q.last_footprint * radius_m * radius_m * math.sqrt(3.0) / 2.0
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
    households_per_part = q.stat_residents / q.stat_household if q.stat_household > 0 else 0.0
    cost_per_household = (q.stat_cost * 1_000_000.0) / households_per_part if households_per_part > 0 else 0.0
    payment = monthly_mortgage_payment(cost_per_household, q.stat_mortgage_rate)
    box.label(text=f"Cost per household: ${cost_per_household:,.0f}")
    box.label(text=f"Estimated Mortgage Payment: ${payment:,.0f} / month")


class ARCOLOGY_UL_list(bpy.types.UIList):
    """One row per arcology: its number, its footprint and its shape."""
    bl_idname = "ARCOLOGY_UL_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        fp = item.footprint
        row = layout.row()
        row.label(text=f"{index + 1}. {fp.name if fp is not None else 'No footprint'}", icon='MESH_DATA')
        row.label(text=SHAPE_LABEL.get(item.shape, item.shape))


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
    box.prop(q, "coll_road")
    box.prop(q, "coll_squares")
    box.prop(q, "coll_square_roofs")
    box.prop(q, "coll_square_parks")
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
    built = q.last_footprint > 0
    oct_cells, sq_cells = q.last_cell_count, q.last_square_count
    if built:
        box.label(text=f"Octagon cells (every layer, excluding junctions and parks): {oct_cells:,}")
        box.label(text=f"Square cells (every layer, excluding parks): {sq_cells:,}")
    else:
        box.label(text="Octagon cells (every layer, excluding junctions and parks): build to count")
        box.label(text="Square cells (every layer, excluding parks): build to count")
    box.prop(q, "stat_residential")
    box.prop(q, "stat_residents")
    box.prop(q, "stat_residents_square")
    box.prop(q, "stat_cost")
    cells = oct_cells + sq_cells
    residents_all = oct_cells * q.stat_residents + sq_cells * q.stat_residents_square
    population = q.stat_residential * residents_all
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
    # Cost per household: the cell cost divided by (residents per cell / persons per household). With squares and
    # octagons, the residents per cell are averaged over every counted cell.
    residents_per_cell = residents_all / cells if cells else q.stat_residents
    households_per_cell = residents_per_cell / q.stat_household if q.stat_household > 0 else 0.0
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
    box.prop(p, "coll_road")
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
        current = active_entry(scene)
        if current is not None and current.shape == SHAPE_FLOWER:
            note = layout.box()
            note.label(text="Flower Arcs Do Not Generate Perfectly", icon='ERROR')
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
        elif entry.shape == SHAPE_FLOWER:
            _draw_flower(layout, entry)
        else:
            _draw_rect(layout, entry)
