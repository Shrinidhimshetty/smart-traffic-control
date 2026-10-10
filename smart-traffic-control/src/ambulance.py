#!/usr/bin/env python3
"""
ambulance.py -- emergency vehicle detection and green-corridor preemption.

WHAT THIS DOES
--------------
Every simulation second it:

  1. DETECTS ambulances, by vType rather than by id string, so any vehicle
     of type "ambulance" is picked up automatically.
  2. LOOKS AHEAD along the ambulance's route using traci.vehicle.getNextTLS(),
     which returns the upcoming traffic lights with the exact link index the
     vehicle will cross and the distance to each.
  3. PREEMPTS every junction inside the lookahead distance -- not just the
     next one. Holding two or three junctions green at once is what turns
     this from "one green light" into a green CORRIDOR.
  4. RELEASES each junction as soon as the ambulance has passed it, handing
     control straight back to the normal controller.

HOW IT COEXISTS WITH THE NORMAL CONTROLLER
------------------------------------------
Two controllers writing to the same traffic light would fight each other and
produce flickering. So preemption takes an exclusive lock:

    ctrl.preempted   a set of TLS ids the base controller must not touch

MaxPressureController checks that set at the top of its per-junction loop and
skips any locked junction. When the ambulance passes, the id is removed and
Max-Pressure resumes from the phase preemption left behind.

For the SUMO-native controllers there is no Python object to coordinate with,
so the manager restores the signal program on release instead:
    static    -> re-select program "custom" (restarts the fixed cycle)
    actuated  -> re-select program "0"      (hands back to actuated logic)

SAFETY
------
The controller never jumps straight from one green to a conflicting green.
Every switch inserts the yellow phase SUMO defined for that transition. An
ambulance therefore never gets a green that cuts across traffic which still
believes it has right of way. The ambulance vType also sets
jmDriveAfterRedTime="-1", meaning it will not run a red light -- it waits for
the preemption to give it a legitimate green.
"""


