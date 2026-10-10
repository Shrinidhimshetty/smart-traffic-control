# AI-Driven Smart Traffic Control System

**Major Project — Department of Computer Science & Engineering, N.M.A.M. Institute of Technology**

A multi-agent traffic signal control framework built on the Eclipse SUMO
simulator. Phase 1 (this repository) delivers a working simulation with a
fixed-timer baseline and an actuated baseline, full statistics collection
through TraCI, and a reinforcement-learning environment ready for MAPPO.

---

## What actually works right now

Everything in this repository has been executed end to end. These are real
measured outputs from a 3×3 grid, 2,397 vehicles, 30 simulated minutes:

| Metric | Fixed-timer | Actuated | Change |
|---|---:|---:|---:|
| Mean waiting time (s) | 25.28 | 19.88 | **−21.3%** |
| Mean queue length (veh) | 69.04 | 59.80 | **−13.4%** |
| Max queue length (veh) | 178 | 148 | **−16.9%** |
| Mean speed (m/s) | 6.13 | 6.68 | **+8.9%** |
| Throughput (vehicles) | 2,316 | 2,318 | +0.1% |
| Fuel used (litres) | 334.60 | 332.28 | −0.7% |
| CO₂ emitted (kg) | 776.37 | 771.06 | −0.7% |

Read that table carefully, because it sets up the whole project. Actuated
control already beats fixed timers by 21% on waiting time **without any AI at
all**. That is your real baseline. A MAPPO agent that beats fixed timers by
25% has achieved almost nothing; one that beats *actuated* control is a
genuine result.

---

## Requirements checklist

| # | Requirement | Where it lives |
|---|---|---|
| 1 | Project folder structure | this file, below |
| 2 | All files with full code | `src/`, `config/` |
| 3 | Working 2×2 / 3×3 SUMO simulation | `src/build_network.py --size 2` or `3` |
| 4 | Vehicle route generation | `src/generate_routes.py` |
| 5 | Traffic light configuration | `src/generate_tls.py` → `config/tls_programs.add.xml` |
| 6 | TraCI script: queue, waiting time, vehicle count | `src/run_simulation.py` |
| 7 | Windows SUMO install instructions | this file, below |
| 8 | Commands to run | this file, below |
| 9 | README | this file |
| 10 | MAPPO + QIGA roadmap | `ROADMAP.md` |

---

## Directory structure

```
smart-traffic-control/
│
├── README.md                    <- you are here
├── ROADMAP.md                   <- how MAPPO and QIGA get added
├── requirements.txt
├── .gitignore
├── run_all.bat                  <- one-click full pipeline (Windows)
├── run_all.sh                   <- one-click full pipeline (Linux/macOS)
│
├── src/
│   ├── sumo_utils.py            <- finds SUMO on any platform
│   ├── build_network.py         <- generates the N x N grid
│   ├── generate_tls.py          <- writes editable signal programs
│   ├── generate_routes.py       <- vehicle demand
│   ├── generate_detectors.py    <- the sensor layer
│   ├── run_simulation.py        <- MAIN SCRIPT: runs sim, collects stats
│   ├── plot_results.py          <- comparison charts and results table
│   └── env/
│       ├── __init__.py
│       └── traffic_env.py       <- multi-agent RL environment (MAPPO-ready)
│
├── config/
│   ├── grid_static.sumocfg      <- fixed-timer configuration
│   ├── grid_actuated.sumocfg    <- actuated configuration
│   ├── gui-settings.xml         <- display settings
│   ├── tls_programs.add.xml     <- GENERATED: signal timings
│   └── detectors.add.xml        <- GENERATED: 72 lane detectors
│
├── net/
│   ├── grid_static.net.xml      <- GENERATED
│   └── grid_actuated.net.xml    <- GENERATED
│
├── demand/
│   ├── vtypes.add.xml           <- GENERATED: vehicle types
│   ├── grid.trips.xml           <- GENERATED: origin-destination trips
│   └── grid.rou.xml             <- GENERATED: routed vehicles
│
├── data/
│   └── simulation_results.db    <- GENERATED: SQLite results
│
├── results/
│   ├── comparison.csv           <- GENERATED: the results table
│   ├── timeseries_static.csv    <- GENERATED
│   ├── timeseries_actuated.csv  <- GENERATED
│   ├── plots/                   <- GENERATED: PNG charts
│   └── screenshots/             <- GENERATED: SUMO GUI captures
│
└── docs/                        <- put your report drafts here
```

Files marked GENERATED are produced by the scripts and are in `.gitignore`.
Never edit them by hand; edit the script that produces them.

---

## Installing SUMO on Windows

### Step 1 — Install Python 3.10 or newer

