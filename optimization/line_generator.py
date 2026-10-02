# optimization/line_generator.py 
"""
End-to-end assembly line balancing engine.

Phase 1  -> PacingCalculator   : takt time / CT_ideal / CT_tentative / CT_floor / CT_target
Phase 2  -> LineGenerator      : graph-driven, successor-first station packing
Phase 3  -> LineEvaluator      : station CTs, bottleneck, throughput, headcount, capacity flag

Two ways to drive it:
  1) target_units + shift_duration + max_workers  -> normal takt-based design
  2) max_workers only (target_units / shift_duration omitted or 0)
     -> "worker-only" mode: takt_time = 0, CT_target is driven purely by CT_ideal
        (work content / available workers).

LineBalancer.run() now generates one or more
CANDIDATE line layouts (different random station-packing orders that all
respect the same CT_target), so callers can compare several feasible line
designs instead of a single deterministic one.
"""

import heapq
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    cycle_time: float                      # raw duration t_i
    task_type: str                         # "manual" or "machine"
    min_operators: int = 1
    max_operators: int = 1
    predecessors: List[str] = field(default_factory=list)
    successors: List[str] = field(default_factory=list)   # filled in automatically


@dataclass
class Station:
    tasks: List[str] = field(default_factory=list)
    p_factors: Dict[str, int] = field(default_factory=dict)   # task_id -> workers used
    station_time: float = 0.0                                  # sum of effective ti

    @property
    def required_workers(self) -> int:
        """A station's headcount is the MAX operator requirement of any single
        task inside it (workers rotate across tasks within the station)."""
        return max(self.p_factors.values()) if self.p_factors else 0


# ---------------------------------------------------------------------------
# Graph manager: precedence tracking + readiness pool
# ---------------------------------------------------------------------------

class GraphManager:
    def __init__(self, tasks: List[Task]):
        self.tasks: Dict[str, Task] = {t.id: t for t in tasks} #This converts the list of tasks into a dictionary where the task ID is the key so we can search using the key directly 
        self._build_successors()
        self.remaining_preds: Dict[str, set] = { t.id: set(t.predecessors) for t in tasks} #For each task, keep a list/set of the tasks that still need to be completed before it can start.
        self.done: set = set()  #This stores the tasks that have already been completed/assigned
        

    def _build_successors(self) -> None:
        #1) we initialize every task's successors as an empty list:
        for t in self.tasks.values():
            t.successors = []
        for t in self.tasks.values():
            for p in t.predecessors:
                if p in self.tasks:
                    self.tasks[p].successors.append(t.id)

    #finds the tasks that are ready to be done at the beginning   
    def initial_free_tasks(self) -> List[str]:
        return [tid for tid, preds in self.remaining_preds.items() if not preds] #set()=empty  → not preds = True

    #checks whether a specific task is free/ready to be selected
    def is_free(self, task_id: str) -> bool:
        #1) check that The task has not already been completed.
        #2)then checks whether the task has no remaining predecessors.
        return task_id not in self.done and not self.remaining_preds[task_id]

    def mark_done(self, task_id: str) -> List[str]:
        """Marks task complete, releases its edges, returns newly-freed successors."""
        self.done.add(task_id)
        newly_free = []
        for succ_id in self.tasks[task_id].successors:
            preds = self.remaining_preds.get(succ_id)
            if preds and task_id in preds:  #if pred is not empty =true
                preds.discard(task_id)
                if not preds and succ_id not in self.done: #if preds is empty=true
                    newly_free.append(succ_id)
        return newly_free

    def all_done(self) -> bool:
        return len(self.done) == len(self.tasks)



# ---------------------------------------------------------------------------
# Phase 1: Pacing calculator
# ---------------------------------------------------------------------------

