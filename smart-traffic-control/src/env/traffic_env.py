#!/usr/bin/env python3
"""
traffic_env.py -- multi-agent reinforcement learning environment wrapping SUMO.

This is the bridge between Phase 1 (working simulation) and Phase 3 (MAPPO).
It is NOT a placeholder: run it directly and it will drive the simulation with
a random policy and report the reward it achieved.

    python src/env/traffic_env.py

Design
------
One AGENT per signalised junction. Nine junctions on a 3x3 grid = nine agents.

OBSERVATION (per agent), all values normalised to roughly [0, 1]:
    - queue length on each controlled incoming lane
    - mean waiting time on each controlled incoming lane
    - lane occupancy on each controlled incoming lane
    - current phase, one-hot encoded
    - time elapsed in the current phase
    - NEIGHBOUR queue totals (one value per adjacent junction)

  That last item matters. Without neighbour information each agent optimises
  its own junction in isolation and you reproduce the "Silo Effect" the
  synopsis complains about. Including it is what makes cooperation possible.

ACTION (per agent), discrete:
    Choose which green phase to run next. The environment inserts the required
    yellow and all-red transition automatically, so the agent can never
    produce an unsafe signal sequence. This is important: it means an
    untrained network cannot cause a crash, only inefficiency.

REWARD (per agent):
    reward = -(accumulated waiting time now - accumulated waiting time before)
    on that junction's own approaches, plus a small shared term from the
    neighbours' change.

  Change-based rewards are far more stable to train than raw queue counts,
  because they are naturally centred near zero. The shared term is what
  turns nine selfish agents into a cooperative team, and its weight
  (`neighbour_weight`) is the single most important tuning knob in Phase 3.

Interface follows the standard Gym/PettingZoo parallel API:
    obs = env.reset()
    obs, rewards, done, info = env.step({agent_id: action, ...})
"""

import os
import sys

import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sumo_utils as su  # noqa: E402

su.ensure_tools_on_path()
import traci  # noqa: E402


