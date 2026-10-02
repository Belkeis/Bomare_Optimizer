"""
engine/delta_t_simulator.py
----------------------------
Next-Event (delta-t) discrete simulation engine for the TV factory line.

Implements the exact logic from the system spec:
  - Converging DAG readiness rule:  Ready(S) <=> for all d in Relation[S], Available[d] >= Quantity[d]
  - Next-event jump: dt = min(timer) among READY stations only (frozen stations are skipped)
  - On timer <= 0: consume parent stock, produce +1 unit, reset timer with lognormal noise
  - Continuous time-weighted WIP integral
  - Termination: shift_duration reached, or resources exhausted with nothing ready

No pandas is used in the hot loop -- the pre-compiled station table is flattened
into small numpy arrays once, and the event loop runs on plain arrays/indices for speed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

# --------------------------------------------------------------------------- #
# Defaults (mirror config.py constants; override via run_simulation kwargs)
# --------------------------------------------------------------------------- #
DEFAULT_SHIFT_DURATION = 480.0   # SECONDS
DEFAULT_NOISE_SIGMA = 0.05       # lognormal(mu=0, sigma) cycle-time jitter
EPS = 1e-9                       # float tolerance for "timer reached zero"


# --------------------------------------------------------------------------- #
# Compiled station table: flat numpy arrays indexed 0..N-1 (internal indices),
# separate from the external "group" id used in the input rows / output dict.
# --------------------------------------------------------------------------- #
@dataclass
class _CompiledStations:
    group_ids: np.ndarray          # external group id per internal index
    is_task: np.ndarray            # bool, True for 'Task' rows, False for 'Resource'
    cycle_time: np.ndarray         # float
    p_factor: np.ndarray           # int, operators assigned
    quantity: np.ndarray           # int, this row's own consumption quantity
    available: np.ndarray          # int/float, mutable state
    timer: np.ndarray              # float, mutable state (-1 for Resource rows)
    relations: List[np.ndarray]    # per-row array of parent internal indices
    task_idx: np.ndarray           # internal indices where is_task
    resource_idx: np.ndarray       # internal indices where not is_task
    sink_idx: int                  # internal index of the final (output) station
    wip_task_idx: np.ndarray       # task_idx EXCLUDING sink_idx -- see _current_wip


def _compile_stations(stations: Sequence[Dict[str, Any]]) -> _CompiledStations:
    """Flatten the pre-compiled station table (list of dict rows) into arrays."""
    n = len(stations)
    id_to_idx = {row["group"]: i for i, row in enumerate(stations)}

    is_task = np.array([row["Type"] == "Task" for row in stations], dtype=bool)
    cycle_time = np.array([float(row.get("CycleTime", 0.0)) for row in stations])
    p_factor = np.array([int(row.get("P_factor", 0)) for row in stations])
    quantity = np.array([int(row.get("Quantity", 0)) for row in stations])
    available = np.array([float(row.get("Available", 0)) for row in stations])
    group_ids = np.array([row["group"] for row in stations])

    relations: List[np.ndarray] = [
        np.array([id_to_idx[d] for d in row.get("Relation", [])], dtype=int)
        for row in stations
    ]

    task_idx = np.where(is_task)[0]
    resource_idx = np.where(~is_task)[0]

    # Sink/output station = a Task row that is never listed as a parent (Relation entry)
    # of any other row, i.e. nothing downstream consumes it.
    referenced = {d for rel in relations for d in rel}
    sink_candidates = [i for i in task_idx if i not in referenced]
    sink_idx = sink_candidates[-1] if sink_candidates else (task_idx[-1] if len(task_idx) else -1)

    timer = np.full(n, -1.0)  # Resource rows stay -1 permanently

    # WIP excludes the sink station's own output buffer: those are FINISHED
    # goods (nothing downstream ever consumes them), not work still in progress.
    # Without this exclusion the sink's Available grows all shift and dominates
    # avg_wip (e.g. ~240 for a single always-busy 1-min station over 480 min,
    # vs. a true in-process WIP of ~1).
    wip_task_idx = task_idx[task_idx != sink_idx] if sink_idx is not None and sink_idx >= 0 else task_idx

    return _CompiledStations(
        group_ids=group_ids,
        is_task=is_task,
        cycle_time=cycle_time,
        p_factor=p_factor,
        quantity=quantity,
        available=available,
        timer=timer,
        relations=relations,
        task_idx=task_idx,
        resource_idx=resource_idx,
        sink_idx=sink_idx,
        wip_task_idx=wip_task_idx,
    )


def _ready_mask(c: _CompiledStations) -> np.ndarray:
    """Ready(S) <=> Type==Task AND for all d in Relation[S]: Available[d] >= Quantity[d]."""
    ready = np.zeros(len(c.is_task), dtype=bool)
    for i in c.task_idx:
        ok = True
        for d in c.relations[i]:
            if c.available[d] < c.quantity[d]:
                ok = False
                break
        ready[i] = ok
    return ready


def _current_wip(c: _CompiledStations, ready_mask: np.ndarray) -> float:
    """WIP(t) = sum(Available over in-process Task stations, EXCLUDING the sink's
    finished-goods buffer) + count(READY stations)."""
    return float(c.available[c.wip_task_idx].sum() + ready_mask.sum())


def _draw_cycle_timer(c: _CompiledStations, i: int,
                       noise_sigma: float, rng: np.random.Generator) -> float:
    """
    (CycleTime + transition) * Lognormal(mu=-sigma^2/2, sigma)
    Mean-preserving jitter (~sigma as coefficient of variation).
    """
    transition = rng.uniform(0.5*60, 2.0*60)
    base = c.cycle_time[i] + transition
    if noise_sigma <= 0:
        return base
    factor = rng.lognormal(mean=-0.5 * noise_sigma ** 2, sigma=noise_sigma)
    return max(1e-6, base * factor)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_simulation(
    stations: Sequence[Dict[str, Any]],
    shift_duration: float = DEFAULT_SHIFT_DURATION,
    noise_sigma: float = DEFAULT_NOISE_SIGMA,
    seed: Optional[int] = None,
    max_events: int = 2_000_000,
) -> Dict[str, Any]:
    """
    Run the next-event stochastic simulation over a pre-compiled station table.

    Parameters
    ----------
    stations : list of dict rows, each with keys:
        group, Relation (list[int] of group ids), Type ('Task'|'Resource'),
        Id (list[int]), Quantity (int), CycleTime (float), P_factor (int),
        Available (int, initial stock/output; 0 for most Task rows).
    shift_duration : total available shift time in SECONDS.
    noise_sigma : sigma of the Lognormal cycle-time jitter.
    seed : optional RNG seed for reproducibility (independent stream per call --
        important when this runs inside a metaheuristic search evaluating many
        candidates, so results aren't correlated via shared global RNG state).
    max_events : safety cap on event-loop iterations to guard against numerical
        edge cases (e.g. repeated near-zero timers) hanging the search.

    Returns
    -------
    dict matching the spec's output schema (throughput_units, total_workers,
    avg_wip, line_efficiency_pct, simulation_time_mins, station_metrics).
    """
    c = _compile_stations(stations)
    rng = np.random.default_rng(seed)
    n = len(c.is_task)

    # Initialize task timers; resource rows keep timer = -1.
    for i in c.task_idx:
        c.timer[i] = _draw_cycle_timer(c, i, noise_sigma, rng)

    global_clock = 0.0
    wip_weighted = 0.0
    busy_time = np.zeros(n)
    starved_time = np.zeros(n)
    units_produced = np.zeros(n, dtype=int)

    event_count = 0
    event_cap_reached = False
    while True:
        event_count += 1
        if event_count > max_events:
            event_cap_reached = True  # surfaced in the result -- never fails silently
            break

        ready = _ready_mask(c)
        ready_idx = np.where(ready)[0]

        # Termination: nothing ready -> resource lockout or deadlock. Credit the
        # remaining shift time to WIP/starved accounting before stopping, rather
        # than silently dropping it (this matters for avg_wip / line_efficiency_pct
        # if the line stalls before shift_duration is reached).
        if ready_idx.size == 0:
            remaining = shift_duration - global_clock
            if remaining > 0:
                wip_weighted += _current_wip(c, ready) * remaining
                starved_time[c.task_idx] += remaining
                global_clock = shift_duration
            break

        dt = float(c.timer[ready_idx].min())
        if dt <= 0:
            dt = EPS  # defensive: avoid a zero-time spin on numerical edge cases
        remaining = shift_duration - global_clock

        # Strict '>' (with a float-safety epsilon): a completion landing exactly
        # at shift_duration must still be processed, or it's silently dropped
        # (e.g. 479 units instead of 480 in a deterministic 1-min-cycle test).
        if dt > remaining + EPS:
            # Partial final interval: record WIP/busy/starved up to shift end, then stop.
            wip = _current_wip(c, ready)
            wip_weighted += wip * remaining
            busy_time[ready_idx] += remaining
            frozen_tasks = np.array([i for i in c.task_idx if not ready[i]], dtype=int)
            if frozen_tasks.size:
                starved_time[frozen_tasks] += remaining
            global_clock = shift_duration
            break

        # --- Full event step ---
        wip = _current_wip(c, ready)
        wip_weighted += wip * dt
        busy_time[ready_idx] += dt
        frozen_tasks = np.array([i for i in c.task_idx if not ready[i]], dtype=int)
        if frozen_tasks.size:
            starved_time[frozen_tasks] += dt

        global_clock += dt
        c.timer[ready_idx] -= dt

        completed = ready_idx[c.timer[ready_idx] <= EPS]
        for s in completed:
            for d in c.relations[s]:
                c.available[d] -= c.quantity[d]
            c.available[s] += 1
            units_produced[s] += 1
            c.timer[s] = _draw_cycle_timer(c, s, noise_sigma, rng)

    # --------------------------- Metrics assembly --------------------------- #
    final_clock = global_clock
    avg_wip = wip_weighted / final_clock if final_clock > 0 else 0.0
    total_workers = int(c.p_factor[c.task_idx].sum())

    n_task = len(c.task_idx)
    line_efficiency_pct = (
        100.0 * busy_time[c.task_idx].sum() / (final_clock * n_task)
        if final_clock > 0 and n_task > 0
        else 0.0
    )

    throughput_units = (
        int(c.available[c.sink_idx]) if c.sink_idx is not None and c.sink_idx >= 0 else 0
    )

    station_metrics: Dict[Any, Dict[str, Any]] = {}
    for i in c.task_idx:
        gid = c.group_ids[i].item() if hasattr(c.group_ids[i], "item") else c.group_ids[i]
        station_metrics[gid] = {
            "busy_pct": (busy_time[i] / final_clock * 100.0) if final_clock > 0 else 0.0,
            "starved_pct": (starved_time[i] / final_clock * 100.0) if final_clock > 0 else 0.0,
            "units_produced": int(units_produced[i]),
        }

    return {
        "throughput_units": throughput_units,
        "total_workers": total_workers,
        "avg_wip": float(avg_wip),
        "line_efficiency_pct": float(line_efficiency_pct),
        "simulation_time_mins": float(final_clock),
        "station_metrics": station_metrics,
        "event_cap_reached": event_cap_reached,
    }


# --------------------------------------------------------------------------- #
# Demo / smoke test (run directly: python delta_t_simulator.py)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal converging line: two raw-material resources feed Station A and
    # Station B, which both feed the final assembly Station C.
    demo_stations = [
        {"group": 0, "Relation": [], "Type": "Resource", "MemberTasks": [], "Quantity": 0,
         "CycleTime": 0.0, "P_factor": 0, "Available": 1000},   # raw material for A
        {"group": 1, "Relation": [], "Type": "Resource", "MemberTasks": [], "Quantity": 0,
         "CycleTime": 0.0, "P_factor": 0, "Available": 1000},   # raw material for B
        {"group": 2, "Relation": [0], "Type": "Task", "MemberTasks": [1, 2], "Quantity": 1,
         "CycleTime": 2.0, "P_factor": 2, "Available": 0},      # Station A
        {"group": 3, "Relation": [1], "Type": "Task", "MemberTasks": [3], "Quantity": 1,
         "CycleTime": 3.0, "P_factor": 1, "Available": 0},      # Station B
        {"group": 4, "Relation": [2, 3], "Type": "Task", "MemberTasks": [4, 5], "Quantity": 1,
         "CycleTime": 1.5, "P_factor": 2, "Available": 0},      # Station C (final assembly)
    ]

    result = run_simulation(demo_stations, shift_duration=480.0, seed=42)
    import json
    print(json.dumps(result, indent=2, default=str))