class PacingCalculator:
    def __init__(self, tasks: List[Task], max_workers: Optional[int] = None,
                 target_units: Optional[float] = None,
                 shift_duration: Optional[float] = None):
        self.tasks = tasks
        self.max_workers = max_workers
        self.target_units = target_units
        self.shift_duration = shift_duration

    def compute(self) -> Dict[str, Any]:
        sum_t = sum(t.cycle_time for t in self.tasks)
        ct_ideal = sum_t / self.max_workers if self.max_workers else float("inf") #Ideal average cycle time per station when work is evenly distributed among workers.

        # --- worker-only mode: no target_units / shift_duration given -----
        worker_only_mode = not self.target_units or not self.shift_duration #not 100 = False    not None= True
        if worker_only_mode: #if worker_only_mode = true mean contain none or 0 
            takt_time = 0.0
            ct_tentative = ct_ideal if ct_ideal != float("inf") else 0.0 #if there is no worker and no takt time ct_tentive =0 and we balance based on the highest value 
        else:
            takt_time = self.shift_duration / self.target_units
            ct_tentative = min(takt_time, ct_ideal) if ct_ideal != float("inf") else takt_time

        ct_floor = 0.0

        # --- lazy max-heap t_min inspection ---------------------------------
        heap = [(-t.cycle_time, t.id) for t in self.tasks]
        heapq.heapify(heap)
        by_id = {t.id: t for t in self.tasks}

        while heap:
            neg_t, tid = heapq.heappop(heap)
            t_max = -neg_t
            task = by_id[tid]

            if t_max <= ct_tentative:
                break  # everything remaining already fits -> stop checking

            if task.task_type.lower() == "machine":
                ct_floor = max(ct_floor, t_max)
                break
            else:
                w_max = max(task.max_operators, 1)
                t_min = t_max / w_max
                if t_min > ct_tentative:
                    ct_floor = max(ct_floor, t_min)

        ct_target = max(ct_tentative, ct_floor)

        return {
            "worker_only_mode": worker_only_mode,
            "takt_time": takt_time,
            "ct_ideal": ct_ideal if ct_ideal != float("inf") else None,
            "ct_tentative": ct_tentative,
            "ct_floor": ct_floor,
            "ct_target": ct_target,   # unrounded - LineGenerator uses this exact value
            "display": {
                "takt_time": round(takt_time, 4),
                "ct_ideal": round(ct_ideal, 4) if ct_ideal != float("inf") else None,
                "ct_tentative": round(ct_tentative, 4),
                "ct_floor": round(ct_floor, 4),
                "ct_target": round(ct_target, 4),
            }
        }


# ---------------------------------------------------------------------------
# Phase 2: Line generator -- graph-driven station packing
# ---------------------------------------------------------------------------

class LineGenerator:
    def __init__(self, tasks: List[Task], ct_target: float, seed: Optional[int] = None):
        self.tasks_by_id = {t.id: t for t in tasks}
        self.ct_target = ct_target
        self.graph = GraphManager(tasks)
        self.rng = random.Random(seed)

    # ---- P-factor: effective duration + workers required ------------------
    def evaluate_task_p_factor(self, task: Task, ct_target: float) -> Tuple[float, int]:
        """
        - Machine: fixed duration, fixed operators (min_operators). Cannot be accelerated.
        - Manual : scale workers up (up to max_operators) until eff. duration <= ct_target.
        """
        duration = task.cycle_time
        if task.task_type.lower() == "machine":
            return duration, task.min_operators

        workers = max(task.min_operators, 1)
        while (duration / workers) > ct_target and workers < task.max_operators:
            workers += 1
        effective_duration = duration / workers
        return effective_duration, workers

    # ---- try to fit one task into the current station ----------------------
    def _try_add_task(self, station: Station, task_id: str) -> bool:
        task = self.tasks_by_id[task_id]
        eff_dur, p_factor = self.evaluate_task_p_factor(task, self.ct_target)

        # Station Time + Effective Task Time <= CT_target (epsilon for float safety)
        if station.station_time + eff_dur <= self.ct_target + 1e-9:
            station.tasks.append(task_id)
            station.p_factors[task_id] = p_factor
            station.station_time += eff_dur
            return True
        return False

    def generate(self) -> List[Station]:
        stations: List[Station] = []
        free_pool: List[str] = self.graph.initial_free_tasks()

        while free_pool or not self.graph.all_done():
            if not free_pool:
                raise RuntimeError(
                    "Graph deadlock: unassigned tasks remain but none are free "
                    "(check for a cycle or a missing/typo'd predecessor id)."
                )

            station = Station() #

            # Step 3: random first task from the global free pool
            first_id = self.rng.choice(free_pool)
            free_pool.remove(first_id)

            # Step 4/5: place first task - force it in even if it alone
            # exceeds ct_target, since an empty station has nothing to
            # compare against and the task has to go somewhere
            added = self._try_add_task(station, first_id)
            if not added:
                task = self.tasks_by_id[first_id]
                eff_dur, p_factor = self.evaluate_task_p_factor(task, self.ct_target)
                station.tasks.append(first_id)
                station.p_factors[first_id] = p_factor
                station.station_time += eff_dur

            free_pool.extend(self.graph.mark_done(first_id))

            last_added = first_id

            # Step 7: pack CONNECTED tasks only (successor-first, dynamic worker scaling)
            while True:
                added_this_round = False
                for succ_id in self.tasks_by_id[last_added].successors:
                    if succ_id in free_pool and self.graph.is_free(succ_id):
                        if self._try_add_task(station, succ_id):
                            free_pool.remove(succ_id)
                            free_pool.extend(self.graph.mark_done(succ_id))
                            last_added = succ_id
                            added_this_round = True
                            break  # re-scan successors of the NEW last_added task
                        # else: doesn't fit even after max-worker scaling (or
                        # it's a fixed machine task) -> try the next successor

                if not added_this_round:
                    break  # no connected free task fits -> close the station

            # Step 8: lock & save the completed station
            stations.append(station)

        return stations


