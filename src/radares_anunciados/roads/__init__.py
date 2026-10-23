"""Road geometry for the published feed: ``radares roads`` writes roads.json.

For every stretch it gives the road between the two published ends and the last
1.5 km of road leading into each end; for every point radar, the last 1.5 km of
road leading to it in the direction it watches. It also adds a travel bearing to
the point radars whose direction the publishers' data and the map agree on.

It reads the Geofabrik extract of Spain through pyosmium, which only the
``roads`` dependency group installs: ``radares feed`` and the Home Assistant image
never import this package's reader. The contract is in ``docs/roads.md``.
"""
