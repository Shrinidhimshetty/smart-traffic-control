#!/usr/bin/env python3
"""
osm_routes.py -- generate traffic demand for the imported OSM network.

Pipeline:  randomTrips.py  ->  merged .trips.xml  ->  duarouter  ->  .rou.xml

Output:    osm/routes/osm.rou.xml
           osm/routes/osm_vtypes.add.xml

Same vehicle mix and rush-hour profile as the grid demand, so results on the
two maps are directly comparable: motorcycles 45%, cars 35%, autos 10%,
buses 10%, over a ramp-up / peak / decline profile.

Usage:
    python src/osm_routes.py
    python src/osm_routes.py --end 1800 --scale 1.5
    python src/osm_routes.py --name osm --end 3600
"""

import argparse
import glob
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import sumo_utils as su

# id           vClass       share  colour         len  accel decel sigma speedFac
VTYPES = [
    ("motorcycle", "motorcycle", 0.45, "1,0.85,0",   2.2, 3.0, 5.0, 0.6, 1.10),
    ("car",        "passenger",  0.35, "0.2,0.6,1",  4.5, 2.6, 4.5, 0.5, 1.00),
    ("auto",       "passenger",  0.10, "0.1,0.8,0.3", 3.2, 2.0, 4.0, 0.7, 0.85),
    ("bus",        "bus",        0.10, "1,0.4,0",   12.0, 1.2, 3.5, 0.4, 0.80),
]

# name, start fraction, end fraction, base insertion period (LOWER = MORE)
PHASES = [
    ("rampup",  0.00, 0.25, 1.10),
    ("peak",    0.25, 0.75, 0.55),
    ("decline", 0.75, 1.00, 1.30),
]


def write_vtypes(out):
    root = ET.Element("additional")
    for vid, vclass, share, color, length, accel, decel, sigma, sf in VTYPES:
        ET.SubElement(root, "vType", id=vid, vClass=vclass, color=color,
                      length=f"{length}", accel=f"{accel}", decel=f"{decel}",
                      sigma=f"{sigma}", speedFactor=f"{sf}",
                      minGap="1.5", tau="0.9")
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    print(f"[osm-routes] vTypes -> {out}")
    return out


def main(name, end, scale, seed, fringe_factor):
    net = su.path("osm", "networks", f"{name}_static.net.xml")
    if not os.path.isfile(net):
        sys.exit(f"{net} missing. Run:  python src/osm_import.py --place mangaluru")

    routes_dir = su.path("osm", "routes")
    os.makedirs(routes_dir, exist_ok=True)

    vtype_file = write_vtypes(os.path.join(routes_dir, f"{name}_vtypes.add.xml"))
    rt = su.random_trips_py()
    partials = []

    print("[osm-routes] generating trips per vehicle type per rush-hour phase ...")
    for vid, vclass, share, *_ in VTYPES:
        for pname, f0, f1, base_period in PHASES:
            b, e = int(end * f0), int(end * f1)
            period = base_period / (share * scale)
            out = os.path.join(routes_dir, f"_part_{vid}_{pname}.trips.xml")
            subprocess.run([
                sys.executable, rt,
                "-n", net, "-o", out,
                "-b", str(b), "-e", str(e),
                "-p", f"{period:.4f}",
                # --vclass filters EDGES. Do NOT use --vehicle-class: it
                # injects its own vType and clashes with the custom types.
                "--vclass", vclass,
                "--prefix", f"{vid}_{pname}",
                "--trip-attributes",
                f'type="{vid}" departLane="best" departSpeed="max"',
                "--fringe-factor", str(fringe_factor),
                "--min-distance", "300",
                "--allow-fringe",
                "--validate",
                "--route-file", os.path.join(routes_dir, f"_val_{vid}_{pname}.rou.xml"),
                "--seed", str(seed + len(partials)),
            ], check=True, stdout=subprocess.DEVNULL)
            partials.append(out)

    trips = []
    for p in partials:
        for t in ET.parse(p).getroot().findall("trip"):
            trips.append((float(t.get("depart")), t))
    trips.sort(key=lambda x: x[0])

    root = ET.Element("routes")
    for _, t in trips:
        root.append(t)
    trips_file = os.path.join(routes_dir, f"{name}.trips.xml")
    ET.ElementTree(root).write(trips_file, encoding="utf-8", xml_declaration=True)
    print(f"[osm-routes] {len(trips)} trips -> {trips_file}")

    routes_file = os.path.join(routes_dir, f"{name}.rou.xml")
    print("[osm-routes] routing with duarouter ...")
    subprocess.run([
        su.binary("duarouter"),
        "-n", net,
        "--route-files", trips_file,
        "--additional-files", vtype_file,
        "-o", routes_file,
        "--ignore-errors", "--remove-loops", "--repair",
        "--departlane", "best", "--departspeed", "max",
        "--no-warnings", "true",
    ], check=True, stdout=subprocess.DEVNULL)

    n = len(ET.parse(routes_file).getroot().findall("vehicle"))
    print(f"[osm-routes] DONE -> {routes_file}  ({n} vehicles over {end}s)")

    for junk in partials + glob.glob(os.path.join(routes_dir, "_val_*")) + \
            glob.glob(os.path.join(routes_dir, "*.rou.alt.xml")):
        try:
            os.remove(junk)
        except OSError:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="osm")
    ap.add_argument("--end", type=int, default=1800)
    ap.add_argument("--scale", type=float, default=1.0, help=">1 = more traffic")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fringe-factor", type=int, default=10)
    a = ap.parse_args()
    main(a.name, a.end, a.scale, a.seed, a.fringe_factor)
