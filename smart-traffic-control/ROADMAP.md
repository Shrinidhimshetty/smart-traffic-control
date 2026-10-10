# Roadmap — from working simulation to MAPPO + QIGA

Phase 1 is done and measured. This document is the plan for everything after
it. Each phase ends with a **verifiable artefact**, so you always have
something to show at a review even if the next phase is incomplete.

---

## Phase status

| Phase | Deliverable | Status |
|---|---|---|
| 1 | SUMO grid, routes, signals, TraCI statistics | **Done** |
| 2 | Max-Pressure baseline + fixed evaluation protocol | Next |
| 3 | MAPPO multi-agent controller | Designed, env ready |
| 4 | QIGA global optimiser | Designed |
| 5 | Streamlit dashboard + real-map / CCTV extension | Planned |

---

## Phase 2 — Baselines and evaluation protocol

**Do this before writing a single line of RL code.** It is unglamorous and it
is the difference between a project that gets marks and one that gets
questioned.

### 2.1 Add a Max-Pressure controller

Max-Pressure is the standard non-learning benchmark in the traffic signal
control literature. Every serious RL paper compares against it. It is roughly
40 lines of TraCI.

The rule: for each phase, compute its *pressure* — the number of vehicles on
the lanes that phase would release, minus the number on the lanes they would
move into. Serve the phase with the highest pressure, subject to a minimum
green time.

```python
def max_pressure_phase(tls_id, green_phases, links):
    best, best_p = green_phases[0], float("-inf")
    for ph in green_phases:
        pressure = 0
        for in_lane, out_lane in links[ph]:
            pressure += traci.lane.getLastStepVehicleNumber(in_lane)
            pressure -= traci.lane.getLastStepVehicleNumber(out_lane)
        if pressure > best_p:
            best_p, best = pressure, ph
    return best
```

Add it as `--controller maxpressure` in `run_simulation.py`.

**Expect it to be good.** Max-Pressure typically lands close to, and
sometimes ahead of, actuated control. If your MAPPO agent later beats
Max-Pressure by 8–12%, that is a strong, defensible result worth writing up.

### 2.2 Fix the evaluation protocol now

Decide these once and never change them, or your numbers stop being
comparable:

- **Seeds.** Every controller runs on seeds `{42, 43, 44, 45, 46}`. Report
  mean ± standard deviation. A single seed proves nothing — run-to-run
  variance on this network is easily several percent.
- **Demand scenarios.** Low (`--scale 0.6`), medium (`1.0`), heavy (`1.5`).
  Report all three. Controllers that shine at medium load often collapse at
  heavy load, and that collapse is the interesting finding.
- **Headline metric.** Mean waiting time per vehicle. Secondary: mean queue,
  throughput, CO₂.
- **Never tune on the test scenario.** Hold out at least one demand pattern
  the agent never trains on.

**Artefact:** a results table with five controllers × three demand levels ×
five seeds. That table alone is a respectable chapter of your report.

---

## Phase 3 — MAPPO

### 3.1 What is already built

`src/env/traffic_env.py` is a working multi-agent environment. Run it:

```cmd
python src\env\traffic_env.py
```

It reports 9 agents, a 33-dimensional observation per agent, and 2 discrete
actions, then plays a full episode with a random policy. The environment is
not a stub — only the policy is missing.

**Observation (33 values per agent)**

| Component | Size | Why |
|---|---:|---|
| queue / waiting / occupancy per incoming lane | 8 lanes × 3 = 24 | local state |
| current phase, one-hot | 4 | agent must know what it is doing |
| time elapsed in current phase | 1 | prevents rapid switching |
| neighbour queue totals | 4 | **this is what defeats the Silo Effect** |

That neighbour block is the single most important design decision. Remove it
and each agent optimises its own junction while pushing congestion next door
— exactly the failure the synopsis sets out to solve.

**Action:** choose the next green phase. The environment inserts yellow and
all-red transitions automatically and enforces `min_green`, so an untrained
network can produce inefficiency but never an unsafe signal sequence.

