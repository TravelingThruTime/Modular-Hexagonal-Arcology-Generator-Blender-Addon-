# Modular Hexagonal Arcology Generator

A Blender add-on that generates a modular hexagonal arcology from ultra-low-poly proxy shapes. Every slider change rebuilds the city live, and you can replace any of the proxies with your own objects, from your own collections.

## Features

### Generation

- Builds the arcology on any footprint mesh. Meshes named `Ark Polygon` or `30 Acre Ark` are picked up automatically.
- Optional roof plane: a mesh whose height above each hex centre sets that hex's number of layers, so the arcology can be sculpted into slopes. Without one, every hex uses the layer cap.
- Hexagonal towers, road hexes, road trapezoids, park hexes, transport hexes with elevators, walls on exposed sides, and columns at every hex centre and corner.
- Connected winding paths across the parks, with smooth bends and flat ribbon geometry to keep the polygon count low.
- Trees and benches in the park hexes, with separate per-hex counts.
- A seed slider that repeats the same layout, and a button to pick a new one.
- Live update, or turn it off and rebuild by hand.

### Sliders

- **Footprint and roof:** footprint mesh and optional roof plane.
- **Size:** hex edge length, level height and roof height, each in feet or metres with a toggle.
- **Height:** max layers, tower min and max layers.
- **Roads and seed:** road chance and seed.
- **Show:** toggle towers, roads, trapezoids, parks, elevators, walls, trees, benches, columns and paths.
- **Park density**, trees per park hex (up to 30), and benches per park hex (up to 30).

### Stats

A Stats box at the bottom of the panel estimates the build from the hexagon cell count:

- Hexagon cells across every layer, excluding transport hexes and the top parks layer
- Percent residential, residents per hexagon, and cost per hexagon ($M)
- Estimated population and estimated cost
- Estimated land area in acres, population per square mile, and density compared to Manhattan
- Persons per household, mortgage rate, cost per household and an estimated 30-year monthly mortgage payment

### Output

Output goes to a collection called **Arcology Proxy**, split into **Layer N** sub-collections, with walls in **Walls.Polygon**. Your own objects are never changed or deleted. The Clear button removes only what the add-on made.

## Using your own objects

Each collection in the **Collections** box accepts any Blender collection. When a collection is picked, the add-on fills its slots with copies of your objects, chosen at random for each slot. The Seed slider makes those choices repeatable. When the picker is empty, the add-on generates its own proxy shapes instead, so you can use any part of the system on its own.

The Collections box has an info button that explains how to set the origin of your objects.

| Collection | What it replaces | Origin and orientation |
| --- | --- | --- |
| **Hexagons** | Tower modules, one per level of each tower | No origin needed. Uses the object's bounding box. Each copy is turned in 60° steps and scaled to fill the hex, then stretched to one level tall. |
| **Road Trapezoids** | The trapezoid on each road hex, one per level | Origin at the centre of the hex cell it sits in. Each copy is turned so its flat side faces the road, then stretched to one level tall. |
| **Parks** | Park pieces on the roofs | Origin at the centre of the park's first hex cell. Each piece is placed at a random free spot and turned at random. A piece about two hexes or more in area covers three cells. |
| **Walls** | Wall panels on each exposed side of a hex | Exterior faces **west** and lengths run **north/south** in your source. Each copy is turned to face out of the hex, stretched to one hex edge long, and one level tall. |
| **Roof** | The roof slab above each module | No origin needed. Uses the bounding box. Each copy fills its hex and is stretched to the roof height. |
| **Elevators** | All six elevator shafts of one transport hex, on each level | Origin at the centre of the transport hex cell. One copy replaces the whole junction, keeping its own orientation and stretched to 36 ft per level. |

Heights come from the lowest point of each object, so the origin's height does not matter.

## Tips

- Keep each object's mesh simple. The add-on copies your objects into every slot, so the polygon count grows with the layer count.
- Changing a collection rebuilds the city, so you can swap objects in your collection and see the result right away.
