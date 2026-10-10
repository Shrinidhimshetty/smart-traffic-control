#!/usr/bin/env python3
"""
build_network.py -- generate the SUMO road network.

Creates an N x N grid of signalised intersections with "attach" stub edges
on the outside, which act as the sources and sinks for traffic.

Two networks are produced from the same geometry so you can benchmark
controllers against each other:

    net/grid_static.net.xml     fixed-timer signals   (the baseline)
    net/grid_actuated.net.xml   gap-based actuated    (a tougher baseline)

Usage:
    python src/build_network.py                # default 3x3
    python src/build_network.py --size 2       # 2x2
    python src/build_network.py --size 4 --length 250
"""

import argparse
import subprocess
import sys

import sumo_utils as su


def build(size, length, attach, lanes, speed_kmh, green, yellow):
    speed_ms = speed_kmh / 3.6
    netgenerate = su.binary("netgenerate")

    common = [
        netgenerate,
        "--grid",
        "--grid.number", str(size),
        "--grid.length", str(length),
        "--grid.attach-length", str(attach),
        "--default.lanenumber", str(lanes),
        "--default.speed", f"{speed_ms:.2f}",
        "--default-junction-type", "traffic_light",
        "--tls.green.time", str(green),
        "--tls.yellow.time", str(yellow),
        "--tls.allred.time", "2",
        "--tls.discard-simple",   # outer stub nodes are dead ends, not junctions
        "--no-turnarounds",
        "--junctions.corner-detail", "5",
    ]

    targets = [
        ("static", ["--tls.default-type", "static"]),
        ("actuated", ["--tls.default-type", "actuated"]),
    ]

    for name, extra in targets:
        out = su.path("net", f"grid_{name}.net.xml")
        cmd = common + extra + ["-o", out]
        print(f"[net] building {name} -> {out}")
        print("      $", " ".join(cmd))
        subprocess.run(cmd, check=True)

    summarise()


def summarise():
    su.ensure_tools_on_path()
    import sumolib

    print("\n[net] === NETWORK SUMMARY ===")
    for name in ("static", "actuated"):
        net = sumolib.net.readNet(su.path("net", f"grid_{name}.net.xml"),
                                  withPrograms=True)
        edges = [e for e in net.getEdges() if e.allows("passenger")]
        lane_km = sum(e.getLength() * e.getLaneNumber() for e in edges) / 1000.0
        tls = net.getTrafficLights()
        print(f"  {name:9s} junctions={len(net.getNodes()):3d}  "
              f"edges={len(edges):3d}  lane-km={lane_km:6.2f}  "
              f"traffic-lights={len(tls)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=3, help="grid is SIZE x SIZE (default 3)")
    ap.add_argument("--length", type=int, default=200, help="metres between junctions")
    ap.add_argument("--attach", type=int, default=150, help="length of outer stub edges")
    ap.add_argument("--lanes", type=int, default=2, help="lanes per direction")
    ap.add_argument("--speed", type=float, default=50.0, help="speed limit km/h")
    ap.add_argument("--green", type=int, default=31)
    ap.add_argument("--yellow", type=int, default=4)
    a = ap.parse_args()

    build(a.size, a.length, a.attach, a.lanes, a.speed, a.green, a.yellow)
