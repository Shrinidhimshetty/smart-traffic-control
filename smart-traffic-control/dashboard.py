#!/usr/bin/env python3
"""
dashboard.py -- Streamlit dashboard for the AI-Driven Smart Traffic Control System.

Run from the PROJECT ROOT (the folder containing README.md):

    streamlit run dashboard.py

This is a READ-ONLY view over artefacts the project already produces. It runs
no simulations and writes nothing. Sources, in order of preference:

    data/simulation_results.db   SQLite: runs, steps, junction_stats, summary
    results/comparison.csv       the table written by plot_results.py
    results/plots/*.png          the figures written by plot_results.py
    results/screenshots/         SUMO GUI captures, if any exist

If the database is missing, the dashboard explains exactly which commands to
run rather than failing with a traceback.
"""

import inspect
import os
import sqlite3
import sys

import pandas as pd
import streamlit as st

# The rest of the project lives in src/. Import sumo_utils for path handling
# so the dashboard obeys the same project-root logic as every other script.
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "src"))

try:
    import sumo_utils as su
    ROOT = su.project_root()

    def ppath(*p):
        return su.path(*p)
except Exception:  # dashboard must still work if src/ is missing
    ROOT = _ROOT

    def ppath(*p):
        return os.path.join(ROOT, *p)


DB_PATH = ppath("data", "simulation_results.db")
COMPARISON_CSV = ppath("results", "comparison.csv")
PLOTS_DIR = ppath("results", "plots")
SHOTS_DIR = ppath("results", "screenshots")

# Consistent naming and colour across the whole dashboard.
PRETTY = {
    "static": "Fixed-timer",
    "actuated": "Actuated",
    "max_pressure": "Max-Pressure",
}
COLORS = {
    "static": "#c0392b",
    "actuated": "#2471a3",
    "max_pressure": "#1e8449",
}
ORDER = ["static", "actuated", "max_pressure"]

# metric key -> (label, unit, lower_is_better)
METRICS = [
    ("mean_waiting_time",  "Mean waiting time", "s",   True),
    ("mean_queue_length",  "Mean queue length", "veh", True),
    ("max_queue_length",   "Max queue length",  "veh", True),
    ("mean_speed_ms",      "Mean speed",        "m/s", False),
    ("throughput",         "Throughput",        "veh", False),
    ("total_fuel_litres",  "Fuel used",         "L",   True),
    ("total_co2_kg",       "CO2 emitted",       "kg",  True),
]


def _wide(fn):
    """
    Return the right 'full width' keyword for the installed Streamlit version.

    Streamlit renamed `use_container_width=True` to `width="stretch"`. Probing
    the signature keeps this dashboard working on both old and new versions
    instead of emitting deprecation warnings or crashing outright.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if "width" in params:
        return {"width": "stretch"}
    if "use_container_width" in params:
        return {"use_container_width": True}
    return {}


def label(name):
    return PRETTY.get(name, name)


def color(name):
    return COLORS.get(name, "#888888")


# ----------------------------------------------------------------------
# Data access
# ----------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_runs(db_mtime):
    con = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in con.execute("PRAGMA table_info(runs)")]
    has_map = "map_name" in cols
    sel = ("SELECT run_id, controller, net, end_time, seed, started_at"
           + (", map_name" if has_map else "") +
           " FROM runs ORDER BY run_id DESC")
    df = pd.read_sql_query(sel, con)
    con.close()
    if not has_map:
        df["map_name"] = "grid"
    df["map_name"] = df["map_name"].fillna("grid")
    return df


@st.cache_data(show_spinner=False)
def load_summary(run_id, db_mtime):
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT metric, value FROM summary WHERE run_id=?", con, params=(run_id,))
    con.close()
    return dict(zip(df["metric"], df["value"]))


@st.cache_data(show_spinner=False)
def load_steps(run_id, db_mtime):
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT t, vehicle_count, queue_length, waiting_time, mean_speed, "
        "co2_mg, fuel_mg, arrived_cum, teleports_cum "
        "FROM steps WHERE run_id=? ORDER BY t", con, params=(run_id,))
    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_junctions(run_id, db_mtime):
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT t, tls_id, queue, waiting, phase FROM junction_stats "
        "WHERE run_id=? ORDER BY t", con, params=(run_id,))
    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_ambulance(run_id, db_mtime):
    con = sqlite3.connect(DB_PATH)
    try:
        runs = pd.read_sql_query(
            "SELECT ambulance_id, depart, arrive, travel_time, waiting_time, "
            "distance, junctions_preempted FROM ambulance_runs WHERE run_id=? "
            "ORDER BY depart", con, params=(run_id,))
        events = pd.read_sql_query(
            "SELECT t, ambulance_id, tls_id, action FROM ambulance_events "
            "WHERE run_id=? ORDER BY t", con, params=(run_id,))
    except Exception:
        runs = pd.DataFrame()
        events = pd.DataFrame()
    con.close()
    return runs, events


def db_mtime():
    return os.path.getmtime(DB_PATH) if os.path.isfile(DB_PATH) else 0.0


def fmt(value, unit):
    if unit == "veh" and float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}"


# ----------------------------------------------------------------------
st.set_page_config(
    page_title="Smart Traffic Control Dashboard",
    page_icon="🚦",
    layout="wide",
)

st.markdown("""
<style>
  .block-container {padding-top: 2rem; padding-bottom: 2rem;}
  div[data-testid="stMetricValue"] {font-size: 1.6rem;}
  div[data-testid="stMetricLabel"] {font-size: 0.85rem; opacity: 0.85;}