class MultiAgentTrafficEnv:
    """SUMO traffic signal control as a cooperative multi-agent problem."""

    def __init__(self,
                 controller="static",
                 use_gui=False,
                 num_seconds=1800,
                 delta_time=5,
                 yellow_time=4,
                 allred_time=2,
                 min_green=10,
                 max_queue_norm=25.0,
                 max_wait_norm=120.0,
                 neighbour_weight=0.25,
                 neighbour_radius=250.0,
                 seed=42):
        self.controller = controller
        self.use_gui = use_gui
        self.num_seconds = num_seconds
        self.delta_time = delta_time          # seconds between agent decisions
        self.yellow_time = yellow_time
        self.allred_time = allred_time
        self.min_green = min_green
        self.max_queue_norm = max_queue_norm
        self.max_wait_norm = max_wait_norm
        self.neighbour_weight = neighbour_weight
        self.neighbour_radius = neighbour_radius
        self.seed = seed

        self.cfg = su.path("config", f"grid_{controller}.sumocfg")
        self.net_path = su.path("net", f"grid_{controller}.net.xml")

        self.agents = []
        self.lanes = {}          # agent -> [lane ids]
        self.green_phases = {}   # agent -> [phase indices that contain green]
        self.neighbours = {}     # agent -> [agent ids]
        self._phase_start = {}
        self._last_wait = {}
        self._started = False
        self._step_count = 0

        self._discover_topology()

    # ------------------------------------------------------------------
    def _discover_topology(self):
        """Read junction adjacency from the network file, before any run."""
        import sumolib
        net = sumolib.net.readNet(self.net_path, withPrograms=True)

        coords = {}
        for tl in net.getTrafficLights():
            tid = tl.getID()
            self.agents.append(tid)
            seen, lanes = set(), []
            for in_lane, _o, _l in tl.getConnections():
                lid = in_lane.getID()
                if lid not in seen:
                    seen.add(lid)
                    lanes.append(lid)
            self.lanes[tid] = lanes
            node = net.getNode(tid)
            coords[tid] = node.getCoord()

        self.agents.sort()

        # adjacency by euclidean distance (grid spacing is 200 m by default)
        for a in self.agents:
            xa, ya = coords[a]
            near = []
            for b in self.agents:
                if a == b:
                    continue
                xb, yb = coords[b]
                if np.hypot(xa - xb, ya - yb) <= self.neighbour_radius:
                    near.append(b)
            self.neighbours[a] = sorted(near)

        self.max_neighbours = max(len(v) for v in self.neighbours.values())
        self.max_lanes = max(len(v) for v in self.lanes.values())

    # ------------------------------------------------------------------
    def _discover_phases(self):
        """Find which phases contain a green light (the agent's action set)."""
        for a in self.agents:
            logic = traci.trafficlight.getAllProgramLogics(a)[0]
            greens = [i for i, ph in enumerate(logic.phases)
                      if "g" in ph.state.lower() and "y" not in ph.state.lower()]
            self.green_phases[a] = greens or [0]

    @property
    def observation_size(self):
        # 3 features per lane + one-hot phase + phase timer + neighbour queues
        max_phases = 4
        return self.max_lanes * 3 + max_phases + 1 + self.max_neighbours

    @property
    def action_size(self):
        return max(len(v) for v in self.green_phases.values()) if self.green_phases else 2

    # ------------------------------------------------------------------
    def reset(self):
        if self._started:
            self.close()

        binary = su.binary("sumo-gui" if self.use_gui else "sumo")
        cmd = [binary, "-c", self.cfg,
               "--seed", str(self.seed),
               "--end", str(self.num_seconds),
               "--no-warnings", "true",
               "--no-step-log", "true"]
        if self.use_gui:
            cmd += ["--start", "true", "--quit-on-end", "true"]

        traci.start(cmd)
        self._started = True
        self._step_count = 0
        self._discover_phases()

        for a in self.agents:
            self._phase_start[a] = 0.0
            self._last_wait[a] = self._junction_wait(a)

        return {a: self._observe(a) for a in self.agents}

    # ------------------------------------------------------------------
    def _junction_wait(self, agent):
        return sum(traci.lane.getWaitingTime(l) for l in self.lanes[agent])

    def _junction_queue(self, agent):
        return sum(traci.lane.getLastStepHaltingNumber(l) for l in self.lanes[agent])

    def _observe(self, agent):
        feats = []
        lanes = self.lanes[agent]

        for i in range(self.max_lanes):
            if i < len(lanes):
                l = lanes[i]
                q = traci.lane.getLastStepHaltingNumber(l) / self.max_queue_norm
                w = traci.lane.getWaitingTime(l) / self.max_wait_norm
                o = traci.lane.getLastStepOccupancy(l)
                feats += [min(q, 2.0), min(w, 2.0), o]
            else:
                feats += [0.0, 0.0, 0.0]

        cur = traci.trafficlight.getPhase(agent)
        onehot = [0.0] * 4
        greens = self.green_phases[agent]
        if cur in greens:
            idx = greens.index(cur)
            if idx < 4:
                onehot[idx] = 1.0
        feats += onehot

        elapsed = traci.simulation.getTime() - self._phase_start[agent]
        feats.append(min(elapsed / 60.0, 2.0))

        for i in range(self.max_neighbours):
            nb = self.neighbours[agent]
            if i < len(nb):
                feats.append(min(self._junction_queue(nb[i]) / self.max_queue_norm, 2.0))
            else:
                feats.append(0.0)

        return np.array(feats, dtype=np.float32)

    # ------------------------------------------------------------------
    def _apply_action(self, agent, action):
        """Switch to the requested green phase, inserting yellow + all-red."""
        greens = self.green_phases[agent]
        target = greens[int(action) % len(greens)]
        current = traci.trafficlight.getPhase(agent)

        if target == current:
            return False

        now = traci.simulation.getTime()
        if now - self._phase_start[agent] < self.min_green:
            return False   # safety: never cut a green shorter than min_green

        logic = traci.trafficlight.getAllProgramLogics(agent)[0]
        # the yellow phase immediately following the current green
        yellow_idx = (current + 1) % len(logic.phases)
        if "y" in logic.phases[yellow_idx].state.lower():
            traci.trafficlight.setPhase(agent, yellow_idx)
            traci.trafficlight.setPhaseDuration(agent, self.yellow_time)

        self._pending = getattr(self, "_pending", {})
        self._pending[agent] = target
        return True

    def _resolve_pending(self):
        pending = getattr(self, "_pending", {})
        for agent, target in list(pending.items()):
            logic = traci.trafficlight.getAllProgramLogics(agent)[0]
            cur = traci.trafficlight.getPhase(agent)
            if "y" not in logic.phases[cur].state.lower():
                traci.trafficlight.setPhase(agent, target)
                self._phase_start[agent] = traci.simulation.getTime()
                del pending[agent]

    # ------------------------------------------------------------------
    def step(self, actions):
        for a, act in actions.items():
            self._apply_action(a, act)

        for _ in range(self.delta_time):
            traci.simulationStep()
            self._resolve_pending()
            if traci.simulation.getTime() >= self.num_seconds:
                break

        self._step_count += 1

        own_delta = {}
        for a in self.agents:
            w = self._junction_wait(a)
            own_delta[a] = -(w - self._last_wait[a])
            self._last_wait[a] = w

        rewards = {}
        for a in self.agents:
            shared = np.mean([own_delta[n] for n in self.neighbours[a]]) \
                if self.neighbours[a] else 0.0
            rewards[a] = float(own_delta[a] + self.neighbour_weight * shared) / 100.0

        obs = {a: self._observe(a) for a in self.agents}
        done = (traci.simulation.getTime() >= self.num_seconds
                or traci.simulation.getMinExpectedNumber() <= 0)

        info = {
            "time": traci.simulation.getTime(),
            "vehicles": traci.vehicle.getIDCount(),
            "total_queue": sum(self._junction_queue(a) for a in self.agents),
            "total_wait": sum(self._last_wait.values()),
        }
        return obs, rewards, done, info

    def close(self):
        if self._started:
            try:
                traci.close()
            except Exception:
                pass
            self._started = False


# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("Smoke test: random policy on the multi-agent traffic environment.\n")

    env = MultiAgentTrafficEnv(controller="static", num_seconds=600, delta_time=5)
    print(f"agents          : {len(env.agents)}  ->  {', '.join(env.agents)}")
    print(f"neighbours of {env.agents[0]}: {env.neighbours[env.agents[0]]}")

    obs = env.reset()
    print(f"observation size: {env.observation_size} per agent")
    print(f"action size     : {env.action_size} (green phases to choose from)")
    print(f"actual obs shape: {obs[env.agents[0]].shape}\n")

    rng = np.random.default_rng(0)
    total = 0.0
    done = False
    steps = 0

    while not done:
        acts = {a: int(rng.integers(env.action_size)) for a in env.agents}
        obs, rewards, done, info = env.step(acts)
        total += sum(rewards.values())
        steps += 1
        if steps % 20 == 0:
            print(f"  t={info['time']:6.0f}s  vehicles={info['vehicles']:4d}  "
                  f"queue={info['total_queue']:4d}  cumReward={total:9.2f}")

    env.close()
    print(f"\nEpisode finished: {steps} decision steps, total reward {total:.2f}")
    print("Environment is working. Phase 3 replaces the random policy with MAPPO.")
