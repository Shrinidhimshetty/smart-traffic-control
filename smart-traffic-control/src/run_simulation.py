#!/usr/bin/env python3
"""
run_simulation.py -- run the grid simulation under a chosen signal controller
and collect traffic statistics through TraCI.

THIS IS THE MAIN SCRIPT. Everything else feeds into it.

Controllers
-----------
  static    fixed-timer signals            (the baseline to beat)
  actuated  SUMO gap-based actuated logic  (a genuinely competitive baseline)

Metrics collected EVERY simulation second
-----------------------------------------
  vehicle_count   vehicles currently in the network
  queue_length    vehicles with speed < 0.1 m/s  (network-wide queue)
  waiting_time    mean accumulated waiting time per vehicle (s)
  total_wait      summed accumulated waiting time (s)
  mean_speed      network mean speed (m/s)
  co2_mg          instantaneous CO2 (mg/s)
  fuel_mg         instantaneous fuel (mg/s)   [SUMO reports mg/s, NOT ml/s]
  arrived_cum     completed trips (throughput)
  teleports_cum   gridlock indicator

Per-junction queue and waiting time are logged every 10 s. That per-junction
table is what Phase 3 turns into the MAPPO observation vector.

Outputs
-------
  results/timeseries_<controller>.csv
  data/simulation_results.db   (SQLite: runs, steps, junction_stats, summary)

Usage
-----
  python src/run_simulation.py --controller static
  python src/run_simulation.py --controller actuated --gui --colorize
  python src/run_simulation.py --controller static --gui --colorize --screenshots
"""

import argparse
import csv
import os
import sqlite3
import sys

import sumo_utils as su

su.ensure_tools_on_path()
import traci  # noqa: E402

