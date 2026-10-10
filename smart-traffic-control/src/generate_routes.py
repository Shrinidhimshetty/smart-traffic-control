#!/usr/bin/env python3
"""
generate_routes.py -- create the vehicle demand for the grid network.

Produces demand/grid.rou.xml via the standard SUMO pipeline:

    randomTrips.py  ->  merged .trips.xml  ->  duarouter  ->  .rou.xml

Two things make this more useful than a flat randomTrips call:

1. VEHICLE MIX. Indian urban traffic is two-wheeler dominated, so we model
   motorcycles 45%, cars 35%, autos 10%, buses 10%, each with its own
   length, acceleration and speed factor.

2. RUSH-HOUR PROFILE. Demand ramps up, peaks, then declines. A fixed-timer
   controller looks fine under constant demand and falls apart when demand
   changes -- which is precisely the weakness the MAPPO layer must exploit,
   so the baseline has to be tested under changing conditions.

Usage:
    python src/generate_routes.py
    python src/generate_routes.py --end 1800 --scale 1.5   # heavier traffic
"""

import argparse
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

# name, begin, end, base insertion period (LOWER = MORE traffic)
PHASES = [
    ("rampup",  0.00, 0.25, 1.10),
    ("peak",    0.25, 0.75, 0.55),
    ("decline", 0.75, 1.00, 1.30),
]


def write_vtypes():
    root = ET.Element("additional")
    for vid, vclass, share, color, length, accel, decel, sigma, sf in VTYPES:
        ET.SubElement(root, "vType", id=vid, vClass=vclass, color=color,
                      length=f"{length}", accel=f"{accel}", decel=f"{decel}",
                      sigma=f"{sigma}", speedFactor=f"{sf}",
                      minGap="1.5", tau="0.9")
    out = su.path("demand", "vtypes.add.xml")
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    print(f"[demand] vTypes -> {out}")
    return out


def main(end, scale, seed, fringe_factor):
    net = su.path("net", "grid_static.net.xml")
    if not os.path.isfile(net):
        sys.exit("net/grid_static.net.xml missing. Run: python src/build_network.py")

    vtype_file = write_vtypes()
    rt = su.random_trips_py()
    partials = []

    print("[demand] generating trips per vehicle type per rush-hour phase ...")
    for vid, vclass, share, *_ in VTYPES:
        for pname, f0, f1, base_period in PHASES:
            b, e = int(end * f0), int(end * f1)
            period = base_period / (share * scale)
            out = su.path("demand", f"_part_{vid}_{pname}.trips.xml")
            cmd = [
                sys.executable, rt,
                "-n", net, "-o", out,
                "-b", str(b), "-e", str(e),
                "-p", f"{period:.4f}",
                # NOTE: --vclass filters which EDGES may be used.
                # Do NOT use --vehicle-class here: it injects its own generic
                # vType and clashes with the custom types above.
                "--vclass", vclass,
                "--prefix", f"{vid}_{pname}",
                "--trip-attributes", f'type="{vid}" departLane="best" departSpeed="max"',
                "--fringe-factor", str(fringe_factor),
                "--min-distance", "250",
                "--allow-fringe",
                "--validate",
                # randomTrips --validate writes a scratch "routes.rou.xml" in
                # the CWD unless told otherwise; keep it inside demand/.
                "--route-file", su.path("demand", f"_val_{vid}_{pname}.rou.xml"),
                "--seed", str(seed + len(partials)),
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            partials.append(out)

    # merge all partial trip files, sorted by departure time
    trips = []
    for p in partials:
        for t in ET.parse(p).getroot().findall("trip"):
            trips.append((float(t.get("depart")), t))
    trips.sort(key=lambda x: x[0])

    root = ET.Element("routes")
    for _, t in trips:
        root.append(t)
    trips_file = su.path("demand", "grid.trips.xml")
    ET.ElementTree(root).write(trips_file, encoding="utf-8", xml_declaration=True)
    print(f"[demand] {len(trips)} trips -> {trips_file}")

    routes_file = su.path("demand", "grid.rou.xml")
    print("[demand] routing with duarouter ...")
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
    print(f"[demand] DONE -> {routes_file}  ({n} vehicles over {end}s)")

    for p in partials:
        os.remove(p)
    # remove duarouter/randomTrips scratch files
    import glob
    for junk in glob.glob(su.path("demand", "_val_*")) + \
                glob.glob(su.path("demand", "*.rou.alt.xml")):
        try:
            os.remove(junk)
        except OSError:
            pass
    alt = routes_file.replace(".rou.xml", ".rou.alt.xml")
    if os.path.isfile(alt):
        os.remove(alt)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=int, default=1800, help="simulation seconds of demand")
    ap.add_argument("--scale", type=float, default=1.0, help=">1 = more traffic")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fringe-factor", type=int, default=8,
                    help="higher = more traffic enters from map edges")
    a = ap.parse_args()
    main(a.end, a.scale, a.seed, a.fringe_factor)
