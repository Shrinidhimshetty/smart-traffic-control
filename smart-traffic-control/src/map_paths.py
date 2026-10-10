#!/usr/bin/env python3
"""
map_paths.py -- one place that knows where each map's files live.

The project now supports two maps:

    grid   the synthetic 3x3 network      net/, demand/, config/
    osm    a real OpenStreetMap network   osm/networks/, osm/routes/, config/

Every script routes its paths through here, so adding a third map later means
editing this file and nothing else.
"""

import sumo_utils as su


class _MapPaths:

    MAPS = ("grid", "osm")

    def net(self, map_name, variant="static"):
        """Network file for a map + traffic-light variant."""
        if map_name == "osm":
            return su.path("osm", "networks", f"osm_{variant}.net.xml")
        return su.path("net", f"grid_{variant}.net.xml")

    def routes(self, map_name):
        if map_name == "osm":
            return su.path("osm", "routes", "osm.rou.xml")
        return su.path("demand", "grid.rou.xml")

    def ambulance_routes(self, map_name):
        if map_name == "osm":
            return su.path("osm", "routes", "osm_ambulance.rou.xml")
        return su.path("demand", "ambulance.rou.xml")

    def config(self, map_name, filename):
        """Additional/config files are namespaced by map inside config/."""
        if map_name == "osm":
            return su.path("config", f"osm_{filename}")
        return su.path("config", filename)

    def sumocfg(self, map_name, controller):
        prefix = "osm" if map_name == "osm" else "grid"
        return su.path("config", f"{prefix}_{controller}.sumocfg")


MAP_PATHS = _MapPaths()