</style>
""", unsafe_allow_html=True)

st.title("🚦 AI-Driven Smart Traffic Control System")
st.caption(
    "Multi-agent traffic signal control on Eclipse SUMO — fixed-timer, "
    "actuated and Max-Pressure control, with ambulance priority, on both a "
    "synthetic grid and a real OpenStreetMap network."
)

# ---- guard: no database yet -------------------------------------------
if not os.path.isfile(DB_PATH):
    st.error(f"No simulation database found at `{DB_PATH}`.")
    st.markdown("Run the pipeline first, from the project root:")
    st.code(
        "python src/build_network.py --size 3\n"
        "python src/generate_tls.py\n"
        "python src/generate_routes.py --end 1800\n"
        "python src/generate_detectors.py\n"
        "python src/run_simulation.py --controller static\n"
        "python src/run_simulation.py --controller actuated\n"
        "python src/run_simulation.py --controller max_pressure\n"
        "python src/plot_results.py",
        language="bash")
    st.stop()

MT = db_mtime()
runs_df = load_runs(MT)

if runs_df.empty:
    st.warning("The database exists but contains no runs yet.")
    st.stop()

# ---- sidebar -----------------------------------------------------------
st.sidebar.header("Controls")

maps_available = sorted(runs_df["map_name"].unique())
MAP_LABEL = {"grid": "Synthetic 3x3 grid", "osm": "OpenStreetMap (real roads)"}
map_name = st.sidebar.radio(
    "Map", maps_available, index=len(maps_available) - 1,
    format_func=lambda m: MAP_LABEL.get(m, m),
    help="Grid and OSM runs use different networks and demand, so they are "
         "never compared against each other.")
runs_df = runs_df[runs_df["map_name"] == map_name].reset_index(drop=True)

available = [c for c in ORDER if c in set(runs_df["controller"])]
available += [c for c in runs_df["controller"].unique() if c not in ORDER]

chosen = st.sidebar.multiselect(
    "Controllers to compare",
    options=available,
    default=available,
    format_func=label,
)

if not chosen:
    st.info("Select at least one controller in the sidebar.")
    st.stop()

st.sidebar.markdown("---")
use_latest = st.sidebar.checkbox(
    "Use latest run of each controller", value=True,
    help="Uncheck to pin a specific run_id — useful when you have several "
         "runs of the same controller with different settings.")

selected = {}
for c in chosen:
    sub = runs_df[runs_df["controller"] == c]
    if use_latest:
        selected[c] = int(sub["run_id"].iloc[0])
    else:
        opts = sub["run_id"].tolist()
        selected[c] = st.sidebar.selectbox(
            f"run_id for {label(c)}", opts, index=0, key=f"run_{c}")

baseline = st.sidebar.selectbox(
    "Baseline for comparison", chosen, index=0, format_func=label,
    help="All percentage changes are measured against this controller.")

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reload data"):
    st.cache_data.clear()
    st.rerun()

with st.sidebar.expander("Run details"):
    st.dataframe(
        runs_df[runs_df["run_id"].isin(selected.values())]
        [["run_id", "controller", "end_time", "seed", "started_at"]],
        hide_index=True, **_wide(st.dataframe))

# ---- load everything once ---------------------------------------------
summaries = {c: load_summary(rid, MT) for c, rid in selected.items()}
steps = {c: load_steps(rid, MT) for c, rid in selected.items()}

(tab_overview, tab_series, tab_junctions, tab_ambulance,
 tab_charts, tab_data) = st.tabs(
    ["Overview", "Time series", "Junction congestion", "Ambulance priority",
     "Generated charts", "Raw data"])

# ======================================================================
# OVERVIEW
# ======================================================================
with tab_overview:
    st.subheader(f"Headline metrics — baseline: {label(baseline)}")

    base_sum = summaries[baseline]

    for c in chosen:
        s = summaries[c]
        st.markdown(f"**{label(c)}**")
        cols = st.columns(len(METRICS))
        for col, (key, lab, unit, lower_better) in zip(cols, METRICS):
            if key not in s:
                col.metric(lab, "—")
                continue
            val = s[key]
            delta, dcolor = None, "off"
            if c != baseline and key in base_sum and base_sum[key]:
                pct = (val - base_sum[key]) / base_sum[key] * 100.0
                delta = f"{pct:+.1f}%"
                better = (pct < 0) if lower_better else (pct > 0)
                dcolor = "normal" if better else "inverse"
                # streamlit colours negative deltas red by default; invert so
                # "better" always shows green regardless of metric direction
                if lower_better:
                    dcolor = "inverse" if better else "normal"
            col.metric(f"{lab} ({unit})", fmt(val, unit),
                       delta=delta, delta_color=dcolor)
        st.markdown("")

    st.markdown("---")
    st.subheader("Comparison table")

    rows = []
    for key, lab, unit, lower_better in METRICS:
        if not all(key in summaries[c] for c in chosen):
            continue
        row = {"Metric": f"{lab} ({unit})"}
        for c in chosen:
            row[label(c)] = round(summaries[c][key], 2)
        for c in chosen:
            if c == baseline:
                continue
            a, b = base_sum.get(key), summaries[c][key]
            row[f"{label(c)} vs {label(baseline)}"] = (
                f"{(b - a) / a * 100:+.1f}%" if a else "—")
        rows.append(row)

    st.dataframe(pd.DataFrame(rows), hide_index=True, **_wide(st.dataframe))

    # health indicators that tell you whether the numbers are trustworthy
    st.subheader("Simulation health")
    hcols = st.columns(len(chosen))
    for col, c in zip(hcols, chosen):
        s = summaries[c]
        tp = int(s.get("teleports", 0))
        col.metric(f"{label(c)} — teleports", tp,
                   help="SUMO teleports a vehicle when it is stuck. "
                        "Anything above 0 means the network gridlocked and "
                        "the results are unreliable.")
        if "phase_switches" in s:
            col.caption(f"Phase switches: {int(s['phase_switches']):,}")

    csv_for_map = ppath("results",
                        "comparison.csv" if map_name == "grid"
                        else "osm_comparison.csv")
    if os.path.isfile(csv_for_map):
        with st.expander(f"{os.path.basename(csv_for_map)} "
                         f"(as written by plot_results.py)"):
            st.dataframe(pd.read_csv(csv_for_map), hide_index=True,
                         **_wide(st.dataframe))

# ======================================================================
# TIME SERIES
# ======================================================================
with tab_series:
    st.subheader("Network state over time")

    smooth = st.slider(
        "Smoothing window (seconds)", 1, 120, 1, step=5,
        help="Rolling mean. The fixed-timer queue is a sawtooth at the "
             "74-second cycle; smoothing makes the underlying trend visible.")

    panels = [
        ("queue_length", "Queue length (stopped vehicles)", "Vehicles"),
        ("waiting_time", "Mean waiting time per vehicle", "Seconds"),
        ("vehicle_count", "Vehicles in network", "Vehicles"),
        ("mean_speed", "Mean network speed", "m/s"),
    ]

    for col_name, title, ylab in panels:
        # Build each controller as its own Series indexed by minutes, then
        # concat on the index. Runs can have DIFFERENT lengths (different
        # --end values), so assigning columns positionally would raise
        # "Length of values does not match length of index".
        parts = []
        for c in chosen:
            df = steps[c]
            if df.empty or col_name not in df:
                continue
            series = df[col_name]
            if smooth > 1:
                series = series.rolling(smooth, min_periods=1).mean()
            parts.append(pd.Series(series.values,
                                   index=df["t"].values / 60.0,
                                   name=label(c)))
        if not parts:
            continue
        frame = pd.concat(parts, axis=1)
        st.markdown(f"**{title}** ({ylab})")
        st.line_chart(
            frame,
            color=[color(c) for c in chosen if label(c) in frame.columns],
            height=260)

    st.markdown("---")
    st.subheader("Cumulative throughput")
    tparts = []
    for c in chosen:
        df = steps[c]
        if not df.empty:
            tparts.append(pd.Series(df["arrived_cum"].values,
                                    index=df["t"].values / 60.0,
                                    name=label(c)))
    if tparts:
        st.area_chart(pd.concat(tparts, axis=1), height=280)
        st.caption(
            "Throughput barely differs between controllers because the same "
            "2,397 vehicles are injected in every run. The difference is in "
            "how long they take, not how many get through.")

# ======================================================================
# JUNCTION CONGESTION
# ======================================================================
with tab_junctions:
    st.subheader("Per-junction congestion")
    st.caption(
        "This is the per-junction state logged every 10 simulated seconds. "
        "It is also exactly the data that becomes the MAPPO observation "
        "vector in Phase 3.")

    jsel = st.selectbox("Controller", chosen, format_func=label, key="jsel")
    jdf = load_junctions(selected[jsel], MT)

    if jdf.empty:
        st.info("No per-junction data recorded for this run.")
    else:
        metric_choice = st.radio(
            "Metric", ["queue", "waiting"], horizontal=True,
            format_func=lambda m: "Queue length (vehicles)" if m == "queue"
            else "Waiting time (seconds)")

        pivot = jdf.pivot_table(index="tls_id", columns="t",
                                values=metric_choice, aggfunc="mean")
        pivot = pivot.sort_index()

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        # ---- heatmap over time -----------------------------------------
        fig, ax = plt.subplots(figsize=(12, 4))
        im = ax.imshow(pivot.values, aspect="auto", cmap="YlOrRd",
                       origin="lower",
                       extent=[pivot.columns.min() / 60, pivot.columns.max() / 60,
                               0, len(pivot.index)])
        ax.set_yticks(np.arange(len(pivot.index)) + 0.5)
        ax.set_yticklabels(pivot.index, fontsize=8)
        ax.set_xlabel("Simulation time (minutes)")
        ax.set_ylabel("Junction")
        ax.set_title(f"{label(jsel)} — per-junction "
                     f"{'queue length' if metric_choice == 'queue' else 'waiting time'}")
        fig.colorbar(im, ax=ax,
                     label="Vehicles" if metric_choice == "queue" else "Seconds")
        fig.tight_layout()
        st.pyplot(fig, **_wide(st.pyplot))
        plt.close(fig)

        # ---- spatial grid ----------------------------------------------
        st.markdown("**Spatial view — average over the whole run**")
        st.caption(
            "Junction IDs are grid coordinates: the letter is the column "
            "(A→C, west→east) and the digit is the row (0→2, south→north). "
            "This lays them out the way they physically sit in the network.")

        means = jdf.groupby("tls_id")[metric_choice].mean()
        cols_letters = sorted({j[0] for j in means.index})
        rows_digits = sorted({j[1:] for j in means.index}, reverse=True)

        grid = np.full((len(rows_digits), len(cols_letters)), np.nan)
        for jid, val in means.items():
            r = rows_digits.index(jid[1:])
            c = cols_letters.index(jid[0])
            grid[r, c] = val

        fig2, ax2 = plt.subplots(figsize=(5.2, 4.6))
        im2 = ax2.imshow(grid, cmap="YlOrRd", vmin=0)
        ax2.set_xticks(range(len(cols_letters)), cols_letters)
        ax2.set_yticks(range(len(rows_digits)), rows_digits)
        for r in range(grid.shape[0]):
            for c in range(grid.shape[1]):
                if not np.isnan(grid[r, c]):
                    ax2.text(c, r, f"{cols_letters[c]}{rows_digits[r]}\n{grid[r, c]:.1f}",
                             ha="center", va="center", fontsize=9,
                             color="black" if grid[r, c] < np.nanmax(grid) * 0.6
                             else "white")
        ax2.set_title(f"{label(jsel)} — mean "
                      f"{'queue' if metric_choice == 'queue' else 'wait'} per junction")
        fig2.colorbar(im2, ax=ax2, shrink=0.8)
        fig2.tight_layout()

        left, right = st.columns([1, 1])
        with left:
            st.pyplot(fig2, **_wide(st.pyplot))
        plt.close(fig2)

        with right:
            st.markdown("**Busiest junctions**")
            rank = means.sort_values(ascending=False).reset_index()
            rank.columns = ["Junction",
                            "Mean queue (veh)" if metric_choice == "queue"
                            else "Mean wait (s)"]
            rank.iloc[:, 1] = rank.iloc[:, 1].round(2)
            st.dataframe(rank, hide_index=True, **_wide(st.dataframe))
            st.caption(
                "The centre junction normally carries the heaviest load "
                "because it lies on the most through-routes.")


# ======================================================================
# AMBULANCE PRIORITY
# ======================================================================
with tab_ambulance:
    st.subheader("Emergency vehicle priority")
    st.caption(
        "Ambulances are detected by vType, the junctions ahead of them are "
        "forced green to form a rolling corridor, and normal control resumes "
        "as soon as they pass.")

    amb_runs_all = runs_df.copy()
    amb_data = {}
    for _, r in amb_runs_all.iterrows():
        rid = int(r["run_id"])
        ar, ae = load_ambulance(rid, MT)
        if not ar.empty:
            amb_data[rid] = (r["controller"], ar, ae)

    if not amb_data:
        st.info("No ambulance runs recorded yet. Generate the route and run it:")
        st.code(
            "python src/generate_ambulance.py\n"
            "python src/run_simulation.py --controller max_pressure --ambulance\n"
            "python src/run_simulation.py --controller max_pressure --ambulance "
            "--amb-no-priority   # control run",
            language="bash")
    else:
        opts = list(amb_data.keys())
        pick = st.selectbox(
            "Run", opts, index=len(opts) - 1,
            format_func=lambda r: f"run {r} — {label(amb_data[r][0])} "
                                  f"({len(amb_data[r][1])} ambulance(s), "
                                  f"{int(amb_data[r][1]['junctions_preempted'].sum())} "
                                  f"preemptions)")
        ctrl_name, ar, ae = amb_data[pick]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ambulances", len(ar))
        c2.metric("Mean travel time (s)", f"{ar['travel_time'].mean():.1f}")
        c3.metric("Mean waiting time (s)", f"{ar['waiting_time'].mean():.1f}")
        c4.metric("Junctions preempted",
                  int(ar["junctions_preempted"].sum()),
                  help="0 means this was a control run with priority disabled.")

        st.markdown("**Per-ambulance log**")
        show = ar.copy()
        show.columns = ["Ambulance", "Depart (s)", "Arrive (s)", "Travel (s)",
                        "Waiting (s)", "Distance (m)", "Junctions preempted"]
        st.dataframe(show.round(1), hide_index=True, **_wide(st.dataframe))

        # ---- priority vs no-priority, if both exist -------------------
        withp, without = [], []
        for rid, (cn, a, _e) in amb_data.items():
            (withp if a["junctions_preempted"].sum() > 0 else without).append(a)

        if withp and without:
            w = pd.concat(withp)
            n = pd.concat(without)
            st.markdown("---")
            st.markdown("**Effect of the green corridor**")
            d1, d2 = st.columns(2)
            tt_gain = (w["travel_time"].mean() - n["travel_time"].mean()) \
                / n["travel_time"].mean() * 100
            d1.metric("Mean travel time with priority",
                      f"{w['travel_time'].mean():.1f} s",
                      delta=f"{tt_gain:+.1f}% vs no priority",
                      delta_color="inverse")
            d2.metric("Mean waiting time with priority",
                      f"{w['waiting_time'].mean():.1f} s",
                      delta=f"{w['waiting_time'].mean() - n['waiting_time'].mean():+.1f} s",
                      delta_color="inverse")
            st.caption(
                f"Without priority the ambulance waits "
                f"{n['waiting_time'].mean():.1f}s on average and takes "
                f"{n['travel_time'].mean():.1f}s. The corridor is what closes "
                f"that gap.")

        # ---- preemption timeline --------------------------------------
        if not ae.empty:
            st.markdown("---")
            st.markdown("**Preemption timeline**")
            st.caption(
                "Each junction is claimed as the ambulance approaches and "
                "released the moment it clears — a corridor that travels with "
                "the vehicle rather than a set of lights stuck on green.")

            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            tls_order = sorted(ae["tls_id"].unique())
            fig, ax = plt.subplots(figsize=(11, 0.6 * len(tls_order) + 2))
            for amb_id in ae["ambulance_id"].dropna().unique():
                sub = ae[ae["ambulance_id"] == amb_id]
                for tls in tls_order:
                    ev = sub[sub["tls_id"] == tls].sort_values("t")
                    starts = ev[ev["action"] == "preempt"]["t"].tolist()
                    ends = ev[ev["action"] == "release"]["t"].tolist()
                    for i, s0 in enumerate(starts):
                        e0 = ends[i] if i < len(ends) else s0 + 5
                        ax.barh(tls_order.index(tls), e0 - s0, left=s0,
                                height=0.55, color="#0073e6", alpha=0.85)
                        ax.text(s0, tls_order.index(tls) + 0.32,
                                str(amb_id), fontsize=7, color="#333")
            ax.set_yticks(range(len(tls_order)))
            ax.set_yticklabels(tls_order)
            ax.set_xlabel("Simulation time (s)")
            ax.set_ylabel("Junction")
            ax.set_title(f"Green corridor — {label(ctrl_name)}")
            ax.grid(axis="x", alpha=0.3)
            fig.tight_layout()
            st.pyplot(fig, **_wide(st.pyplot))
            plt.close(fig)

            with st.expander("Raw preemption events"):
                st.dataframe(ae, hide_index=True, **_wide(st.dataframe))

# ======================================================================
# GENERATED CHARTS
# ======================================================================
with tab_charts:
    st.subheader("Figures produced by plot_results.py")

    pfx = "" if map_name == "grid" else "osm_"
    figures = [
        (f"{pfx}comparison_timeseries.png", "Controller comparison over time",
         "Queue length, waiting time and vehicles in network for every "
         "controller. The fixed-timer sawtooth against the smoother adaptive "
         "traces is the clearest single picture of why adaptive control wins."),
        (f"{pfx}comparison_bars.png", "Summary metrics",
         "Every metric normalised to the baseline controller. Bar labels are "
         "absolute values."),
        (f"{pfx}junction_heatmap.png", "Per-junction queue heatmap",
         "One row per controller, shared colour scale so the panels are "
         "directly comparable."),
    ]

    found = False
    for fname, title, caption in figures:
        path = os.path.join(PLOTS_DIR, fname)
        if os.path.isfile(path):
            found = True
            st.markdown(f"**{title}**")
            st.image(path, **_wide(st.image))
            st.caption(caption)
            st.markdown("---")

    if not found:
        st.info("No figures found in results/plots/. Generate them with:")
        st.code(f"python src/plot_results.py --map {map_name}", language="bash")

    # SUMO GUI screenshots, if the user captured any
    shots = []
    if os.path.isdir(SHOTS_DIR):
        for sub in sorted(os.listdir(SHOTS_DIR)):
            d = os.path.join(SHOTS_DIR, sub)
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f.lower().endswith(".png"):
                        shots.append((sub, os.path.join(d, f)))

    if shots:
        st.subheader("SUMO GUI screenshots")
        controllers_with_shots = sorted({s[0] for s in shots})
        pick = st.selectbox("Controller", controllers_with_shots,
                            format_func=label, key="shotpick")
        chosen_shots = [p for c, p in shots if c == pick]
        cols = st.columns(min(2, len(chosen_shots)))
        for i, p in enumerate(chosen_shots):
            cols[i % len(cols)].image(p, caption=os.path.basename(p),
                                      **_wide(st.image))
    else:
        st.caption(
            "No SUMO screenshots yet. Capture some with:  "
            "`python src/run_simulation.py --controller max_pressure "
            "--gui --colorize --screenshots`")

# ======================================================================
# RAW DATA
# ======================================================================
with tab_data:
    st.subheader("Raw data")

    dsel = st.selectbox("Controller", chosen, format_func=label, key="dsel")
    df = steps[dsel]

    st.markdown(f"**Per-second time series — {label(dsel)}** "
                f"({len(df):,} rows, run_id {selected[dsel]})")
    st.dataframe(df, height=340, **_wide(st.dataframe))

    st.download_button(
        "Download this time series as CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"timeseries_{dsel}.csv",
        mime="text/csv")

    st.markdown("---")
    st.markdown("**Summary metrics stored in SQLite**")
    sdf = pd.DataFrame(
        [{"metric": k, "value": round(v, 4)} for k, v in summaries[dsel].items()])
    st.dataframe(sdf, hide_index=True, **_wide(st.dataframe))

    st.markdown("---")
    st.markdown("**All runs in the database**")
    st.dataframe(runs_df, hide_index=True, **_wide(st.dataframe))

st.markdown("---")
st.caption(
    "Read-only dashboard. It runs no simulations — regenerate the underlying "
    "data with `python src/run_simulation.py --controller <name>` followed by "
    "`python src/plot_results.py`, then press **Reload data** in the sidebar."
)
