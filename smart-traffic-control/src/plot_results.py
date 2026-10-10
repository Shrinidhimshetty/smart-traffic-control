#!/usr/bin/env python3
"""
plot_results.py -- compare controllers and produce the figures you put in
the report.

Reads data/simulation_results.db (written by run_simulation.py) and produces:

    results/plots/comparison_timeseries.png   queue / waiting / vehicles vs time
    results/plots/comparison_bars.png         summary metrics side by side
    results/plots/junction_heatmap.png        per-junction queue over time
    results/comparison.csv                    the numbers, for the report table

It prints a percentage-improvement table to the terminal. That table is the
headline result of Phase 1 and the baseline every later phase is measured
against.

Usage:
    python src/plot_results.py
    python src/plot_results.py --baseline static --candidate actuated
"""

import argparse
import os
import sqlite3

import matplotlib
matplotlib.use("Agg")          # headless-safe: never needs a display
import matplotlib.pyplot as plt
import numpy as np

import sumo_utils as su

DB = su.path("data", "simulation_results.db")

# metric key -> (label, lower_is_better)
METRICS = [
    ("mean_waiting_time",  "Mean waiting time (s)",   True),
    ("mean_queue_length",  "Mean queue length (veh)", True),
    ("max_queue_length",   "Max queue length (veh)",  True),
    ("mean_speed_ms",      "Mean speed (m/s)",        False),
    ("throughput",         "Throughput (vehicles)",   False),
    ("total_fuel_litres",  "Fuel used (litres)",      True),
    ("total_co2_kg",       "CO2 emitted (kg)",        True),
]


def latest_run(con, controller):
    r = con.execute(
        "SELECT run_id FROM runs WHERE controller=? ORDER BY run_id DESC LIMIT 1",
        (controller,)).fetchone()
    if r is None:
        raise SystemExit(
            f"No run found for controller '{controller}'.\n"
            f"Run:  python src/run_simulation.py --controller {controller}")
    return r[0]


def summary_of(con, run_id):
    return {k: v for k, v in con.execute(
        "SELECT metric, value FROM summary WHERE run_id=?", (run_id,))}


def timeseries_of(con, run_id):
    rows = con.execute(
        "SELECT t, vehicle_count, queue_length, waiting_time, mean_speed "
        "FROM steps WHERE run_id=? ORDER BY t", (run_id,)).fetchall()
    return np.array(rows, dtype=float)


def plot_timeseries(ts_a, ts_b, name_a, name_b, out):
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    panels = [
        (2, "Queue length (stopped vehicles)", "Vehicles"),
        (3, "Mean waiting time per vehicle", "Seconds"),
        (1, "Vehicles in network", "Vehicles"),
    ]
    for ax, (col, title, ylab) in zip(axes, panels):
        ax.plot(ts_a[:, 0] / 60, ts_a[:, col], label=name_a, lw=1.6, color="#c0392b")
        ax.plot(ts_b[:, 0] / 60, ts_b[:, col], label=name_b, lw=1.6, color="#2471a3")
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_ylabel(ylab)
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left")
    axes[-1].set_xlabel("Simulation time (minutes)")
    fig.suptitle("Fixed-timer vs Actuated signal control", fontsize=13, y=0.995)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"[plot] {out}")


def plot_bars(sa, sb, name_a, name_b, out):
    keys = [k for k, _, _ in METRICS if k in sa and k in sb]
    labels = [dict((k, l) for k, l, _ in METRICS)[k] for k in keys]

    # normalise each metric to the baseline so different units share an axis
    va = [1.0 for _ in keys]
    vb = [(sb[k] / sa[k]) if sa[k] else 0.0 for k in keys]

    x = np.arange(len(keys))
    w = 0.38
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.bar(x - w / 2, va, w, label=name_a, color="#c0392b")
    ax.bar(x + w / 2, vb, w, label=name_b, color="#2471a3")
    ax.axhline(1.0, color="black", lw=0.8, ls="--", alpha=0.6)

    for i, k in enumerate(keys):
        ax.text(i + w / 2, vb[i] + 0.02, f"{sb[k]:.1f}", ha="center", fontsize=8)
        ax.text(i - w / 2, 1.02, f"{sa[k]:.1f}", ha="center", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax.set_ylabel(f"Relative to {name_a} (= 1.0)")
    ax.set_title("Controller comparison (bar labels are absolute values)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"[plot] {out}")


def plot_heatmap(con, run_id, title, out):
    rows = con.execute(
        "SELECT t, tls_id, queue FROM junction_stats WHERE run_id=? ORDER BY t",
        (run_id,)).fetchall()
    if not rows:
        print("[plot] no junction data, skipping heatmap")
        return

    times = sorted({r[0] for r in rows})
    tls = sorted({r[1] for r in rows})
    tidx = {t: i for i, t in enumerate(times)}
    jidx = {j: i for i, j in enumerate(tls)}

    mat = np.zeros((len(tls), len(times)))
    for t, j, q in rows:
        mat[jidx[j], tidx[t]] = q

    fig, ax = plt.subplots(figsize=(12, 4.5))
    im = ax.imshow(mat, aspect="auto", cmap="YlOrRd", origin="lower",
                   extent=[times[0] / 60, times[-1] / 60, 0, len(tls)])
    ax.set_yticks(np.arange(len(tls)) + 0.5)
    ax.set_yticklabels(tls, fontsize=8)
    ax.set_xlabel("Simulation time (minutes)")
    ax.set_ylabel("Junction")
    ax.set_title(f"Per-junction queue length — {title}")
    fig.colorbar(im, ax=ax, label="Queued vehicles")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"[plot] {out}")


def main(baseline, candidate):
    if not os.path.isfile(DB):
        raise SystemExit(f"{DB} not found. Run run_simulation.py first.")
    con = sqlite3.connect(DB)

    ra, rb = latest_run(con, baseline), latest_run(con, candidate)
    sa, sb = summary_of(con, ra), summary_of(con, rb)
    ta, tb = timeseries_of(con, ra), timeseries_of(con, rb)

    os.makedirs(su.path("results", "plots"), exist_ok=True)
    plot_timeseries(ta, tb, baseline, candidate,
                    su.path("results", "plots", "comparison_timeseries.png"))
    plot_bars(sa, sb, baseline, candidate,
              su.path("results", "plots", "comparison_bars.png"))
    plot_heatmap(con, ra, baseline,
                 su.path("results", "plots", "junction_heatmap.png"))

    # ---- terminal table + CSV ------------------------------------------
    print("\n" + "=" * 74)
    print(f"{'METRIC':<26}{baseline:>14}{candidate:>14}{'CHANGE':>18}")
    print("=" * 74)
    lines = [("metric", baseline, candidate, "change_percent")]
    for key, label, lower_better in METRICS:
        if key not in sa or key not in sb:
            continue
        a, b = sa[key], sb[key]
        if a == 0:
            continue
        pct = (b - a) / a * 100.0
        better = (pct < 0) if lower_better else (pct > 0)
        mark = "better" if better else "worse"
        print(f"{label:<26}{a:>14.2f}{b:>14.2f}{pct:>+13.1f}%  {mark}")
        lines.append((label, f"{a:.2f}", f"{b:.2f}", f"{pct:+.1f}"))
    print("=" * 74)

    csv_path = su.path("results", "comparison.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        for row in lines:
            f.write(",".join(str(c) for c in row) + "\n")
    print(f"[plot] {csv_path}")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="static")
    ap.add_argument("--candidate", default="actuated")
    a = ap.parse_args()
    main(a.baseline, a.candidate)
