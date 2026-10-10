#!/usr/bin/env python3
"""
generate_detectors.py -- place E2 (lane-area) detectors on every incoming
lane of every signalised junction.

These detectors are the project's SENSOR LAYER. In simulation they read from
SUMO. In a real deployment the identical numbers would come from CCTV vehicle
counting or inductive loops -- which is what makes the architecture
"hardware-ready" rather than simulation-only.

Each detector reports, per approach:
    jamLengthInVehicle / jamLengthInMeters  -> QUEUE LENGTH
    lastIntervalMeanTimeLoss                -> DELAY
    lastIntervalVehicleSum                  -> FLOW

These are exactly the quantities that become the MAPPO observation vector
in Phase 3, so this is not throwaway demo scaffolding.

Usage:
    python src/generate_detectors.py
"""

import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom

import sumo_utils as su


def main(det_length, period, net_name):
    su.ensure_tools_on_path()
    import sumolib

    net = sumolib.net.readNet(su.path("net", f"grid_{net_name}.net.xml"),
                              withPrograms=True)
    root = ET.Element("additional")
    n = 0

    for tl in sorted(net.getTrafficLights(), key=lambda t: t.getID()):
        seen = set()
        for in_lane, _out, _link in tl.getConnections():
            lid = in_lane.getID()
            if lid in seen:
                continue
            seen.add(lid)

            lane_len = in_lane.getLength()
            length = min(det_length, max(5.0, lane_len - 1.0))
            pos = max(0.0, lane_len - length - 0.5)

            ET.SubElement(
                root, "laneAreaDetector",
                id=f"det_{tl.getID()}_{lid}".replace("#", "_"),
                lane=lid,
                pos=f"{pos:.2f}",
                length=f"{length:.2f}",
                period=f"{period}",
                file="../results/detectors_out.xml",
                friendlyPos="true",
                timeThreshold="1.0",
                speedThreshold="1.39",   # below 5 km/h counts as halting
                jamThreshold="10.0",
            )
            n += 1

    out = su.path("config", "detectors.add.xml")
    xml = minidom.parseString(ET.tostring(root, "utf-8")).toprettyxml(indent="  ")
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"[detectors] {n} lane-area detectors -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", type=float, default=75.0, help="metres upstream of stop line")
    ap.add_argument("--period", type=float, default=60.0, help="aggregation interval (s)")
    ap.add_argument("--net", default="static", choices=["static", "actuated"])
    a = ap.parse_args()
    main(a.length, a.period, a.net)
