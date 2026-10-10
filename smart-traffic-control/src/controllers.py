#!/usr/bin/env python3
"""
controllers.py -- Python-side traffic signal controllers.

WHERE THIS FITS IN THE EXISTING ARCHITECTURE
--------------------------------------------
Before this file existed, both controllers in the project were SUMO-native:

    static     fixed-timer program defined in config/tls_programs.add.xml
    actuated   SUMO's built-in gap-based logic, baked into the .net.xml

Neither needed a line of Python during the run -- run_simulation.py simply
loaded a different .sumocfg. Max-Pressure is the first controller that must
make live decisions, so it lives here and is driven from the simulation loop
in run_simulation.py via `controller_obj.step(now)`.

Max-Pressure reuses net/grid_static.net.xml. It needs a program with fixed
phase DEFINITIONS (which movements go together) but ignores the fixed phase
DURATIONS, choosing which phase to run itself.

THE ALGORITHM
-------------
For every green phase, compute its "pressure":

    pressure(phase) = sum over movements served by that phase of
                      (vehicles waiting on the incoming lane
                       - vehicles already on the outgoing lane)

Then serve the phase with the highest pressure.

The subtraction is the whole idea. A movement with 20 vehicles queued but a
jammed road ahead has LOW pressure, because releasing those vehicles just
moves the jam one block downstream. A movement with 12 vehicles queued and an
empty road ahead has HIGH pressure. So Max-Pressure naturally avoids feeding
congested downstream links -- which is exactly the "Silo Effect" the project
synopsis describes, solved without any learning at all.

Max-Pressure has a proven theoretical property: it is throughput-optimal,
meaning it keeps queues bounded whenever ANY fixed-time plan could. That is
why it is the standard benchmark in the traffic-signal RL literature, and why
beating it is a much stronger claim than beating fixed timers.

SAFETY
------
The controller never jumps straight between two greens. Switching always
inserts the yellow phase that SUMO defined for that transition, and a
minimum green time prevents rapid flickering between phases.

AMBULANCE PREEMPTION
--------------------
`self.preempted` holds the ids of junctions currently commandeered by an
emergency vehicle (see src/ambulance.py). This controller skips them
entirely, so preemption and Max-Pressure never write to the same signal on
the same step. `release()` resynchronises this controller when the ambulance
has passed.
"""