class AmbulancePriority:
    """Green-corridor preemption for emergency vehicles."""

    HOLD = 3600.0   # long phase duration: we control the timing, not SUMO

    def __init__(self, traci_conn, base_controller=None, controller_name="static",
                 lookahead=220.0, corridor_junctions=3, yellow_time=3.0,
                 verbose=True, enabled=True):
        self.traci = traci_conn
        self.ctrl = base_controller          # MaxPressureController or None
        self.controller_name = controller_name
        self.lookahead = lookahead           # metres ahead to hold green
        self.corridor_junctions = corridor_junctions
        self.yellow_time = yellow_time
        self.verbose = verbose
        # enabled=False still spawns, tracks and logs the ambulance but never
        # preempts. That is the CONTROL CASE: it is what lets you prove the
        # feature works, instead of just asserting it.
        self.enabled = enabled

        self.phase_states = {}       # tls -> [state string per phase]
        self.locked = {}             # tls -> ambulance id holding it
        self._pending = {}           # tls -> (target phase, time yellow ends)
        self._saved_program = {}     # tls -> program id active before preemption

        # ---- logging ----
        self.active = {}             # ambulance id -> tracking dict
        self.records = []            # completed ambulance journeys
        self.events = []             # (time, ambulance, tls, action)
        self.preemption_count = 0

    # ------------------------------------------------------------------
    def setup(self):
        """Cache phase state strings once, after traci.start()."""
        tr = self.traci
        for t in tr.trafficlight.getIDList():
            logic = tr.trafficlight.getAllProgramLogics(t)[0]
            self.phase_states[t] = [ph.state for ph in logic.phases]
        if self.enabled:
            print(f"[ambulance] preemption armed on {len(self.phase_states)} "
                  f"junctions (lookahead {self.lookahead:.0f} m, "
                  f"up to {self.corridor_junctions} junctions held at once)")
        else:
            print("[ambulance] PRIORITY DISABLED (control run): the ambulance "
                  "is tracked and logged but gets no green corridor")

    # ------------------------------------------------------------------
    def _ambulance_ids(self):
        """Detect by vType, not by id, so any emergency vehicle is handled."""
        tr = self.traci
        out = []
        for vid in tr.vehicle.getIDList():
            try:
                if tr.vehicle.getTypeID(vid) == "ambulance":
                    out.append(vid)
            except tr.TraCIException:
                continue
        return out

    def _green_phase_for_link(self, tls_id, link_index):
        """
        Find a phase that gives this specific movement a green.

        Prefer a protected green ('G') over a permissive one ('g'): a
        protected green means the ambulance does not have to yield.
        """
        states = self.phase_states.get(tls_id, [])
        protected, permissive = None, None
        for i, state in enumerate(states):
            if link_index >= len(state):
                continue
            ch = state[link_index]
            if ch == "G" and protected is None:
                protected = i
            elif ch == "g" and permissive is None:
                permissive = i
        return protected if protected is not None else permissive

    def _yellow_phase_after(self, tls_id, phase):
        states = self.phase_states.get(tls_id, [])
        if not states:
            return None
        nxt = (phase + 1) % len(states)
        return nxt if "y" in states[nxt].lower() else None

    # ------------------------------------------------------------------
    def _preempt(self, tls_id, link_index, amb_id, now):
        """Force this junction green for the ambulance's movement."""
        tr = self.traci
        target = self._green_phase_for_link(tls_id, link_index)
        if target is None:
            return

        if tls_id not in self.locked:
            # take the lock and remember what was running before
            try:
                self._saved_program[tls_id] = tr.trafficlight.getProgram(tls_id)
            except tr.TraCIException:
                self._saved_program[tls_id] = None
            self.locked[tls_id] = amb_id
            self.preemption_count += 1
            self.events.append((now, amb_id, tls_id, "preempt"))
            if self.ctrl is not None:
                self.ctrl.preempted.add(tls_id)
            if self.verbose:
                print(f"  [amb] t={now:.0f}s  {amb_id}: GREEN CORRIDOR -> {tls_id}")

        # already transitioning to the right phase?
        if tls_id in self._pending:
            target_phase, yellow_end = self._pending[tls_id]
            if now >= yellow_end:
                tr.trafficlight.setPhase(tls_id, target_phase)
                tr.trafficlight.setPhaseDuration(tls_id, self.HOLD)
                del self._pending[tls_id]
            return

        current = tr.trafficlight.getPhase(tls_id)
        if current == target:
            tr.trafficlight.setPhaseDuration(tls_id, self.HOLD)
            return

        # never jump green -> conflicting green: insert the proper yellow
        yellow = self._yellow_phase_after(tls_id, current)
        states = self.phase_states[tls_id]
        if yellow is not None and "y" not in states[current].lower():
            tr.trafficlight.setPhase(tls_id, yellow)
            tr.trafficlight.setPhaseDuration(tls_id, self.yellow_time)
            self._pending[tls_id] = (target, now + self.yellow_time)
        else:
            tr.trafficlight.setPhase(tls_id, target)
            tr.trafficlight.setPhaseDuration(tls_id, self.HOLD)

    def _release(self, tls_id, now):
        """Hand the junction back to the normal controller."""
        tr = self.traci
        amb_id = self.locked.pop(tls_id, None)
        self._pending.pop(tls_id, None)
        self.events.append((now, amb_id, tls_id, "release"))

        if self.ctrl is not None:
            # Max-Pressure: unlock and let it re-evaluate immediately
            self.ctrl.preempted.discard(tls_id)
            self.ctrl.release(tls_id, now)
        else:
            # SUMO-native controller: re-selecting the program restores it
            prog = self._saved_program.get(tls_id)
            if prog:
                try:
                    tr.trafficlight.setProgram(tls_id, prog)
                except tr.TraCIException:
                    pass
        self._saved_program.pop(tls_id, None)
        if self.verbose:
            print(f"  [amb] t={now:.0f}s  {tls_id}: released to "
                  f"{self.controller_name}")

    # ------------------------------------------------------------------
    def step(self, now):
        """Call once per simulation step, after traci.simulationStep()."""
        tr = self.traci
        ambulances = self._ambulance_ids()
        still_needed = set()

        for amb in ambulances:
            rec = self.active.get(amb)
            if rec is None:
                rec = {
                    "id": amb,
                    "depart": now,
                    "junctions": set(),
                    "wait": 0.0,
                    "distance": 0.0,
                }
                self.active[amb] = rec
                if self.verbose:
                    print(f"  [amb] t={now:.0f}s  {amb} entered the network")

            try:
                rec["wait"] = tr.vehicle.getAccumulatedWaitingTime(amb)
                rec["distance"] = tr.vehicle.getDistance(amb)
                upcoming = tr.vehicle.getNextTLS(amb)
            except tr.TraCIException:
                continue

            # upcoming entries: (tlsID, tlsLinkIndex, distance, state)
            if not self.enabled:
                continue

            held = 0
            for tls_id, link_index, dist, _state in upcoming:
                if held >= self.corridor_junctions:
                    break
                if dist > self.lookahead:
                    break
                if tls_id not in self.phase_states:
                    continue
                still_needed.add(tls_id)
                rec["junctions"].add(tls_id)
                self._preempt(tls_id, link_index, amb, now)
                held += 1

        # release every junction no ambulance still needs
        for tls_id in list(self.locked.keys()):
            if tls_id not in still_needed:
                self._release(tls_id, now)

        # finalise ambulances that have left the network
        present = set(ambulances)
        for amb in list(self.active.keys()):
            if amb not in present:
                rec = self.active.pop(amb)
                rec["arrive"] = now
                rec["travel_time"] = now - rec["depart"]
                self.records.append(rec)
                if self.verbose:
                    print(f"  [amb] t={now:.0f}s  {amb} COMPLETED: "
                          f"travel {rec['travel_time']:.1f}s, "
                          f"waiting {rec['wait']:.1f}s, "
                          f"{len(rec['junctions'])} junctions preempted")

    # ------------------------------------------------------------------
    def finalise(self, now):
        """Close out any ambulance still on the network when the run ends."""
        for amb, rec in list(self.active.items()):
            rec["arrive"] = now
            rec["travel_time"] = now - rec["depart"]
            rec["unfinished"] = True
            self.records.append(rec)
            self.active.pop(amb)

    def report(self):
        """Console summary. Returns the aggregate dict for the database."""
        if not self.records:
            print("[ambulance] no ambulances were observed in this run")
            return {}

        n = len(self.records)
        tt = [r["travel_time"] for r in self.records]
        wt = [r["wait"] for r in self.records]

        print(f"\n[ambulance] ===== EMERGENCY VEHICLE REPORT =====")
        print(f"  {'ambulance':<14}{'travel (s)':>12}{'waiting (s)':>13}"
              f"{'junctions':>11}")
        for r in sorted(self.records, key=lambda x: x["depart"]):
            flag = "  (unfinished)" if r.get("unfinished") else ""
            print(f"  {r['id']:<14}{r['travel_time']:>12.1f}{r['wait']:>13.1f}"
                  f"{len(r['junctions']):>11}{flag}")
        print(f"  {'-' * 48}")
        print(f"  {'mean':<14}{sum(tt) / n:>12.1f}{sum(wt) / n:>13.1f}")
        print(f"  preemptions triggered: {self.preemption_count}"
              f"{'' if self.enabled else '   (priority disabled)'}")

        return {
            "ambulance_count": float(n),
            "ambulance_mean_travel_time": sum(tt) / n,
            "ambulance_mean_waiting_time": sum(wt) / n,
            "ambulance_max_waiting_time": max(wt),
            "ambulance_preemptions": float(self.preemption_count),
        }
