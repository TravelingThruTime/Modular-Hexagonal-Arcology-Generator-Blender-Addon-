"""Rebuilds the selected arcology and the live-update timer."""


import time
import traceback

import bpy

from .constants import SHAPE_FLOWER, SHAPE_OCT, SHAPE_PEN, SHAPE_RECT
from .flower import build_flower_city
from .hexagon import build_city
from .octagon import build_octagon_city
from .output import active_entry, arcology_role, child_collection, clear_arcology, drop_prefabs, ensure_uids, output_collection, prune_output_collections, write_output
from .penrose import build_penrose_city
from .records import write_copies
from .rectangle import build_rect_city


_pending = False                  # a live rebuild is waiting in the timer queue


def rebuild(scene, entry):
    """Clears and rebuilds one arcology: the selected one. The other arcologies in the list are not touched."""
    shared = scene.arcology_shared               # also holds the shared Live switch and status line
    t0 = time.perf_counter()
    if entry is None:
        shared.status = "Add an arcology with +"
        return None
    ensure_uids(scene)
    number = next((i + 1 for i, e in enumerate(scene.arcologies) if e.uid == entry.uid), 0)
    clear_arcology(entry)
    fp = entry.footprint
    if fp is None or fp.type != 'MESH':
        shared.status = f"Arcology {number}: no footprint. Pick a mesh for it first"
        return None
    is_rect = entry.shape == SHAPE_RECT
    is_oct = entry.shape == SHAPE_OCT
    props = {SHAPE_PEN: entry.penrose_settings, SHAPE_RECT: entry.rect_settings,
             SHAPE_OCT: entry.oct_settings, SHAPE_FLOWER: entry.flower_settings}.get(entry.shape, entry.hex_settings)
    target = child_collection(output_collection(scene), arcology_role(entry))
    if entry.shape == SHAPE_FLOWER:
        buckets, stats, records = build_flower_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_cell_count = stats["cells_counted"]
        props.last_part_count = stats["parts"]
        footprint, unit = stats["footprint"], "junctions"
    elif entry.shape == SHAPE_PEN:
        buckets, stats, records = build_penrose_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_thick = stats["thick"]
        props.last_cell_count = stats["cells_counted"]
        footprint, unit = stats["footprint"], "tiles"
    elif is_rect:
        buckets, stats, records = build_rect_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_cell_count = stats["cells_counted"]
        footprint, unit = stats["footprint"], "rectangles"
    elif is_oct:
        buckets, stats, records = build_octagon_city(props, fp, number - 1)
        props.last_footprint = stats["footprint"]
        props.last_cell_count = stats["octagon_cells"]
        props.last_square_count = stats["square_cells"]
        footprint, unit = stats["footprint"], "octagons"
    else:
        buckets, stats, records = build_city(props, fp, number - 1)
        props.last_footprint = stats["hexes"]
        props.last_hex_count = stats["cells_counted"]
        footprint, unit = stats["hexes"], "hexes"
    n_objects = write_output(target, buckets) + write_copies(target, records)
    drop_prefabs(records)
    prune_output_collections()
    dt = (time.perf_counter() - t0) * 1000.0
    shared.status = (f"Arcology {number}: {footprint} {unit} across the footprint, top layer {stats['top_layer']}, "
                     f"{stats['towers']} towers, {stats['walls']} walls, {stats['parks']} park cells, "
                     f"{stats['trees']} trees, {stats['benches']} benches, {n_objects} objects, {dt:.0f} ms")
    return None


def _timer_fire():
    global _pending
    _pending = False
    scene = bpy.context.scene
    try:
        rebuild(scene, active_entry(scene))    # live: only the arcology the panel shows
    except Exception as exc:                       # keep the add-on alive; show the error in the panel
        traceback.print_exc()
        try:
            scene.arcology_shared.status = f"Error: {exc}"
        except Exception:
            pass
    return None                                    # run once


def schedule_rebuild(delay=0.15):
    """Waits a moment after the last slider change, then rebuilds once."""
    global _pending
    if _pending:
        return
    _pending = True
    bpy.app.timers.register(_timer_fire, first_interval=delay)


def _auto_rebuild():
    """Schedules a live rebuild while the Live switch is on. Any arcology's change rebuilds them all."""
    scene = bpy.context.scene
    if scene is not None and scene.arcology_shared.live:
        schedule_rebuild()


def _on_change(self, context):
    _auto_rebuild()


def _on_oct_change(self, context):
    _auto_rebuild()


def cancel_pending():
    """Forgets a waiting live rebuild. Used when the add-on unregisters."""
    global _pending
    _pending = False
