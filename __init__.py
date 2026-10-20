"""
Hex Arcology Proxy Generator (Blender add-on)

Builds the hex arcology from ultra-low-poly proxy shapes (hex prisms, boxes and pyramids) instead of
your detailed modules, so it can rebuild live while you drag the sliders in the sidebar.

Install: Edit > Preferences > Add-ons > Install from Disk, pick the add-on's zip file, tick the add-on on.
Use: 3D Viewport > N sidebar > "Arcology" tab. The list holds one arcology per footprint: press + to add
one, then pick its footprint mesh and its shape (Hexagon or Octagon). Each arcology has its own settings,
so you can mix shapes and values. Build replaces the city in the scene, and Clear removes it.

Every change to a slider rebuilds the city a moment later (turn "Live" off to rebuild by hand).
Output goes to the "Arcology Proxy" collection, or to the collections you pick in the Collections box.
Your own objects are never changed or deleted.

Footprint: pick a mesh in the Footprint field, or name a mesh "Ark Polygon" or "30 Acre Ark".
Roof Plane: optional. Pick a mesh; its height above each hexagon centre sets that hex's layer count,
so it can be sculpted into slopes. Without one, every hex gets the layer cap.
"""


bl_info = {
    "name": "Modular Arcology Generator",
    "author": "Traveling",
    "version": (1, 5, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Arcology",
    "description": "Generates the hex arcology from ultra-low-poly proxies, live, with sliders.",
    "category": "Add Mesh",
}


import bpy

from bpy.props import EnumProperty, IntProperty, PointerProperty

from .constants import SHAPE_FLOWER, SHAPE_HEX, SHAPE_OCT, SHAPE_PEN, SHAPE_RECT
from .header import _install_arcology_header, _load_arcology_icon, _release_arcology_icon, _uninstall_arcology_header
from .operators import ARCOLOGY_OT_add, ARCOLOGY_OT_clear, ARCOLOGY_OT_collection_info, ARCOLOGY_OT_info, ARCOLOGY_OT_rebuild, ARCOLOGY_OT_remove, ARCOLOGY_OT_reseed
from .panels import ARCOLOGY_PT_panel, ARCOLOGY_UL_list
from .preferences import ARCOLOGY_Preferences
from .properties import ArcologyEntry, ArcologyProps, ArcologyShared, FlowerProps, OctagonProps, PenroseProps, RectProps
from .rebuild import _timer_fire, cancel_pending
from .sidebar import ARCOLOGY_OT_open_sidebar


CLASSES = (
    ArcologyProps,
    OctagonProps,
    RectProps,
    PenroseProps,
    FlowerProps,
    ArcologyShared,
    ArcologyEntry,
    ARCOLOGY_UL_list,
    ARCOLOGY_OT_rebuild,
    ARCOLOGY_OT_clear,
    ARCOLOGY_OT_reseed,
    ARCOLOGY_OT_add,
    ARCOLOGY_OT_remove,
    ARCOLOGY_OT_collection_info,
    ARCOLOGY_OT_info,
    ARCOLOGY_PT_panel,
    ARCOLOGY_OT_open_sidebar,
    ARCOLOGY_Preferences,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _load_arcology_icon()
    _install_arcology_header()
    bpy.types.Scene.arcology_shared = PointerProperty(type=ArcologyShared)
    bpy.types.Scene.arcologies = bpy.props.CollectionProperty(type=ArcologyEntry)
    bpy.types.Scene.arcology_index = IntProperty(
        name="Arcology", default=0, min=0,
        description="The arcology the panel is showing")
    bpy.types.Scene.arcology_shape = EnumProperty(
        name="Shape",
        items=(
            (SHAPE_HEX, "Hexagon", "The selected arcology is a hexagon city"),
            (SHAPE_OCT, "Octagon", "The selected arcology is an octagon city, with square gaps"),
            (SHAPE_RECT, "Rectangle", "A city of touching rectangles, with a 12 ft hallway down each road rectangle"),
            (SHAPE_PEN, "Penrose", "A city of Penrose tiles: thick tiles are towers, thin tiles are roads"),
            (SHAPE_FLOWER, "Flower", "A city of Flower of Life circles: concrete outlines, three elevators at every junction"),
        ),
        default=SHAPE_HEX,
        description="The shape Build gives the selected arcology")


def unregister():
    _uninstall_arcology_header()
    _release_arcology_icon()
    try:
        bpy.app.timers.unregister(_timer_fire)
    except Exception:
        pass
    cancel_pending()
    del bpy.types.Scene.arcology_shape
    del bpy.types.Scene.arcology_index
    del bpy.types.Scene.arcologies
    del bpy.types.Scene.arcology_shared
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
