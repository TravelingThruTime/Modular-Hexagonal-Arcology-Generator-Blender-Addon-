"""The Arcology button in the Properties editor header."""


import bpy

from .sidebar import _ICON_PNG_B64


_icon_coll = None


_ORIG_HEADER_DRAW = None


def _load_arcology_icon():
    """Load the embedded hexagon PNG as a custom icon."""
    global _icon_coll
    import base64
    import os
    import tempfile
    import bpy.utils.previews
    if _icon_coll is None:
        _icon_coll = bpy.utils.previews.new()
    if "hex" in _icon_coll:
        return
    try:
        folder = bpy.app.tempdir or tempfile.gettempdir()
        path = os.path.join(folder, "arcology_hex_icon.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(_ICON_PNG_B64))
        _icon_coll.load("hex", path, 'IMAGE')
    except Exception as exc:
        print("Arcology: could not load the hexagon icon:", exc)


def _release_arcology_icon():
    global _icon_coll
    import bpy.utils.previews
    if _icon_coll is not None:
        bpy.utils.previews.remove(_icon_coll)
        _icon_coll = None


def _draw_arcology_header_button(layout):
    """The Arcology button, drawn just left of the Properties search field."""
    if _icon_coll is not None and "hex" in _icon_coll:
        layout.operator("arcology.open_sidebar", text="",
                        icon_value=_icon_coll["hex"].icon_id)
    else:
        layout.operator("arcology.open_sidebar", text="", icon='VIEW3D')


class _ArcologyHeaderLayout:
    """Wraps the header layout and inserts the button right before the search field."""

    def __init__(self, layout):
        self._layout = layout
        self._done = False

    def __getattr__(self, name):
        return getattr(self._layout, name)

    def prop(self, data, property, *args, **kwargs):
        if not self._done and property == "search_filter":
            self._done = True
            _draw_arcology_header_button(self._layout)
        return self._layout.prop(data, property, *args, **kwargs)


class _ArcologyHeaderSelf:
    def __init__(self, owner, layout):
        self._owner = owner
        self.layout = layout

    def __getattr__(self, name):
        return getattr(self._owner, name)


def _arcology_header_draw(self, context):
    wrapped = _ArcologyHeaderSelf(self, _ArcologyHeaderLayout(self.layout))
    _ORIG_HEADER_DRAW(wrapped, context)


def _install_arcology_header():
    global _ORIG_HEADER_DRAW
    if _ORIG_HEADER_DRAW is None:
        _ORIG_HEADER_DRAW = bpy.types.PROPERTIES_HT_header.draw
        bpy.types.PROPERTIES_HT_header.draw = _arcology_header_draw


def _uninstall_arcology_header():
    global _ORIG_HEADER_DRAW
    if _ORIG_HEADER_DRAW is not None:
        bpy.types.PROPERTIES_HT_header.draw = _ORIG_HEADER_DRAW
        _ORIG_HEADER_DRAW = None
