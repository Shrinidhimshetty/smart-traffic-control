#!/usr/bin/env python3
"""
osm_import.py -- import a real OpenStreetMap road network into SUMO.

Pipeline:  OSM extract  ->  netconvert  ->  osm/networks/osm_{static,actuated}.net.xml

Three ways to supply the map, tried in order:

  1. --osm-file PATH        use an .osm/.osm.xml you already have
                            (this is what osmWebWizard.py produces)
  2. --bbox W,S,E,N         download that bounding box from the Overpass API
  3. --place NAME           use a built-in bounding box (see PLACES)

osmWebWizard route (interactive, needs a browser):

    python %SUMO_HOME%\\tools\\osmWebWizard.py

Select an area, press "Generate Scenario", then point this script at the
osm_bbox.osm.xml it wrote:

    python src/osm_import.py --osm-file path/to/osm_bbox.osm.xml

Usage:
    python src/osm_import.py --place mangaluru
    python src/osm_import.py --bbox 74.836,12.862,74.866,12.886
    python src/osm_import.py --osm-file osm/maps/mangaluru.osm
"""

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request

import sumo_utils as su

# Built-in bounding boxes: west, south, east, north
PLACES = {
    "mangaluru":  (74.8360, 12.8620, 74.8660, 12.8860),
    "udupi":      (74.7350, 13.3300, 74.7620, 13.3520),
    "bengaluru":  (77.5800, 12.9600, 77.6100, 12.9820),
}

OVERPASS = [
    "https://overpass-api.de/api/map?bbox={w},{s},{e},{n}",
    "https://overpass.kumi.systems/api/map?bbox={w},{s},{e},{n}",
]

MAPS_DIR = ("osm", "maps")
NETS_DIR = ("osm", "networks")


def download(bbox, out_path):
    w, s, e, n = bbox
    last = None
    for tpl in OVERPASS:
        url = tpl.format(w=w, s=s, e=e, n=n)
        print(f"[osm] downloading {url}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "smart-traffic-control/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r, \
                    open(out_path, "wb") as f:
                f.write(r.read())
            size = os.path.getsize(out_path) / 1e6
            print(f"[osm] saved {out_path} ({size:.2f} MB)")
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            print(f"[osm] failed: {exc}")
    print("\n[osm] Could not download the map. Options:")
    print("  - check your internet connection and retry")
    print("  - run osmWebWizard.py and pass the file with --osm-file:")
    print("      python %SUMO_HOME%\\tools\\osmWebWizard.py")
    print("  - download manually from https://www.openstreetmap.org/export")
    print(f"\nlast error: {last}")
    return False


def netconvert(osm_file, name):
    typemap_dir = os.path.join(su.sumo_home(), "data", "typemap")
    typemaps = ",".join(
        os.path.join(typemap_dir, f) for f in
        ("osmNetconvert.typ.xml", "osmNetconvertUrbanIT.typ.xml")
        if os.path.isfile(os.path.join(typemap_dir, f)))
    if not typemaps:
        typemaps = os.path.join(typemap_dir, "osmNetconvert.typ.xml")

    common = [
        su.binary("netconvert"),
        "--osm-files", osm_file,
        "--type-files", typemaps,
        "--lefthand",                        # India drives on the left
        "--geometry.remove",                 # drop redundant shape nodes
        "--roundabouts.guess",
        "--ramps.guess",
        "--junctions.join",                  # merge dual-carriageway clusters
        "--junctions.join-dist", "20",
        "--tls.guess-signals",               # honour highway=traffic_signals
        "--tls.discard-simple",              # no TLS on trivial 2-leg nodes
        "--tls.join",
        "--tls.green.time", "31",
        "--tls.yellow.time", "4",
        "--tls.allred.time", "2",
        "--no-turnarounds",
        "--remove-edges.isolated",
        "--keep-edges.by-vclass", "passenger,bus,truck,emergency",
        "--output.street-names", "true",
        "--output.original-names", "true",
        "--no-warnings", "true",
    ]

    os.makedirs(su.path(*NETS_DIR), exist_ok=True)
    for variant, tls_type in (("static", "static"), ("actuated", "actuated")):
        out = su.path(*NETS_DIR, f"{name}_{variant}.net.xml")
        print(f"[osm] netconvert -> {out}")
        subprocess.run(common + ["--tls.default-type", tls_type, "-o", out],
                       check=True)
    return True


def summarise(name):
    su.ensure_tools_on_path()
    import sumolib
    print("\n[osm] === NETWORK SUMMARY ===")
    for variant in ("static", "actuated"):
        path = su.path(*NETS_DIR, f"{name}_{variant}.net.xml")
        net = sumolib.net.readNet(path, withPrograms=True)
        edges = [e for e in net.getEdges() if e.allows("passenger")]
        lane_km = sum(e.getLength() * e.getLaneNumber() for e in edges) / 1000.0
        tls = net.getTrafficLights()
        print(f"  {variant:9s} junctions={len(net.getNodes()):4d}  "
              f"edges={len(edges):4d}  lane-km={lane_km:7.2f}  "
              f"traffic-lights={len(tls)}")
        if variant == "static" and len(tls) == 0:
            print("\n  WARNING: this network has no traffic lights.")
            print("  Pick a denser urban area, or the controllers have "
                  "nothing to control.")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--osm-file", help="existing .osm file (e.g. from osmWebWizard)")
    g.add_argument("--bbox", help="west,south,east,north in degrees")
    g.add_argument("--place", choices=sorted(PLACES), help="built-in bounding box")
    ap.add_argument("--name", default="osm", help="output basename")
    a = ap.parse_args()

    os.makedirs(su.path(*MAPS_DIR), exist_ok=True)

    if a.osm_file:
        osm_file = a.osm_file if os.path.isabs(a.osm_file) else su.path(a.osm_file)
        if not os.path.isfile(osm_file):
            sys.exit(f"[osm] no such file: {osm_file}")
    else:
        if a.bbox:
            bbox = tuple(float(x) for x in a.bbox.split(","))
            if len(bbox) != 4:
                sys.exit("[osm] --bbox needs west,south,east,north")
        else:
            place = a.place or "mangaluru"
            bbox = PLACES[place]
            print(f"[osm] using built-in bounding box for {place}: {bbox}")
        osm_file = su.path(*MAPS_DIR, f"{a.name}.osm")
        if not download(bbox, osm_file):
            sys.exit(1)

    netconvert(osm_file, a.name)
    summarise(a.name)

    print("\n[osm] next steps:")
    print("  python src/osm_routes.py")
    print("  python src/run_simulation.py --map osm --controller max_pressure")


if __name__ == "__main__":
    main()
