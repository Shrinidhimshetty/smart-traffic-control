#!/usr/bin/env python3
"""
build_osm.py  --  Emit a valid OpenStreetMap XML extract for the
Mangalore (Mangaluru) city-centre corridor.

WHY THIS FILE EXISTS
--------------------
FALLBACK ONLY. The normal way to get OSM data is osm_import.py, which either
downloads from Overpass or accepts an osmWebWizard file:

    python src/osm_import.py --place mangaluru
    python src/osm_import.py --osm-file path/to/osm_bbox.osm.xml

Use this script only when neither works -- offline, behind a firewall, or
when Overpass is rate limiting. It emits a valid OSM XML extract for the
Mangaluru city centre using real WGS84 coordinates and real road names, in
the exact schema netconvert consumes.

Every downstream step (netconvert, randomTrips, duarouter, sumo, TraCI) is
then the genuine OSM pipeline, unmodified. Swapping in a real downloaded
map later changes nothing else.

    python src/make_sample_osm.py
    python src/osm_import.py --osm-file osm/maps/osm.osm

Geography covered (Mangaluru city centre, ~2.6 km x 2.4 km):
    Hampankatta Circle, PVS Junction, Jyothi Circle, Balmatta Junction,
    Bendoorwell Circle, Kankanady Junction, Nanthoor Junction,
    Bunts Hostel Circle, Lalbagh, Navabharath Circle, KSRTC Bejai,
    Ladyhill Junction.
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
import os
import sys

# --------------------------------------------------------------------------
# 1. NODES  --  real junctions with approximate real lat/lon
#    'signal': True  -> tagged highway=traffic_signals so netconvert's
#                       --tls.guess-signals promotes it to a TLS junction.
# --------------------------------------------------------------------------
NODES = {
    "hampankatta":  (12.8703, 74.8425, True,  "Hampankatta Circle"),
    "pvs":          (12.8752, 74.8421, True,  "PVS Junction"),
    "jyothi":       (12.8734, 74.8449, True,  "Jyothi Circle"),
    "balmatta":     (12.8681, 74.8461, True,  "Balmatta Junction"),
    "bendoorwell":  (12.8641, 74.8502, True,  "Bendoorwell Circle"),
    "kankanady":    (12.8690, 74.8561, True,  "Kankanady Junction"),
    "nanthoor":     (12.8828, 74.8629, True,  "Nanthoor Junction"),
    "buntshostel":  (12.8779, 74.8498, True,  "Bunts Hostel Circle"),
    "lalbagh":      (12.8768, 74.8468, True,  "Lalbagh Junction"),
    "navabharath":  (12.8699, 74.8391, True,  "Navabharath Circle"),
    "ksrtc":        (12.8791, 74.8423, True,  "KSRTC Bejai"),
    "ladyhill":     (12.8845, 74.8455, True,  "Ladyhill Junction"),
    # ---- fringe nodes: these become the network's source/sink edges ----
    "f_north":      (12.8930, 74.8450, False, "NH-66 North (Surathkal)"),
    "f_east":       (12.8880, 74.8740, False, "NH-75 East (Bantwal)"),
    "f_southeast":  (12.8570, 74.8620, False, "Padil / Deralakatte"),
    "f_south":      (12.8560, 74.8440, False, "Ullal / Thokkottu"),
    "f_west":       (12.8700, 74.8300, False, "Bunder / Port Road"),
    "f_northwest":  (12.8830, 74.8330, False, "Kadri Road North"),
}

# --------------------------------------------------------------------------
# 2. WAYS  --  real named roads.
#    (id, from, to, name, highway_class, lanes, oneway, maxspeed_kmh)
# --------------------------------------------------------------------------
WAYS = [
    # --- NH-66 / arterial spine, north-south, dual carriageway -----------
    ("w01", "f_north",     "ladyhill",    "NH-66 Mangaluru Bypass",  "trunk",     3, False, 60),
    ("w02", "ladyhill",    "ksrtc",       "Bejai Main Road",         "primary",   2, False, 50),
    ("w03", "ksrtc",       "pvs",         "Bejai Kapikad Road",      "primary",   2, False, 50),
    ("w04", "pvs",         "hampankatta", "KS Rao Road",             "primary",   2, False, 40),
    ("w05", "hampankatta", "navabharath", "MG Road",                 "primary",   2, False, 40),
    ("w06", "navabharath", "f_west",      "Bunder Road",             "secondary", 2, False, 40),

    # --- Balmatta / Kankanady axis --------------------------------------
    ("w07", "hampankatta", "balmatta",    "Balmatta Road",           "primary",   2, False, 40),
    ("w08", "balmatta",    "bendoorwell", "Bendoor Church Road",     "secondary", 2, False, 40),
    ("w09", "bendoorwell", "kankanady",   "Kankanady Main Road",     "primary",   2, False, 40),
    ("w10", "kankanady",   "nanthoor",    "Kankanady Nanthoor Road", "primary",   2, False, 50),
    ("w11", "nanthoor",    "f_east",      "NH-75 Bantwal Road",      "trunk",     3, False, 60),
    ("w12", "kankanady",   "f_southeast", "Padil Road",              "secondary", 2, False, 50),
    ("w13", "bendoorwell", "f_south",     "Kudroli Thokkottu Road",  "secondary", 2, False, 50),

    # --- Cross links: these are what create the "Silo Effect" -----------
    ("w14", "hampankatta", "jyothi",      "Jyothi Circle Link",      "secondary", 2, False, 40),
    ("w15", "jyothi",      "lalbagh",     "Lalbagh Road",            "secondary", 2, False, 40),
    ("w16", "lalbagh",     "buntshostel", "Kadri Road",              "primary",   2, False, 40),
    ("w17", "buntshostel", "nanthoor",    "Kadri Nanthoor Road",     "primary",   2, False, 50),
    ("w18", "jyothi",      "balmatta",    "Jyothi Balmatta Link",    "tertiary",  1, True,  30),
    ("w19", "pvs",         "lalbagh",     "PVS Lalbagh Link",        "secondary", 2, False, 40),
    ("w20", "ksrtc",       "f_northwest", "Kadri Temple Road",       "secondary", 2, False, 40),
    ("w21", "ladyhill",    "buntshostel", "Ladyhill Kadri Road",     "secondary", 2, False, 40),
    ("w22", "jyothi",      "kankanady",   "Jyothi Kankanady Link",   "tertiary",  1, False, 30),
    ("w23", "navabharath", "balmatta",    "Falnir Road",             "secondary", 2, False, 40),
]


def prettify(elem):
    rough = ET.tostring(elem, "utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ")


def build(out_path):
    osm = ET.Element("osm", version="0.6", generator="build_osm.py (NMAMIT major project)")

    lats = [v[0] for v in NODES.values()]
    lons = [v[1] for v in NODES.values()]
    ET.SubElement(
        osm, "bounds",
        minlat=f"{min(lats):.5f}", minlon=f"{min(lons):.5f}",
        maxlat=f"{max(lats):.5f}", maxlon=f"{max(lons):.5f}",
    )

    # ---- nodes ----
    node_ids = {}
    for i, (key, (lat, lon, signal, name)) in enumerate(NODES.items(), start=1000):
        node_ids[key] = i
        n = ET.SubElement(osm, "node", id=str(i), lat=f"{lat:.7f}", lon=f"{lon:.7f}",
                          version="1", visible="true")
        if signal:
            ET.SubElement(n, "tag", k="highway", v="traffic_signals")
        ET.SubElement(n, "tag", k="name", v=name)

    # ---- ways ----
    for j, (wid, a, b, name, hw, lanes, oneway, speed) in enumerate(WAYS, start=5000):
        w = ET.SubElement(osm, "way", id=str(j), version="1", visible="true")
        ET.SubElement(w, "nd", ref=str(node_ids[a]))
        ET.SubElement(w, "nd", ref=str(node_ids[b]))
        ET.SubElement(w, "tag", k="highway", v=hw)
        ET.SubElement(w, "tag", k="name", v=name)
        ET.SubElement(w, "tag", k="lanes", v=str(lanes))
        ET.SubElement(w, "tag", k="maxspeed", v=str(speed))
        if oneway:
            ET.SubElement(w, "tag", k="oneway", v="yes")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(prettify(osm))

    signals = sum(1 for v in NODES.values() if v[2])
    print(f"[build_osm] wrote {out_path}")
    print(f"[build_osm]   nodes={len(NODES)}  ways={len(WAYS)}  signal-tagged nodes={signals}")


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sumo_utils as su
    out = sys.argv[1] if len(sys.argv) > 1 else su.path("osm", "maps", "osm.osm")
    build(out)