# ---------------------------------------------------------------------------
# Phase 3: Line evaluator
# ---------------------------------------------------------------------------

class LineEvaluator:
    def __init__(self, stations: List[Station],  max_workers: Optional[int] ):
        self.stations = stations
        self.max_workers = max_workers

    def evaluate(self) -> Dict[str, Any]:
        station_reports = []
        station_cts = []

        for idx, st in enumerate(self.stations, start=1):
            station_ct = st.station_time  # sum_i (t_i / w_i), 
            station_cts.append(station_ct)
            station_reports.append({
                "station_index": idx,
                "tasks": list(st.tasks),
                "task_workers": dict(st.p_factors),
                "required_workers": st.required_workers,
                "station_cycle_time": round(station_ct, 4),
            })

        actual_ct = max(station_cts) if station_cts else 0.0  #	The slowest station's time. bottleneck
      
        # Total line headcount = sum of each station's own requirement
        assigned_workers = sum(s["required_workers"] for s in station_reports)

        exceeds_capacity = bool(
            self.max_workers is not None and assigned_workers > self.max_workers
        )
        additional_workers_needed = (
            max(0, assigned_workers - self.max_workers) if self.max_workers is not None else 0
        )
        

        return {
            "stations": station_reports,
            "num_stations": len(station_reports),
            "actual_ct": round(actual_ct, 4),
            "assigned_workers": assigned_workers,
            "available_workers": self.max_workers,
            "exceeds_available_workers": exceeds_capacity,
            "additional_workers_needed": additional_workers_needed,
           
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class LineBalancer:
    def __init__(self, tasks: List[Task], max_workers: Optional[int] = None,  target_units: Optional[float] = None,  shift_duration: Optional[float] = None, seed: Optional[int] = None):
        self.tasks = tasks
        self.max_workers = max_workers
        self.target_units = target_units
        self.shift_duration = shift_duration
        self.seed = seed

    def run(self, num_candidates: int = 5, max_attempts_factor: int = 20) -> Dict[str, Any]: #safety limit on how many times we try to find new layouts.
        """
        Runs Phase 1 once (pacing is independent of station packing order),
        then runs Phase 2 + Phase 3 repeatedly with different random seeds to
        produce up to `num_candidates` distinct, feasible line layouts, all
        built against the same CT_target.

        Duplicate layouts (identical station/task groupings, just re-run by
        chance) are skipped so candidates are meaningfully different. If the
        task graph is small/rigid, fewer than `num_candidates` unique layouts
        may come back -- that's expected, not an error.

        Returns:
            {
                "candidates": [
                    {
                        "candidate_id": 1,
                        "stations": [
                            {
                                "station_id": 1,
                                "tasks": ["T1", "T2"],
                                "p_factors": {"T1": 1, "T2": 2},
                                "station_time": 85.0
                                "required_workers": 2
                            },
                            ...
                        ],
                        "num_stations": 4,
                        "total_workers": 6,
                        "deterministic_bottleneck": 95.0
                          "available_workers": 8,
                        "exceeds_available_workers": False,
                        "additional_workers_needed": 0
                    },
                    ...
                ]
            }
        """
        pacing = PacingCalculator(  self.tasks, self.max_workers, self.target_units, self.shift_duration ).compute()

        ct_target = pacing["ct_target"]

        # Base seed: respect an explicit seed if given (still varied per attempt
        # so we don't just regenerate the same layout `num_candidates` times).
        base_seed = self.seed if self.seed is not None else random.randrange(1_000_000)

        candidates: List[Dict[str, Any]] = []
        seen_signatures = set()
        max_attempts = max(num_candidates * max_attempts_factor, num_candidates)

        for attempt in range(max_attempts):
            if len(candidates) >= num_candidates:
                break

            trial_seed = base_seed + attempt
            generator = LineGenerator(self.tasks, ct_target, seed=trial_seed)
            stations = generator.generate()

            # A layout's identity is which tasks land in which station, in order.
            signature = tuple(tuple(st.tasks) for st in stations)
            if signature in seen_signatures:
                continue  # duplicate, throw it away
            seen_signatures.add(signature) # new layout, remember it and keep it

            metrics = LineEvaluator(stations,  self.max_workers).evaluate()

            candidates.append({
                "candidate_id": len(candidates) + 1,
                "stations": [
                    {
                        "station_id": s["station_index"],
                        "tasks": s["tasks"],
                        "p_factors": s["task_workers"],
                        "station_time": s["station_cycle_time"],
                        "required_workers": s["required_workers"],
                    }
                    for s in metrics["stations"]
                ],
                "num_stations": metrics["num_stations"],
                "total_workers": metrics["assigned_workers"],
                "deterministic_bottleneck": metrics["actual_ct"],
                "available_workers": metrics["available_workers"],
                "exceeds_available_workers": metrics["exceeds_available_workers"],
                "additional_workers_needed": metrics["additional_workers_needed"],

            })


        return { "candidates": candidates}