Download from [python.org](https://www.python.org/downloads/windows/).
**Tick "Add Python to PATH"** on the first installer screen. Verify:

```cmd
python --version
```

### Step 2 — Install SUMO

Two options. Pick **one**.

**Option A — Official installer (recommended).**
Download the 64-bit Windows installer from
<https://sumo.dlr.de/docs/Downloads.php> and run it. Keep the default path,
normally `C:\Program Files (x86)\Eclipse\Sumo`.

Then set `SUMO_HOME` permanently. Open **Command Prompt as Administrator**:

```cmd
setx SUMO_HOME "C:\Program Files (x86)\Eclipse\Sumo" /M
setx PATH "%PATH%;C:\Program Files (x86)\Eclipse\Sumo\bin" /M
```

**Close and reopen** the terminal — `setx` only affects new terminals. Verify:

```cmd
echo %SUMO_HOME%
sumo --version
```

**Option B — pip only (faster, no admin rights).**

```cmd
pip install eclipse-sumo
```

This ships the SUMO binaries inside the Python package. No `SUMO_HOME`
needed — `src/sumo_utils.py` finds it automatically. The GUI works too.

### Step 3 — Install the Python packages

```cmd
cd path\to\smart-traffic-control
pip install -r requirements.txt
```

### Step 4 — Verify everything

```cmd
python src\sumo_utils.py
```

You should see paths for `sumo`, `sumo-gui`, `netgenerate`, `duarouter`, and
`import OK` for both `traci` and `sumolib`. If anything says `NOT FOUND`,
fix that before continuing — every later step depends on it.

---

## Commands to run the simulation

### The fast path

```cmd
run_all.bat
```

On Linux/macOS: `bash run_all.sh`

That runs the whole pipeline: build network → signal programs → routes →
detectors → both simulations → comparison plots. Takes roughly 3–5 minutes,
most of it in route generation.

### Step by step

```cmd
REM 1. Build the road network (3x3 grid, 9 signalised junctions)
python src\build_network.py --size 3

REM    ...or a smaller 2x2 grid for faster iteration
python src\build_network.py --size 2

REM 2. Write the traffic light programs
python src\generate_tls.py --green 31 --yellow 4

REM 3. Generate vehicle demand (~2400 vehicles over 30 minutes)
python src\generate_routes.py --end 1800

REM 4. Place the lane detectors (the sensor layer)
python src\generate_detectors.py

REM 5. Run the simulations
python src\run_simulation.py --controller static   --end 1800
python src\run_simulation.py --controller actuated --end 1800

REM 6. Compare them
python src\plot_results.py
```

### The visual demo

This is what you show your guide:

```cmd
python src\run_simulation.py --controller static --gui --colorize
```

`--colorize` paints every vehicle by its speed, so congestion is instantly
readable:

- **RED** — stopped at a red light
- **ORANGE** — crawling
- **YELLOW** — slow
- **GREEN** — free flow

Run the same command with `--controller actuated` and watch the red queues
clear noticeably faster. Side by side, that comparison makes the point better
than any table.

To save PNGs instead of watching live, add `--screenshots`. They land in
`results/screenshots/<controller>/`.

### Useful variations

```cmd
REM heavier traffic (1.5x demand)
python src\generate_routes.py --end 1800 --scale 1.5

REM green-wave coordination between junctions
python src\generate_tls.py --offset-step 8

REM shorter cycle
python src\generate_tls.py --green 20 --yellow 3

REM test the MAPPO environment with a random policy
python src\env\traffic_env.py
```

---

## What the TraCI script measures

`src/run_simulation.py` records these **every simulated second**:

| Field | Meaning |
|---|---|
| `vehicle_count` | vehicles currently in the network |
| `queue_length` | vehicles with speed < 0.1 m/s (network-wide queue) |
| `waiting_time` | mean accumulated waiting time per vehicle |
| `total_wait` | summed accumulated waiting time |
| `mean_speed` | network mean speed (m/s) |
| `co2_mg`, `fuel_mg` | instantaneous emissions and fuel |
| `arrived_cum` | completed trips (throughput) |
| `teleports_cum` | gridlock indicator — should stay at 0 |

Plus per-junction queue, waiting time and current phase every 10 seconds.

Outputs go to `results/timeseries_<controller>.csv` and to SQLite at
`data/simulation_results.db` (tables: `runs`, `steps`, `junction_stats`,
`summary`). SQLite means you can query across many runs later without
re-simulating:

```sql
SELECT controller, value FROM summary
JOIN runs USING(run_id)
WHERE metric = 'mean_waiting_time';
```

---

## Things that will bite you

These are real failures hit while building this, with the fixes already
applied. You will meet them again as you extend the project.

**`--tls.cycle.time` and `--tls.green.time` are mutually exclusive** in
netgenerate/netconvert. Pass one, not both.

**Never redefine programID `"0"`** in an additional file. SUMO errors with
*"Another logic with id 'A0' and programID '0' exists"*. That is why
`generate_tls.py` writes programID `"custom"` and `run_simulation.py`
activates it with `traci.trafficlight.setProgram()`.

**duarouter embeds vehicle types into the route file.** If you also load
`vtypes.add.xml` in the sumocfg you get *"Another vehicle type with id
'motorcycle' exists"* and SUMO refuses to start. Load it in duarouter or in
the sumocfg — never both.

**In `randomTrips.py`, `--vclass` and `--vehicle-class` are different
options.** `--vclass` filters which edges may be used. `--vehicle-class`
injects its own generic vType and will clash with your custom types.

**SUMO reports fuel in mg/s, not ml/s.** The naive conversion gives absurd
numbers like 95,000 litres per hour. Convert mg → kg → litres using a petrol
density of about 0.75 kg/L.

**In GUI settings, `minSize` is a visibility threshold, not a minimum draw
size.** Raising it makes roads disappear entirely.

**SUMO silently ignores parts of a hand-written GUI colour scheme.** That is
why `--colorize` sets vehicle colours from Python via
`traci.vehicle.setColor()` instead of relying on the scheme file.

---

## Where to go next

See `ROADMAP.md` for the phase plan, the MAPPO integration design, the QIGA
design, and the division of work across a four-person team.

The single most important next step is **not** to start writing MAPPO. It is
to add a **Max-Pressure** controller as a third baseline. It is about 40
lines, it is the standard benchmark in every traffic-signal RL paper, and
without it your examiners cannot tell whether your AI is genuinely good or
merely better than a straw man.
