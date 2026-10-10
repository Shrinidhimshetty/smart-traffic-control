#!/usr/bin/env python3
"""
generate_tls.py -- write an explicit, editable traffic light program file.

WHY THIS EXISTS
---------------
netgenerate already puts a default signal program inside the .net.xml, but
it is buried in a machine-generated file you should not hand-edit. This
script extracts those programs and rewrites them into

    config/tls_programs.add.xml

as a clean additional-file with programID "0", which OVERRIDES the built-in
program when SUMO loads it. That gives you one readable place to tune signal
timing, and it is the exact file the MAPPO agent will later replace.

WHAT IT LETS YOU CONTROL
------------------------
  --green / --yellow / --allred    phase durations
  --offset-step                    green-wave coordination between junctions

The "offset" is the key coordination knob. Junction k starts its cycle
OFFSET_STEP * k seconds later than junction 0, so a platoon released by one
signal arrives at the next as it turns green. This is a classical, non-AI
way of attacking the "Silo Effect" described in the synopsis -- and it is
the baseline your MAPPO agent has to beat, not just fixed timers.

Usage:
    python src/generate_tls.py
    python src/generate_tls.py --green 25 --yellow 4 --offset-step 8
"""

import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom

import sumo_utils as su

# Program id used for our editable programs. Must NOT be "0": SUMO refuses to
# redefine the built-in program from an additional file.
PROGRAM_ID = "custom"


def classify(state):
    """Classify a phase by its state string."""
    s = state.lower()
    if "y" in s:
        return "yellow"
    if "g" in s:
        return "green"
    return "allred"


def build(green, yellow, allred, offset_step, net_name):
    su.ensure_tools_on_path()
    import sumolib

    net_path = su.path("net", f"grid_{net_name}.net.xml")
    net = sumolib.net.readNet(net_path, withPrograms=True)

    root = ET.Element("additional")
    root.append(ET.Comment(
        " Traffic light programs, programID='custom'. "
        "SUMO forbids redefining programID '0' from an additional file, so "
        "these are loaded as an ALTERNATIVE program and activated at runtime "
        "by run_simulation.py via traci.trafficlight.setProgram(). "
        "Edit signal timings HERE, not inside the generated .net.xml. "
    ))

    tls_list = sorted(net.getTrafficLights(), key=lambda t: t.getID())
    cycle_lengths = []

    for idx, tl in enumerate(tls_list):
        programs = tl.getPrograms()
        if not programs:
            continue
        base = programs[list(programs.keys())[0]]

        offset = (idx * offset_step) % max(1, (green + yellow + allred) * 2)

        el = ET.SubElement(root, "tlLogic", id=tl.getID(), type="static",
                           programID=PROGRAM_ID, offset=str(offset))

        total = 0
        for ph in base.getPhases():
            kind = classify(ph.state)
            dur = {"green": green, "yellow": yellow, "allred": allred}[kind]
            ET.SubElement(el, "phase", duration=str(dur), state=ph.state)
            total += dur
        cycle_lengths.append(total)

    out = su.path("config", "tls_programs.add.xml")
    xml = minidom.parseString(ET.tostring(root, "utf-8")).toprettyxml(indent="  ")
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"[tls] wrote {len(tls_list)} programs (programID='{PROGRAM_ID}') -> {out}")
    if cycle_lengths:
        print(f"[tls] green={green}s yellow={yellow}s all-red={allred}s")
        print(f"[tls] cycle length = {cycle_lengths[0]}s   "
              f"green-wave offset step = {offset_step}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--green", type=int, default=31)
    ap.add_argument("--yellow", type=int, default=4)
    ap.add_argument("--allred", type=int, default=2)
    ap.add_argument("--offset-step", type=int, default=0,
                    help="seconds of green-wave offset between junctions (0 = uncoordinated)")
    ap.add_argument("--net", default="static", choices=["static", "actuated"])
    a = ap.parse_args()
    build(a.green, a.yellow, a.allred, a.offset_step, a.net)
