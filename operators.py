"""Operators for the arcology list, Build, Clear and the info popups."""


import random

from bpy.props import StringProperty
from bpy.types import Operator

from .constants import SHAPE_FLOWER, SHAPE_OCT, SHAPE_PEN, SHAPE_RECT
from .geometry import default_footprint
from .info import COLLECTION_INFO, FLOWER_COLLECTION_INFO, OCT_COLLECTION_INFO, PENROSE_COLLECTION_INFO, RECT_COLLECTION_INFO, _wrap_info
from .output import active_entry, clear_arcology, ensure_uids
from .rebuild import _auto_rebuild, rebuild


class ARCOLOGY_OT_rebuild(Operator):
    bl_idname = "arcology.rebuild"
    bl_label = "Build"
    bl_description = "Rebuild the selected arcology. It takes the shape on the tabs"

    def execute(self, context):
        scene = context.scene
        entry = active_entry(scene)
        if entry is not None and entry.shape != scene.arcology_shape:
            entry.shape = scene.arcology_shape     # only Build changes an arcology's shape
        try:
            rebuild(scene, entry)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class ARCOLOGY_OT_clear(Operator):
    bl_idname = "arcology.clear"
    bl_label = "Clear"
    bl_description = "Remove the selected arcology's city. The others are left as they are"

    def execute(self, context):
        entry = active_entry(context.scene)
        if entry is not None:
            clear_arcology(entry)
        context.scene.arcology_shared.status = "Cleared" if entry is not None else "Add an arcology with +"
        return {'FINISHED'}


class ARCOLOGY_OT_reseed(Operator):
    bl_idname = "arcology.reseed"
    bl_label = "New seed"
    bl_description = "Pick a random seed for the selected arcology"

    def execute(self, context):
        entry = active_entry(context.scene)
        if entry is None:
            return {'CANCELLED'}
        props = {SHAPE_PEN: entry.penrose_settings, SHAPE_OCT: entry.oct_settings,
                 SHAPE_RECT: entry.rect_settings, SHAPE_FLOWER: entry.flower_settings}.get(entry.shape, entry.hex_settings)
        props.seed = random.randint(0, 999999)
        return {'FINISHED'}


class ARCOLOGY_OT_add(Operator):
    bl_idname = "arcology.add"
    bl_label = "Add arcology"
    bl_description = "Add an arcology. It starts on the active mesh, or on 'Ark Polygon' or '30 Acre Ark'"

    def execute(self, context):
        scene = context.scene
        entry = scene.arcologies.add()
        ensure_uids(scene)
        entry.footprint = default_footprint(context)
        scene.arcology_index = len(scene.arcologies) - 1
        _auto_rebuild()
        return {'FINISHED'}


class ARCOLOGY_OT_remove(Operator):
    bl_idname = "arcology.remove"
    bl_label = "Remove arcology"
    bl_description = "Remove the selected arcology and its city. The others are left as they are"

    def execute(self, context):
        scene = context.scene
        if not scene.arcologies:
            return {'CANCELLED'}
        index = min(max(scene.arcology_index, 0), len(scene.arcologies) - 1)
        clear_arcology(scene.arcologies[index])
        scene.arcologies.remove(index)
        scene.arcology_index = max(0, min(index, len(scene.arcologies) - 1))
        scene.arcology_shared.status = (f"Removed arcology {index + 1}" if scene.arcologies
                                        else "Add an arcology with +")
        return {'FINISHED'}


class ARCOLOGY_OT_info(Operator):
    """Shows a short popup with the text it was given"""
    bl_idname = "arcology.info"
    bl_label = "Info"
    bl_options = {'INTERNAL'}
    text: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        col = self.layout.column(align=True)
        for line in _wrap_info(self.text.split("\n")):
            col.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


class ARCOLOGY_OT_collection_info(Operator):
    bl_idname = "arcology.collection_info"
    bl_label = "Collection origins"
    bl_description = "How to set the origin of the objects you pick"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        col = self.layout.column(align=True)
        entry = active_entry(context.scene)
        is_oct = entry is not None and entry.shape == SHAPE_OCT
        if entry is not None and entry.shape == SHAPE_FLOWER:
            lines = FLOWER_COLLECTION_INFO
        elif entry is not None and entry.shape == SHAPE_PEN:
            lines = PENROSE_COLLECTION_INFO
        elif entry is not None and entry.shape == SHAPE_RECT:
            lines = RECT_COLLECTION_INFO
        else:
            lines = OCT_COLLECTION_INFO if is_oct else COLLECTION_INFO
        for line in _wrap_info(lines):
            col.label(text=line)

    def execute(self, context):
        return {'FINISHED'}