**Reward:**

```
reward_i = -(Δ waiting time at junction i)
           + neighbour_weight × mean(Δ waiting time at neighbours of i)
```

Change-based rewards train far more stably than raw queue counts because they
are naturally centred on zero. `neighbour_weight` (default 0.25) is the knob
that converts nine selfish agents into a cooperative team — tune it first.

### 3.2 The library decision — read this carefully

**Stable-Baselines3 does not implement MAPPO.** It only has single-agent PPO.
Any tutorial claiming otherwise is describing independent PPO. Your options:

**Option A — parameter-shared PPO with a centralised critic (recommended).**
All nine agents share one policy network. The critic sees the concatenated
global state during training; the actor sees only local observations at
execution time. This *is* MAPPO in substance, it is what most published
traffic-signal work actually does, and it trains far faster because every
junction contributes experience to the same network.

**Option B — the reference implementation.** Clone `marlbenchmark/on-policy`
(the original MAPPO code). More faithful to the paper, considerably more
work to wire up, and the repository is not actively maintained.

Take Option A. Wrap `MultiAgentTrafficEnv` in a PettingZoo `ParallelEnv`,
then use SuperSuit's vector wrappers to expose it as a batched single-agent
problem for PPO.

### 3.3 Training reality check

Be honest with your guide about compute:

- A 3×3 grid needs roughly **300k–800k environment steps** to converge.
- On a consumer laptop CPU, budget **6–20 hours**.
- Use `libsumo` instead of `traci` for training — same API, no GUI, 5–10×
  faster. Switch back to `traci` for demos.

```python
# training: fast, headless
import libsumo as traci
# demo: visual
import traci
```

- Save checkpoints every 50k steps. Training *will* be interrupted.
- Log to TensorBoard from day one. Reward curves are how you diagnose a
  broken reward function, and you will have one at some point.

### 3.4 Failure modes to expect

| Symptom | Likely cause |
|---|---|
| Reward rises but waiting time gets worse | Reward is misaligned. Check the sign and normalisation. |
| Agents flicker between phases every step | `min_green` too low, or no phase-change penalty. |
| One junction hogs green forever | Missing fairness term. Add a max-wait penalty per approach. |
| Great in training, poor at higher demand | Overfitted to one demand level. Randomise `--scale` during training. |

---

## Phase 4 — QIGA (Quantum-Inspired Genetic Algorithm)

### 4.1 Set expectations honestly

QIGA is **not** quantum computing. It is a classical evolutionary algorithm
that borrows the *representation* of qubits: each gene is a probability
amplitude pair rather than a fixed bit, and a rotation gate nudges those
amplitudes toward the best solution found so far.

Say this plainly in your report. Panels reward honest framing and punish
overselling — and "quantum-inspired" is a legitimate, published technique
that stands on its own merits.

**Qiskit is not needed.** QIGA is pure NumPy. Use Qiskit only if you want one
circuit-diagram slide for presentation.

### 4.2 What QIGA optimises

Not per-second signal decisions — that is MAPPO's job. QIGA runs on a slower
loop (every ~300 simulated seconds) and optimises the **global coordination
parameters**:

- cycle length per junction
- **offset per junction** (green-wave coordination — the highest-value knob)
- green split between competing phases

Chromosome for a 3×3 grid: 9 junctions × 3 parameters = 27 genes, encoded as
qubit pairs.

### 4.3 Algorithm

```
initialise Q-bit population: every gene = (α, β) with α = β = 1/√2
                             (equal superposition — all values equally likely)

repeat for each generation:
    observe:   collapse each Q-bit to a classical bit using |α|²
    decode:    bits -> cycle lengths, offsets, green splits
    evaluate:  run a SHORT SUMO episode, fitness = -mean waiting time
    update:    rotate each Q-bit toward the best solution found so far
                   Δθ = lookup(current bit, best bit, fitness comparison)
                   [α', β'] = R(Δθ) · [α, β]
    optionally: apply a Q-gate mutation to escape local optima
```