DB_PATH = su.path("data", "simulation_results.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    controller TEXT,
    net        TEXT,
    routes     TEXT,
    end_time   INTEGER,
    seed       INTEGER,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS steps (
    run_id INTEGER, t REAL,
    vehicle_count INTEGER, queue_length INTEGER,
    waiting_time REAL, total_wait REAL, mean_speed REAL,
    co2_mg REAL, fuel_mg REAL,
    arrived_cum INTEGER, teleports_cum INTEGER
);
CREATE TABLE IF NOT EXISTS junction_stats (
    run_id INTEGER, t REAL, tls_id TEXT,
    queue INTEGER, waiting REAL, phase INTEGER
);
CREATE TABLE IF NOT EXISTS summary (
    run_id INTEGER, metric TEXT, value REAL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id);
CREATE INDEX IF NOT EXISTS idx_junc_run  ON junction_stats(run_id);
"""


def speed_color(speed):
    """RED stopped -> GREEN free flow. Makes congestion legible instantly."""
    if speed < 0.3:
        return (220, 20, 20, 255)
    if speed < 3.0:
        return (255, 130, 0, 255)
    if speed < 7.0:
        return (250, 220, 0, 255)
    if speed < 12.0:
        return (140, 220, 40, 255)
    return (0, 190, 90, 255)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.commit()
    return con


def controlled_lanes(tls_ids):
    """TLS id -> de-duplicated list of its controlled incoming lanes."""
    out = {}
    for t in tls_ids:
        seen, lanes = set(), []
        for lane in traci.trafficlight.getControlledLanes(t):
            if lane not in seen:
                seen.add(lane)
                lanes.append(lane)
        out[t] = lanes
    return out


def run(controller, use_gui, colorize, screenshots, end_time, seed, step_length):
    cfg = su.path("config", f"grid_{controller}.sumocfg")
    if not os.path.isfile(cfg):
        sys.exit(f"Missing {cfg}. Run the build steps first (see README).")

    binary = su.binary("sumo-gui" if use_gui else "sumo")
    cmd = [
        binary, "-c", cfg,
        "--seed", str(seed),
        "--end", str(end_time),
        "--step-length", str(step_length),
        "--no-warnings", "true",
    ]
    if use_gui:
        cmd += ["--window-size", "1400,950", "--start", "true", "--quit-on-end", "true"]

    shot_dir = su.path("results", "screenshots", controller)
    if screenshots:
        os.makedirs(shot_dir, exist_ok=True)
    shot_times = {int(end_time * f) for f in (0.25, 0.5, 0.75, 0.95)}

    print(f"[run] controller={controller}  gui={use_gui}  end={end_time}s  seed={seed}")
    traci.start(cmd)

    con = init_db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO runs (controller, net, routes, end_time, seed) VALUES (?,?,?,?,?)",
        (controller, f"net/grid_{controller}.net.xml", "demand/grid.rou.xml",
         end_time, seed),
    )
    run_id = cur.lastrowid
    con.commit()

    tls_ids = traci.trafficlight.getIDList()
    tls_lanes = controlled_lanes(tls_ids)
    print(f"[run] {len(tls_ids)} signalised junctions: {', '.join(sorted(tls_ids))}")

    # Activate our editable signal programs from config/tls_programs.add.xml.
    # SUMO loads them as an alternative program; program "0" stays active until
    # we switch, so we switch here. Actuated nets keep their own logic.
    if controller == "static":
        switched = 0
        for t in tls_ids:
            progs = [l.programID for l in
                     traci.trafficlight.getAllProgramLogics(t)]
            if "custom" in progs:
                traci.trafficlight.setProgram(t, "custom")
                switched += 1
        if switched:
            print(f"[run] activated custom signal program on {switched} junctions")

    if use_gui:
        try:
            view = traci.gui.DEFAULT_VIEW
            import sumolib
            net = sumolib.net.readNet(su.path("net", f"grid_{controller}.net.xml"))
            x0, y0, x1, y1 = net.getBoundary()
            traci.gui.setBoundary(view, x0 - 40, y0 - 40, x1 + 40, y1 + 40)
        except Exception as exc:
            print(f"[run] gui framing skipped: {exc}")

    rows, jrows = [], []
    arrived_cum = teleports_cum = 0
    step = 0

    while step < end_time:
        traci.simulationStep()
        step = int(traci.simulation.getTime())

        arrived_cum += traci.simulation.getArrivedNumber()
        teleports_cum += traci.simulation.getStartingTeleportNumber()

        vids = traci.vehicle.getIDList()
        vehicle_count = len(vids)
        queue_length = 0
        total_wait = speed_sum = co2 = fuel = 0.0

        for v in vids:
            s = traci.vehicle.getSpeed(v)
            speed_sum += s
            if s < 0.1:
                queue_length += 1
            total_wait += traci.vehicle.getAccumulatedWaitingTime(v)
            co2 += traci.vehicle.getCO2Emission(v)
            fuel += traci.vehicle.getFuelConsumption(v)
            if colorize and use_gui:
                traci.vehicle.setColor(v, speed_color(s))

        mean_speed = speed_sum / vehicle_count if vehicle_count else 0.0
        waiting_time = total_wait / vehicle_count if vehicle_count else 0.0

        rows.append((run_id, step, vehicle_count, queue_length, waiting_time,
                     total_wait, mean_speed, co2, fuel, arrived_cum, teleports_cum))

        if step % 10 == 0:
            for t, lanes in tls_lanes.items():
                q = sum(traci.lane.getLastStepHaltingNumber(l) for l in lanes)
                w = sum(traci.lane.getWaitingTime(l) for l in lanes)
                ph = traci.trafficlight.getPhase(t)
                jrows.append((run_id, step, t, q, w, ph))

        if screenshots and use_gui and step in shot_times:
            p = os.path.join(shot_dir, f"t{step:05d}.png")
            try:
                traci.gui.screenshot(traci.gui.DEFAULT_VIEW, p)
            except traci.TraCIException as exc:
                print(f"[run] screenshot failed at {step}s: {exc}")

        if step % 300 == 0:
            print(f"  t={step:5d}s  vehicles={vehicle_count:4d}  "
                  f"queue={queue_length:4d}  meanWait={waiting_time:6.1f}s  "
                  f"arrived={arrived_cum:5d}")

    traci.close()

    cur.executemany("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    cur.executemany("INSERT INTO junction_stats VALUES (?,?,?,?,?,?)", jrows)

    n = len(rows)
    agg = {
        "mean_vehicle_count": sum(r[2] for r in rows) / n,
        "mean_queue_length":  sum(r[3] for r in rows) / n,
        "max_queue_length":   max(r[3] for r in rows),
        "mean_waiting_time":  sum(r[4] for r in rows) / n,
        "peak_waiting_time":  max(r[4] for r in rows),
        "mean_speed_ms":      sum(r[6] for r in rows) / n,
        "total_co2_kg":       sum(r[7] for r in rows) / 1e9 * 1000,
        "total_fuel_litres":  sum(r[8] for r in rows) / 1e6 / 0.75,
        "throughput":         arrived_cum,
        "teleports":          teleports_cum,
    }
    cur.executemany("INSERT INTO summary VALUES (?,?,?)",
                    [(run_id, k, float(v)) for k, v in agg.items()])
    con.commit()

    csv_path = su.path("results", f"timeseries_{controller}.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "vehicle_count", "queue_length", "waiting_time",
                    "total_wait", "mean_speed", "co2_mg", "fuel_mg",
                    "arrived_cum", "teleports_cum"])
        for r in rows:
            w.writerow(r[1:])

    print(f"\n[run] ===== SUMMARY ({controller}) =====")
    for k, v in agg.items():
        print(f"  {k:20s} {v:12.2f}")
    print(f"\n[run] run_id={run_id}")
    print(f"[run] CSV -> {csv_path}")
    print(f"[run] DB  -> {DB_PATH}")
    con.close()
    return run_id, agg


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run the SUMO grid simulation via TraCI")
    ap.add_argument("--controller", default="static", choices=["static", "actuated"])
    ap.add_argument("--gui", action="store_true", help="run sumo-gui instead of headless sumo")
    ap.add_argument("--colorize", action="store_true", help="colour vehicles by speed (needs --gui)")
    ap.add_argument("--screenshots", action="store_true", help="save PNGs (needs --gui)")
    ap.add_argument("--end", type=int, default=1800)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--step-length", type=float, default=1.0)
    a = ap.parse_args()
    run(a.controller, a.gui, a.colorize, a.screenshots, a.end, a.seed, a.step_length)
