"""The add-on's Preferences panel."""


import textwrap

import bpy

from .info import DESCRIPTION_PARAGRAPHS


class ARCOLOGY_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        for paragraph in DESCRIPTION_PARAGRAPHS:
            for line in textwrap.wrap(paragraph, 90):
                layout.label(text=line)
            layout.label(text="")
        layout.label(text="Proudly made by Claude Haiku 5.5")