class MaxPressureController:
    """
    Live Max-Pressure signal control over TraCI.

    Usage from run_simulation.py:

        ctrl = MaxPressureController(traci, min_green=10, yellow_time=4)
        ctrl.setup()
        while simulating:
            traci.simulationStep()
            ctrl.step(traci.simulation.getTime())
    """

    HOLD = 3600.0   # keep SUMO from auto-advancing a phase we control

    def __init__(self, traci_conn, min_green=10.0, yellow_time=4.0,
                 decision_interval=5.0, verbose=False):
        self.traci = traci_conn
        self.min_green = min_green
        self.yellow_time = yellow_time
        self.decision_interval = decision_interval
        self.verbose = verbose

        self.tls_ids = []
        self.green_phases = {}     # tls -> [phase index, ...]
        self.phase_movements = {}  # tls -> {phase index: [(inLane, outLane), ...]}
        self.yellow_after = {}     # tls -> {green phase: yellow phase}

        self._state = {}           # tls -> "GREEN" | "YELLOW"
        self._phase_start = {}     # tls -> sim time the current green began
        self._pending = {}         # tls -> green phase to enter after yellow
        self._yellow_end = {}      # tls -> sim time the yellow ends
        self._last_decision = {}   # tls -> last time we evaluated pressure
        self.switch_count = 0

        # Junctions currently under ambulance preemption. AmbulancePriority
        # adds ids here and this controller skips them, so the two never
        # fight over the same signal. See src/ambulance.py.
        self.preempted = set()

    # ------------------------------------------------------------------
    def setup(self):
        """Read phase definitions and movement lists once, after traci.start()."""
        tr = self.traci
        self.tls_ids = list(tr.trafficlight.getIDList())

        for t in self.tls_ids:
            logic = tr.trafficlight.getAllProgramLogics(t)[0]
            phases = logic.phases
            links = tr.trafficlight.getControlledLinks(t)

            greens = []
            for i, ph in enumerate(phases):
                s = ph.state.lower()
                if "g" in s and "y" not in s:
                    greens.append(i)
            self.green_phases[t] = greens or [0]

            # Which (incoming, outgoing) movements does each green phase serve?
            moves = {}
            for p in self.green_phases[t]:
                state = phases[p].state
                pairs = []
                seen = set()
                for idx, ch in enumerate(state):
                    if ch not in "Gg" or idx >= len(links):
                        continue
                    for link in links[idx]:
                        if len(link) < 2:
                            continue
                        in_lane, out_lane = link[0], link[1]
                        key = (in_lane, out_lane)
                        # de-duplicate: one lane can feed several link indices,
                        # and counting it repeatedly biases the pressure
                        if key not in seen:
                            seen.add(key)
                            pairs.append(key)
                moves[p] = pairs
            self.phase_movements[t] = moves

            # The yellow phase SUMO defined for leaving each green.
            ymap = {}
            for p in self.green_phases[t]:
                nxt = (p + 1) % len(phases)
                ymap[p] = nxt if "y" in phases[nxt].state.lower() else None
            self.yellow_after[t] = ymap

            # Start every junction on its first green, held indefinitely.
            first = self.green_phases[t][0]
            tr.trafficlight.setPhase(t, first)
            tr.trafficlight.setPhaseDuration(t, self.HOLD)
            self._state[t] = "GREEN"
            self._phase_start[t] = 0.0
            self._last_decision[t] = -1e9

        total_moves = sum(len(m) for mv in self.phase_movements.values()
                          for m in mv.values())
        print(f"[max_pressure] controlling {len(self.tls_ids)} junctions, "
              f"{total_moves} movements, "
              f"{len(self.green_phases[self.tls_ids[0]])} green phases each")
        print(f"[max_pressure] min_green={self.min_green:.0f}s  "
              f"yellow={self.yellow_time:.0f}s  "
              f"decision_interval={self.decision_interval:.0f}s")

    # ------------------------------------------------------------------
    def pressure(self, tls_id, phase):
        """
        Pressure of a phase = (vehicles waiting to enter) - (vehicles ahead).

        Using vehicle counts rather than halting counts is the textbook
        formulation: a movement whose downstream link is filling up should
        lose priority even if those downstream vehicles are still rolling.
        """
        tr = self.traci
        total = 0
        for in_lane, out_lane in self.phase_movements[tls_id][phase]:
            total += tr.lane.getLastStepVehicleNumber(in_lane)
            total -= tr.lane.getLastStepVehicleNumber(out_lane)
        return total

    def best_phase(self, tls_id):
        best, best_p = None, None
        for p in self.green_phases[tls_id]:
            val = self.pressure(tls_id, p)
            if best_p is None or val > best_p:
                best, best_p = p, val
        return best, best_p

    # ------------------------------------------------------------------
    def release(self, tls_id, now):
        """
        Called by AmbulancePriority when preemption ends at a junction.

        Preemption left the signal on whatever phase the ambulance needed, so
        we resynchronise our own bookkeeping to that phase and allow an
        immediate pressure re-evaluation. Without this the controller would
        believe a stale phase was running and could hold it for a full
        min_green before correcting.
        """
        try:
            current = self.traci.trafficlight.getPhase(tls_id)
        except Exception:
            current = self.green_phases.get(tls_id, [0])[0]
        self._state[tls_id] = "GREEN"
        self._phase_start[tls_id] = now
        self._last_decision[tls_id] = -1e9      # decide again on the next step
        self._pending = getattr(self, "_pending", {})
        self._pending.pop(tls_id, None)
        if current not in self.green_phases.get(tls_id, []):
            # preemption may have left a yellow/all-red showing
            self.traci.trafficlight.setPhase(tls_id, self.green_phases[tls_id][0])
            self.traci.trafficlight.setPhaseDuration(tls_id, self.HOLD)

    def step(self, now):
        """Call once per simulation step, after traci.simulationStep()."""
        tr = self.traci

        for t in self.tls_ids:
            # An ambulance owns this junction right now: hands off entirely.
            if t in self.preempted:
                continue

            state = self._state[t]

            # ---- finishing a yellow transition ----
            if state == "YELLOW":
                if now >= self._yellow_end[t]:
                    target = self._pending.pop(t, self.green_phases[t][0])
                    tr.trafficlight.setPhase(t, target)
                    tr.trafficlight.setPhaseDuration(t, self.HOLD)
                    self._state[t] = "GREEN"
                    self._phase_start[t] = now
                continue

            # ---- holding a green ----
            # Re-assert the long duration so SUMO never advances on its own.
            tr.trafficlight.setPhaseDuration(t, self.HOLD)

            if now - self._phase_start[t] < self.min_green:
                continue
            if now - self._last_decision[t] < self.decision_interval:
                continue
            self._last_decision[t] = now

            current = tr.trafficlight.getPhase(t)
            if current not in self.green_phases[t]:
                continue

            target, target_p = self.best_phase(t)
            if target is None or target == current:
                continue

            # Only switch if the alternative is genuinely better. Ties keep
            # the current phase, which avoids pointless lost time in yellow.
            if target_p <= self.pressure(t, current):
                continue

            yellow = self.yellow_after[t].get(current)
            if yellow is None:
                tr.trafficlight.setPhase(t, target)
                tr.trafficlight.setPhaseDuration(t, self.HOLD)
                self._phase_start[t] = now
            else:
                tr.trafficlight.setPhase(t, yellow)
                tr.trafficlight.setPhaseDuration(t, self.yellow_time)
                self._pending[t] = target
                self._yellow_end[t] = now + self.yellow_time
                self._state[t] = "YELLOW"

            self.switch_count += 1
            if self.verbose:
                print(f"  [mp] t={now:.0f} {t}: phase {current} -> {target} "
                      f"(pressure {target_p})")


# Maps a controller name to the network variant it runs on. Max-Pressure has
# no network of its own -- it drives the static network from Python.
CONTROLLER_NET = {
    "static": "static",
    "actuated": "actuated",
    "max_pressure": "static",
}


def build_controller(name, traci_conn, **kwargs):
    """Factory used by run_simulation.py. Returns None for SUMO-native controllers."""
    if name == "max_pressure":
        return MaxPressureController(traci_conn, **kwargs)
    return None
