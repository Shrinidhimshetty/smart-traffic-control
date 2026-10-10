#!/usr/bin/env python3
"""
generate_ambulance.py -- create the emergency vehicle type and its route.

Writes demand/ambulance.rou.xml, a SELF-CONTAINED file holding:
    - the "ambulance" vType (vClass=emergency, blue, high acceleration)
    - the corridor route it drives
    - one or more ambulance vehicles with staggered departure times

It is deliberately a separate file from demand/grid.rou.xml so that:
    1. normal traffic is completely unchanged -- the ambulance run and the
       baseline run use the identical 2,397 background vehicles, so any
       difference in the numbers is caused by preemption and nothing else;
    2. the ambulance is loaded only when you pass --ambulance, so every
       result you have already collected stays valid.

The default corridor crosses the middle row of the grid west to east:

    left1A1 -> A1B1 -> B1C1 -> C1right1

which passes THREE signalised junctions (A1, B1, C1). That is what makes a
green *corridor* demonstrable rather than a single green light.

Usage:
    python src/generate_ambulance.py
    python src/generate_ambulance.py --corridor south_north --count 2
    python src/generate_ambulance.py --depart 900 --count 3 --gap 300
"""

import argparse
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom

import sumo_utils as su
from map_paths import MAP_PATHS

# Named corridors through the 3x3 grid. Each crosses three traffic lights.
CORRIDORS = {
    # west -> east along the middle row: junctions A1, B1, C1
    "west_east":  ["left1A1", "A1B1", "B1C1", "C1right1"],
    # east -> west along the middle row
    "east_west":  ["right1C1", "C1B1", "B1A1", "A1left1"],
    # south -> north up the middle column: junctions B0, B1, B2
    "south_north": ["bottom1B0", "B0B1", "B1B2", "B2top1"],
    # north -> south down the middle column
    "north_south": ["top1B2", "B2B1", "B1B0", "B0bottom1"],
}


def auto_corridor(map_name):
    """
    Pick a corridor automatically for a map that has no hand-written one.

    Used for the OSM network, where edge ids come from OSM and cannot be
    hard-coded. Strategy: take the two most distant fringe edges that a
    passenger car can travel between, and route through the network with
    sumolib's Dijkstra so the path crosses as many signals as possible.
    """
    su.ensure_tools_on_path()
    import sumolib

    net = sumolib.net.readNet(MAP_PATHS.net(map_name, "static"), withPrograms=True)
    tls_nodes = {t.getID() for t in net.getTrafficLights()}

    # candidate entry/exit edges: those starting or ending at a dead end
    sources = [e for e in net.getEdges()
               if e.allows("emergency") and len(e.getFromNode().getIncoming()) == 0]
    sinks = [e for e in net.getEdges()
             if e.allows("emergency") and len(e.getToNode().getOutgoing()) == 0]
    if not sources or not sinks:
        sources = [e for e in net.getEdges() if e.allows("emergency")]
        sinks = sources

    best_path, best_signals = None, -1
    for src in sources:
        for dst in sinks:
            if src.getID() == dst.getID():
                continue
            path, cost = net.getOptimalPath(src, dst, vClass="emergency")
            if path is None:
                continue
            signals = sum(1 for e in path if e.getToNode().getID() in tls_nodes)
            if signals > best_signals:
                best_signals, best_path = signals, path

    if best_path is None:
        raise SystemExit(
            f"[ambulance] could not find a route through the {map_name} network")

    edges = [e.getID() for e in best_path]
    print(f"[ambulance] auto corridor for '{map_name}': {len(edges)} edges, "
          f"{best_signals} signalised junctions")
    return edges


def build(corridor, count, first_depart, gap, speed_factor, out_path,
          map_name="grid"):
    if map_name == "grid":
        edges = CORRIDORS[corridor]
    else:
        edges = auto_corridor(map_name)

    root = ET.Element("routes")
    root.append(ET.Comment(
        f" Emergency vehicle demand. Corridor '{corridor}': "
        f"{' '.join(edges)} "
    ))

    # ---- the ambulance vehicle type --------------------------------------
    # vClass="emergency" matters: SUMO gives emergency vehicles their own
    # right-of-way rules, and it lets us identify them from TraCI without
    # relying on the id string.
    ET.SubElement(
        root, "vType",
        id="ambulance",
        vClass="emergency",
        guiShape="emergency",
        color="0,0.45,1",          # blue, so it stands out against traffic
        length="6.5",
        width="2.2",
        accel="3.2",               # accelerates harder than ordinary traffic
        decel="6.0",
        emergencyDecel="9.0",
        sigma="0.1",               # near-deterministic driving
        speedFactor=str(speed_factor),
        minGap="1.0",
        tau="0.6",
        jmDriveAfterRedTime="-1",  # never runs a red: preemption gives it green
        jmIgnoreFoeProb="0.0",
    )

    route_id = f"ambulance_route_{corridor if map_name == 'grid' else map_name}"
    ET.SubElement(root, "route", id=route_id, edges=" ".join(edges))

    for i in range(count):
        depart = first_depart + i * gap
        ET.SubElement(
            root, "vehicle",
            id=f"ambulance_{i}",
            type="ambulance",
            route=route_id,
            depart=f"{depart}",
            departLane="best",
            departSpeed="max",
            departPos="0",
        )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    xml = minidom.parseString(ET.tostring(root, "utf-8")).toprettyxml(indent="  ")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)

    shown = " -> ".join(edges) if len(edges) <= 8 else \
        " -> ".join(edges[:4]) + f" ... ({len(edges)} edges) ... " + \
        " -> ".join(edges[-2:])
    print(f"[ambulance] corridor: {shown}")
    print(f"[ambulance] {count} ambulance(s), first departs at {first_depart}s, "
          f"gap {gap}s")
    print(f"[ambulance] wrote {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corridor", default="west_east", choices=sorted(CORRIDORS),
                    help="which corridor the ambulance drives")
    ap.add_argument("--count", type=int, default=2, help="number of ambulances")
    ap.add_argument("--depart", type=float, default=900.0,
                    help="departure time of the first ambulance (s). 900 puts "
                         "it at the start of the peak, where preemption matters")
    ap.add_argument("--gap", type=float, default=600.0,
                    help="seconds between successive ambulances")
    ap.add_argument("--speed-factor", type=float, default=1.3,
                    help="how far above the speed limit the ambulance drives")
    ap.add_argument("--map", default="grid", choices=["grid", "osm"],
                    dest="map_name",
                    help="grid uses a named corridor; osm derives one automatically")
    a = ap.parse_args()

    build(a.corridor, a.count, a.depart, a.gap, a.speed_factor,
          MAP_PATHS.ambulance_routes(a.map_name), a.map_name)