The rotation angle Δθ is typically 0.01π–0.05π. Too large and it converges
prematurely to a local optimum; too small and it never converges. This lookup
table is the piece with the least reliable guidance online — take it from a
specific paper and cite that paper.

### 4.4 The comparison you must run

Implement a **plain classical GA** with the same chromosome, the same
population size, and the same fitness function. Then compare convergence.

If QIGA converges faster or to a better optimum, you have a result. If it
does not, that is *also* a publishable finding, and reporting it honestly is
better science than quietly dropping the comparison. Without this baseline,
an examiner can reasonably ask whether the quantum framing does any work at
all — and you will have no answer.

### 4.5 Integration with MAPPO

```
        ┌──────────────────────────────────────────┐
        │  QIGA  —  global layer, every ~300 s     │
        │  optimises cycle lengths + offsets       │
        └────────────────┬─────────────────────────┘
                         │ constraints (offset, cycle bounds)
                         ▼
        ┌──────────────────────────────────────────┐
        │  MAPPO  —  local layer, every 5 s        │
        │  9 agents choose phases within bounds    │
        └────────────────┬─────────────────────────┘
                         │ phase commands
                         ▼
        ┌──────────────────────────────────────────┐
        │  SUMO  —  Digital Twin, every 1 s        │
        └────────────────┬─────────────────────────┘
                         │ detector readings
                         └──────────► back up to both layers
```

QIGA sets the boundaries; MAPPO acts within them. Two loops at different
timescales, which is also how real adaptive systems such as SCOOT and SCATS
are structured — worth a sentence in your literature review.

---

## Phase 5 — Dashboard and real-world extension

**Streamlit dashboard.** KPI cards, live queue heatmap, controller selector,
side-by-side replay from the SQLite database. The data layer already exists,
so this is presentation work, not new engineering.

**Real map.** `osmWebWizard.py` (ships with SUMO) downloads any real road
network from OpenStreetMap. Import a congested corridor from your own city.
This single change separates your project from every other 3×3 grid submission
and costs about a day.

**CCTV adapter — the highest-value extension.** Train YOLOv8 on a short
traffic video, count vehicles per lane, and feed those counts in place of
SUMO's virtual detectors. Write it behind a small interface:

```python
class TrafficDataSource:
    def get_queue_lengths(self, junction_id) -> dict[str, int]: ...
    def get_waiting_times(self, junction_id) -> dict[str, float]: ...

class SumoSource(TrafficDataSource):   ...   # simulation
class CameraSource(TrafficDataSource): ...   # YOLOv8 on real footage
```

That interface **is** the "hardware-agnostic framework" your synopsis
promises. It costs almost nothing architecturally and turns a claim in your
conclusion into a demonstrated capability.

---

## Team split (four people)

| Person | Owns |
|---|---|
| 1 | Networks, demand scenarios, incident and emergency-vehicle injection |
| 2 | MAPPO: environment, training loop, ST-GCN neighbour module |
| 3 | QIGA + classical GA + all baselines (fixed, actuated, Max-Pressure) |
| 4 | Dashboard, SQLite analytics, YOLO adapter, evaluation pipeline |

Everyone contributes to the final evaluation table. That table is what your
guide and your examiners will actually judge.

---

## Suggested schedule

| Weeks | Work |
|---|---|
| 1–2 | Phase 2: Max-Pressure, seeds, demand scenarios, results table |
| 3–5 | Phase 3: PettingZoo wrapper, PPO training loop, first convergence |
| 6–7 | Phase 3: neighbour observations, reward tuning, fairness term |
| 8–9 | Phase 4: QIGA + classical GA comparison |
| 10 | Phase 4: two-layer integration |
| 11 | Phase 5: dashboard, real OSM map |
| 12 | Full evaluation sweep, report, presentation |

Build the smallest working version of each phase before improving any of
them. A complete, honest 3×3 result beats a half-finished 5×5 every time.
