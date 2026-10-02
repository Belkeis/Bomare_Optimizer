# optimization/search_runner.py
"""
Simulated-annealing search over station layouts for the TV factory line.

Pipeline
--------
    raw table --setup()--> Problem
    Problem   --make_seeds()--> validated starting Layouts   (via LineBalancer)
    Layout    --4 moves--> neighbour Layout                   (crew / split / exit-forward / merge)
    Layout    --compile_for_simulator()--> station table --run_simulation()--> metrics
    metrics   --fitness--> accept / reject (Metropolis)  --> best layout + report

A Layout is only a partition of the raw tasks into stations plus a crew size per
station. Station time, the station graph and the simulator table are always
derived from it, never edited by hand.

Usage
-----
    from optimization.search_runner import SearchConfig, run_search, format_report
    result = run_search(raw_df, SearchConfig(shift_duration=480, unit_price=50,
                                             worker_salary=100, wip_holding_cost=2))
    print(format_report(result))
"""

from __future__ import annotations

import ast
import math
import random
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

import networkx as nx
import numpy as np
import pandas as pd

# Allow `python optimization/search_runner.py` as well as `python -m optimization.search_runner`.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.delta_t_simulator import run_simulation          # noqa: E402
from engine.graph_manager import RawTableGraph               # noqa: E402
from optimization.line_generator import LineBalancer         # noqa: E402
from optimization.line_generator import Task as GenTask      # noqa: E402


# =========================================================================== #
# 0. Config
# =========================================================================== #
@dataclass
class SearchConfig:
    # problem
    shift_duration: float
    # money
    unit_price: float
    worker_salary: float
    wip_holding_cost: float
    # optional fitness guards
    target_units: Optional[float] = None
    available_workers: Optional[int] = None
    # search
    n_iterations: int = 500
    start_temp: float = 100.0
    cooling_rate: float = 0.99
    n_replications: int = 3
    no_improve_limit: int = 100
    master_seed: Optional[int] = 0
    num_seed_candidates: int = 5
    # reporting / safety
    top_k: int = 5
    debug_validate: bool = False   # re-validate every neighbour layout (slower, catches move bugs)

    def __post_init__(self) -> None:
        if self.shift_duration <= 0:
            raise ValueError("shift_duration must be > 0")
        if self.n_replications < 1:
            raise ValueError("n_replications must be >= 1")
        if not (0 < self.cooling_rate <= 1):
            raise ValueError("cooling_rate must be in (0, 1]")
        if self.master_seed is None:
            self.master_seed = random.randrange(2 ** 31)


# =========================================================================== #
# 1. Setup: raw table -> Problem
# =========================================================================== #
_COLUMN_ALIASES = {
    "id": "Id", "type": "Type",
    "relation": "Relation", "relations": "Relation", "predecessors": "Relation",
    "quantity": "Quantity", "available": "Available",
    "cycletime": "CycleTime",
    "tasktype": "TaskType",
    "minoperators": "MinOperators", "minoperator": "MinOperators", "minworkers": "MinOperators",
    "maxoperators": "MaxOperators", "maxoperator": "MaxOperators", "maxworkers": "MaxOperators",
}


def _norm_id(v: Any) -> Any:
    """Canonical id: integral numbers -> int, digit strings -> int, other strings stripped."""
    if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, (float, np.floating)):
        if math.isnan(v):
            return None
        return int(v) if float(v).is_integer() else float(v)
    if isinstance(v, str):
        s = v.strip().strip("'\"")
        if s == "":
            return None
        return int(s) if re.fullmatch(r"[+-]?\d+", s) else s
    return v


def _parse_relation(cell: Any) -> List[Any]:
    """Relation cell (list, '[3, 4]', '3', 3, NaN, ...) -> list of canonical predecessor ids."""
    if isinstance(cell, (list, tuple, set, frozenset, np.ndarray)):
        items = list(cell)
    elif isinstance(cell, str):
        s = cell.strip()
        if s in ("", "[]", "()"):
            items = []
        else:
            try:
                val = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                val = [tok for tok in re.split(r"[,;\s]+", s.strip("[]() ")) if tok]
            items = list(val) if isinstance(val, (list, tuple, set)) else [val]
    else:
        try:
            items = [] if (cell is None or pd.isna(cell)) else [cell]
        except (TypeError, ValueError):
            items = [cell]
    out: List[Any] = []
    for it in items:
        n = _norm_id(it)
        if n is not None and n not in out:
            out.append(n)
    return out


def _num(value: Any, default: Any, *, what: str, minimum: Optional[float] = None,
         as_int: bool = False) -> Any:
    """NaN/blank-safe numeric read. Blank -> default; unreadable text -> clear error."""
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return default
    else:
        try:
            if pd.isna(value):
                return default
        except (TypeError, ValueError):
            pass
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: cannot read {value!r} as a number") from None
    if not math.isfinite(x):
        raise ValueError(f"{what}: value must be finite, got {value!r}")
    if minimum is not None and x < minimum:
        raise ValueError(f"{what}: {x} is below the minimum allowed ({minimum})")
    return int(round(x)) if as_int else x


def _is_machine(value: Any, what: str) -> bool:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return False                       # blank -> manual
    s = str(value).strip().lower()
    if s == "":
        return False
    if s.startswith("mach"):
        return True
    if s.startswith("man"):
        return False
    raise ValueError(f"{what}: TaskType must be 'machine' or 'manual', got {value!r}")


def _canonicalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to the canonical names, parse Id/Type/Relation, add missing optional columns."""
    rename = {c: _COLUMN_ALIASES[re.sub(r"[^a-z0-9]", "", str(c).lower())]
              for c in raw.columns if re.sub(r"[^a-z0-9]", "", str(c).lower()) in _COLUMN_ALIASES}
    df = raw.rename(columns=rename).copy()
    if df.columns.duplicated().any():
        raise ValueError(f"Raw table has duplicate columns after normalising names: "
                         f"{list(df.columns[df.columns.duplicated()])}")
    for req in ("Id", "Type"):
        if req not in df.columns:
            raise ValueError(f"Raw table is missing required column '{req}'")
    for opt in ("Quantity", "Available", "CycleTime", "TaskType", "MinOperators", "MaxOperators"):
        if opt not in df.columns:
            df[opt] = np.nan
    if "Relation" not in df.columns:
        df["Relation"] = [[] for _ in range(len(df))]

    ids = [_norm_id(v) for v in df["Id"]]
    if any(i is None for i in ids):
        raise ValueError("Every row needs an Id (found a blank one)")
    types = []
    for i, v in zip(ids, df["Type"]):
        s = str(v).strip().lower()
        if s not in ("task", "resource"):
            raise ValueError(f"Row {i!r}: Type must be 'Task' or 'Resource', got {v!r}")
        types.append(s.capitalize())
    df["Id"] = pd.Series(ids, index=df.index, dtype=object)
    df["Type"] = pd.Series(types, index=df.index, dtype=object)
    df["Relation"] = pd.Series([_parse_relation(v) for v in df["Relation"]],
                               index=df.index, dtype=object)
    return df


@dataclass
class Problem:
    raw_graph: RawTableGraph
    info: Dict[Any, Dict[str, Any]]        # task id -> cycle_time, is_machine, min/max_operators, quantity
    resources: Dict[Any, Dict[str, Any]]   # resource id -> quantity, available
    sink: Any
    preds_task: Dict[Any, List[Any]]       # task -> task predecessors (raw graph)
    preds_res: Dict[Any, List[Any]]        # task -> resource predecessors
    succ: Dict[Any, Any]                   # task -> its single successor task (None for the sink)
    res_consumer: Dict[Any, Any]           # resource -> the task that consumes it (None if unused)
    order: Dict[Any, int]                  # task -> position in a topological order (for determinism)


def setup(raw_table: pd.DataFrame) -> Problem:
    df = _canonicalise(raw_table)

    ids = list(df["Id"])
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1}, key=str)
        raise ValueError(f"Duplicate ids in raw table: {dupes}")
    known_ids = set(ids)

    phantom = sorted({p for rel in df["Relation"] for p in rel if p not in known_ids}, key=str)
    if phantom:
        raise ValueError(f"Relation column references ids that are not in the table: {phantom}")

    raw_graph = RawTableGraph(df)          # rejects cycles and branching (out-degree > 1)
    G = raw_graph.graph

    info: Dict[Any, Dict[str, Any]] = {}
    resources: Dict[Any, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        rid = row["Id"]
        if row["Type"] == "Task":
            what = f"Task {rid!r}"
            mn = _num(row["MinOperators"], 1, what=f"{what} MinOperators", minimum=0, as_int=True)
            mx = _num(row["MaxOperators"], mn, what=f"{what} MaxOperators", minimum=0, as_int=True)
            info[rid] = {
                "cycle_time": _num(row["CycleTime"], 0.0, what=f"{what} CycleTime", minimum=0.0),
                "is_machine": _is_machine(row["TaskType"], what),
                "min_operators": mn,
                "max_operators": max(mx, mn),
                "quantity": _num(row["Quantity"], 1, what=f"{what} Quantity", minimum=0, as_int=True),
            }
        else:
            what = f"Resource {rid!r}"
            if row["Relation"]:
                raise ValueError(f"{what} has predecessors {row['Relation']}; resources must be sources")
            if pd.isna(row["Available"]) or str(row["Available"]).strip() == "":
                warnings.warn(f"{what} has no Available stock; assuming 0 (line will starve)")
            resources[rid] = {
                "quantity": _num(row["Quantity"], 1, what=f"{what} Quantity", minimum=0, as_int=True),
                "available": _num(row["Available"], 0.0, what=f"{what} Available", minimum=0.0),
            }
    if not info:
        raise ValueError("Raw table contains no Task rows")

    preds_task = {t: [p for p in G.predecessors(t) if p in info] for t in info}
    preds_res = {t: [p for p in G.predecessors(t) if p in resources] for t in info}
    succ = {t: next(iter(G.successors(t)), None) for t in info}
    res_consumer = {r: next(iter(G.successors(r)), None) for r in resources}

    sinks = [t for t in info if succ[t] is None]
    if len(sinks) != 1:
        raise ValueError(f"Line must have exactly one final task (no successor); found {len(sinks)}: {sinks}")

    topo = raw_graph.get_topological_order()
    order = {t: i for i, t in enumerate(x for x in topo if x in info)}
    return Problem(raw_graph, info, resources, sinks[0], preds_task, preds_res, succ,
                   res_consumer, order)


# =========================================================================== #
# Layout: a partition of tasks into stations + a crew per station
# =========================================================================== #
@dataclass
class Station:
    sid: int
    tasks: FrozenSet[Any]
    required_workers: int
    time: float = 0.0


class Layout:
    def __init__(self) -> None:
        self.stations: Dict[int, Station] = {}
        self.station_of: Dict[Any, int] = {}
        self.station_graph: nx.DiGraph = nx.DiGraph()
        self.next_id: int = 0                  # fresh station ids, never reused within a lineage

    def copy(self) -> "Layout":
        new = Layout()
        new.stations = {sid: Station(s.sid, s.tasks, s.required_workers, s.time)
                        for sid, s in self.stations.items()}
        new.station_of = dict(self.station_of)
        new.station_graph = self.station_graph.copy()
        new.next_id = self.next_id
        return new


def layout_signature(layout: Layout) -> FrozenSet:
    return frozenset((s.tasks, s.required_workers) for s in layout.stations.values())


# =========================================================================== #
# 2. Things computed on demand
# =========================================================================== #
def crew_bounds(problem: Problem, tasks: Sequence[Any]) -> Tuple[int, int]:
    lo = max(problem.info[t]["min_operators"] for t in tasks)          # machines included
    manual_max = [problem.info[t]["max_operators"] for t in tasks if not problem.info[t]["is_machine"]]
    hi = max(manual_max) if manual_max else lo
    return lo, max(lo, hi)


def station_exit_task(problem: Problem, tasks: FrozenSet[Any]) -> Any:
    exits = [t for t in tasks if problem.succ[t] not in tasks]
    if len(exits) != 1:
        raise ValueError(f"Station {sorted(tasks, key=str)} has {len(exits)} exit tasks (need exactly 1)")
    return exits[0]


def station_connected(problem: Problem, tasks: FrozenSet[Any]) -> bool:
    return problem.raw_graph.is_valid_station_group(list(tasks))


def _internal_neighbors(problem: Problem, tasks: FrozenSet[Any], t: Any) -> set:
    nb = {p for p in problem.preds_task[t] if p in tasks}
    s = problem.succ[t]
    if s is not None and s in tasks:
        nb.add(s)
    return nb


def _multipliers(problem: Problem, tasks: FrozenSet[Any], exit_task: Any) -> Dict[Any, int]:
    """How many times each task runs per ONE output unit of its station."""
    mult = {exit_task: 1}
    stack = [exit_task]
    while stack:
        x = stack.pop()
        for p in problem.preds_task[x]:
            if p in tasks:
                mult[p] = mult[x] * problem.info[p]["quantity"]
                stack.append(p)
    return mult


def refresh(problem: Problem, station: Station) -> None:
    lo, hi = crew_bounds(problem, station.tasks)
    station.required_workers = min(max(station.required_workers, lo), hi)
    exit_task = station_exit_task(problem, station.tasks)
    total = 0.0
    for t, m in _multipliers(problem, station.tasks, exit_task).items():
        ti = problem.info[t]
        if ti["is_machine"]:
            d = ti["cycle_time"]
        else:
            d = ti["cycle_time"] / max(1, min(station.required_workers, ti["max_operators"]))
        total += m * d
    station.time = total


# =========================================================================== #
# 6. Station-graph edges + legality
# =========================================================================== #
def _add_edges_for(problem: Problem, G: nx.DiGraph, sid: int, tasks: FrozenSet[Any],
                   station_of: Callable[[Any], int]) -> None:
    G.add_node(sid)
    for t in sorted(tasks, key=problem.order.__getitem__):
        for p in problem.preds_task[t]:
            ps = station_of(p)
            if ps != sid:
                G.add_edge(ps, sid)
        s = problem.succ[t]
        if s is not None:
            ss = station_of(s)
            if ss != sid:
                G.add_edge(sid, ss)


def rebuild_station_graph_edges(problem: Problem, layout: Layout, *touched: int) -> None:
    G = layout.station_graph
    for sid in touched:
        G.add_node(sid)
        G.remove_edges_from(list(G.in_edges(sid)) + list(G.out_edges(sid)))
    for sid in touched:
        _add_edges_for(problem, G, sid, layout.stations[sid].tasks, layout.station_of.__getitem__)


def move_is_legal(problem: Problem, layout: Layout, sid: int, did: int, t: Any) -> bool:
    """Would moving task t from station sid into station did keep the station graph acyclic?
    Checked on a scratch copy with a hypothetical station_of."""
    G = layout.station_graph.copy()
    for x in (sid, did):
        G.remove_edges_from(list(G.in_edges(x)) + list(G.out_edges(x)))
    s_after = layout.stations[sid].tasks - {t}
    d_after = layout.stations[did].tasks | {t}

    def of(x: Any) -> int:
        return did if x == t else layout.station_of[x]

    if s_after:
        _add_edges_for(problem, G, sid, s_after, of)
    else:
        G.remove_node(sid)
    _add_edges_for(problem, G, did, d_after, of)
    return nx.is_directed_acyclic_graph(G)


# =========================================================================== #
# 3. Seeds
# =========================================================================== #
def _validate_layout(problem: Problem, layout: Layout) -> None:
    assigned = [t for s in layout.stations.values() for t in s.tasks]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(problem.info):
        raise ValueError("tasks are not assigned exactly once")
    for s in layout.stations.values():
        if not station_connected(problem, s.tasks):
            raise ValueError(f"station {sorted(s.tasks, key=str)} is not connected")
        station_exit_task(problem, s.tasks)                      # raises unless unique
        if any(layout.station_of[t] != s.sid for t in s.tasks):
            raise ValueError("station_of is out of sync with station contents")
    if set(layout.station_graph.nodes) != set(layout.stations):
        raise ValueError("station graph nodes do not match stations")
    if not nx.is_directed_acyclic_graph(layout.station_graph):
        raise ValueError("station graph has a cycle")


def _layout_from_candidate(problem: Problem, cand: Dict[str, Any]) -> Layout:
    flat = [t for st in cand["stations"] for t in st["tasks"]]
    if len(flat) != len(set(flat)) or set(flat) != set(problem.info):
        raise ValueError("candidate does not assign every task exactly once")
    layout = Layout()
    for k, st in enumerate(cand["stations"]):
        layout.stations[k] = Station(k, frozenset(st["tasks"]), int(st["required_workers"]))
        for t in st["tasks"]:
            layout.station_of[t] = k
    layout.next_id = len(layout.stations)
    for s in layout.stations.values():
        if not station_connected(problem, s.tasks):
            raise ValueError(f"station {sorted(s.tasks, key=str)} is not connected")
        refresh(problem, s)                                       # also checks unique exit
    rebuild_station_graph_edges(problem, layout, *layout.stations)
    _validate_layout(problem, layout)
    return layout


def make_seeds(problem: Problem, cfg: SearchConfig) -> List[Layout]:
    gen_tasks = [
        GenTask(id=t, cycle_time=i["cycle_time"],
                task_type="machine" if i["is_machine"] else "manual",
                min_operators=i["min_operators"], max_operators=i["max_operators"],
                predecessors=list(problem.preds_task[t]))          # task predecessors only:
        for t, i in problem.info.items()                            # a resource id here would deadlock LineBalancer
    ]
    out = LineBalancer(gen_tasks, cfg.available_workers, cfg.target_units,
                       cfg.shift_duration, seed=cfg.master_seed).run(num_candidates=cfg.num_seed_candidates)
    seeds: List[Layout] = []
    for cand in out["candidates"]:
        try:
            seeds.append(_layout_from_candidate(problem, cand))
        except (ValueError, KeyError, TypeError, nx.NetworkXException) as exc:
            warnings.warn(f"Discarding seed candidate {cand.get('candidate_id')}: {exc}")
    if not seeds:
        raise RuntimeError("LineBalancer produced no valid seed layouts")
    return seeds


# =========================================================================== #
# 4. Moves
# =========================================================================== #
Move = Tuple[Any, ...]


def enumerate_moves(problem: Problem, layout: Layout) -> List[Move]:
    """Every legal move from `layout`: ('crew', sid, +-1), ('split', sid, t),
    ('exit_fwd', sid), ('merge', sid)."""
    moves: List[Move] = []
    for sid, S in layout.stations.items():
        # 4.1 crew change
        lo, hi = crew_bounds(problem, S.tasks)
        if S.required_workers + 1 <= hi:
            moves.append(("crew", sid, +1))
        if S.required_workers - 1 >= lo:
            moves.append(("crew", sid, -1))

        exit_task = station_exit_task(problem, S.tasks)

        # 4.2 split before a task that has exactly one predecessor inside the station
        if len(S.tasks) > 1:
            for t in sorted(S.tasks, key=problem.order.__getitem__):
                if sum(1 for p in problem.preds_task[t] if p in S.tasks) == 1:
                    moves.append(("split", sid, t))

            # 4.3 move the exit task forward into the downstream station
            s = problem.succ[exit_task]
            if s is not None and len(_internal_neighbors(problem, S.tasks, exit_task)) == 1:
                if move_is_legal(problem, layout, sid, layout.station_of[s], exit_task):
                    moves.append(("exit_fwd", sid))

        # 4.4 merge with the station that consumes this one's output
        if problem.succ[exit_task] is not None:
            moves.append(("merge", sid))
    return moves


def apply_move(problem: Problem, layout: Layout, move: Move) -> Layout:
    """Return a NEW layout with `move` applied (the input layout is never modified)."""
    new = layout.copy()
    kind, sid = move[0], move[1]
    S = new.stations[sid]

    if kind == "crew":
        S.required_workers += move[2]
        refresh(problem, S)

    elif kind == "split":
        t = move[2]
        (u,) = [p for p in problem.preds_task[t] if p in S.tasks]
        upstream, stack = {u}, [u]
        while stack:
            x = stack.pop()
            for p in problem.preds_task[x]:
                if p in S.tasks and p not in upstream:
                    upstream.add(p)
                    stack.append(p)
        part_a, part_b = frozenset(upstream), S.tasks - upstream
        new_sid = new.next_id
        new.next_id += 1
        S.tasks = part_a
        S.required_workers = crew_bounds(problem, part_a)[0]
        ns = Station(new_sid, part_b, crew_bounds(problem, part_b)[0])
        new.stations[new_sid] = ns
        for x in part_b:
            new.station_of[x] = new_sid
        new.station_graph.add_node(new_sid)
        refresh(problem, S)
        refresh(problem, ns)
        rebuild_station_graph_edges(problem, new, sid, new_sid)

    elif kind == "exit_fwd":
        e = station_exit_task(problem, S.tasks)
        did = new.station_of[problem.succ[e]]
        D = new.stations[did]
        S.tasks = S.tasks - {e}
        D.tasks = D.tasks | {e}
        new.station_of[e] = did
        refresh(problem, D)
        if S.tasks:
            refresh(problem, S)
            rebuild_station_graph_edges(problem, new, sid, did)
        else:                                                     # S vanished
            del new.stations[sid]
            new.station_graph.remove_node(sid)
            rebuild_station_graph_edges(problem, new, did)

    elif kind == "merge":
        e = station_exit_task(problem, S.tasks)
        jid = new.station_of[problem.succ[e]]
        Sj = new.stations.pop(jid)
        S.tasks = S.tasks | Sj.tasks
        for x in Sj.tasks:
            new.station_of[x] = sid
        new.station_graph.remove_node(jid)
        S.required_workers = max(S.required_workers, Sj.required_workers)
        refresh(problem, S)
        rebuild_station_graph_edges(problem, new, sid)            # contracting an edge of a DAG can't cycle

    else:
        raise ValueError(f"unknown move {move!r}")
    return new


# =========================================================================== #
# 5. Compile for the simulator
# =========================================================================== #
def compile_for_simulator(problem: Problem, layout: Layout) -> List[Dict[str, Any]]:
    """Full rebuild of the simulator's station table from the layout.

    Quantity rows are scaled by how many times the CONSUMING task runs per station
    cycle (its multiplier). Without that, a consumer that runs m times per station
    output would draw only 1x of its input per cycle while its station time already
    charges m runs. With every multiplier equal to 1 this is identical to the plain
    'Quantity = raw quantity' rule.
    """
    exits: Dict[int, Any] = {}
    mult: Dict[Any, int] = {}
    for sid, S in layout.stations.items():
        exits[sid] = station_exit_task(problem, S.tasks)
        mult.update(_multipliers(problem, S.tasks, exits[sid]))

    rows: List[Dict[str, Any]] = []
    res_row: Dict[Any, int] = {}
    for r, rinfo in problem.resources.items():
        res_row[r] = len(rows)
        consumer = problem.res_consumer[r]
        q = rinfo["quantity"] * (mult[consumer] if consumer is not None else 1)
        rows.append({"group": res_row[r], "Relation": [], "Type": "Resource", "MemberTasks": [r],
                     "Quantity": int(q), "CycleTime": 0.0, "P_factor": 0,
                     "Available": rinfo["available"]})

    n_res = len(rows)
    first_task = lambda sid: min(problem.order[t] for t in layout.stations[sid].tasks)  # noqa: E731
    station_order = list(nx.lexicographical_topological_sort(layout.station_graph, key=first_task))
    station_row = {sid: n_res + k for k, sid in enumerate(station_order)}
    for sid in station_order:
        S = layout.stations[sid]
        deps = set()
        for t in S.tasks:
            for r in problem.preds_res[t]:
                deps.add(res_row[r])
            for p in problem.preds_task[t]:
                if layout.station_of[p] != sid:
                    deps.add(station_row[layout.station_of[p]])
        e = exits[sid]
        q = 1 if e == problem.sink else problem.info[e]["quantity"] * mult[problem.succ[e]]
        rows.append({"group": station_row[sid], "Relation": sorted(deps), "Type": "Task",
                     "MemberTasks": sorted(S.tasks, key=problem.order.__getitem__),
                     "Quantity": int(q), "CycleTime": S.time, "P_factor": S.required_workers,
                     "Available": 0})
    return rows


# =========================================================================== #
# 7. Evaluate + fitness
# =========================================================================== #
@dataclass
class Evaluation:
    layout: Layout
    signature: FrozenSet
    units: float
    avg_wip: float
    workers: int
    fitness: float
    flag: str


def compute_fitness(cfg: SearchConfig, units: float, avg_wip: float, workers: int) -> float:
    f = cfg.unit_price * units
    if cfg.target_units is not None:
        f -= cfg.unit_price * max(0.0, cfg.target_units - units)
    f -= cfg.wip_holding_cost * avg_wip
    if cfg.available_workers is not None:
        f -= cfg.worker_salary * min(cfg.available_workers, workers)
        f -= 1.5 * cfg.worker_salary * max(0, workers - cfg.available_workers)
    else:
        f -= cfg.worker_salary * workers
    return f


def compute_flag(cfg: SearchConfig, units: float, workers: int) -> str:
    """🔴 target missed | 🟡 target met but over the worker budget | 🟢 everything satisfied."""
    if cfg.target_units is not None and units < cfg.target_units:
        return "🔴"
    if cfg.available_workers is not None and workers > cfg.available_workers:
        return "🟡"
    return "🟢"


class Evaluator:
    """Simulates layouts with common random numbers and caches results by layout signature."""

    def __init__(self, problem: Problem, cfg: SearchConfig) -> None:
        self.problem = problem
        self.cfg = cfg
        self.cache: Dict[FrozenSet, Evaluation] = {}

    def evaluate(self, layout: Layout) -> Evaluation:
        sig = layout_signature(layout)
        hit = self.cache.get(sig)
        if hit is not None:
            return hit

        cfg = self.cfg
        table = compile_for_simulator(self.problem, layout)
        runs = [run_simulation(table, shift_duration=cfg.shift_duration, seed=cfg.master_seed + r)
                for r in range(1, cfg.n_replications + 1)]        # same seeds for every layout
        if any(r.get("event_cap_reached") for r in runs):
            warnings.warn("Simulator hit max_events; metrics for this layout are truncated")

        units = float(np.mean([r["throughput_units"] for r in runs]))
        avg_wip = float(np.mean([r["avg_wip"] for r in runs]))
        workers = sum(s.required_workers for s in layout.stations.values())
        ev = Evaluation(layout, sig, units, avg_wip, workers,
                        compute_fitness(cfg, units, avg_wip, workers),
                        compute_flag(cfg, units, workers))
        self.cache[sig] = ev
        return ev


# =========================================================================== #
# 8. Main loop
# =========================================================================== #
def run_search(raw_table: pd.DataFrame, cfg: SearchConfig) -> Dict[str, Any]:
    problem = setup(raw_table)
    evaluator = Evaluator(problem, cfg)
    rng = random.Random(cfg.master_seed)

    seeds = make_seeds(problem, cfg)
    seed_evals = [evaluator.evaluate(layout) for layout in seeds]
    current = best = max(seed_evals, key=lambda e: e.fitness)

    temp, no_improve = cfg.start_temp, 0
    history: List[Dict[str, Any]] = [{
        "iteration": 0, "temp": temp, "move": None, "accepted": None,
        "candidate_fitness": None, "current_fitness": current.fitness, "best_fitness": best.fitness}]

    iterations_run = 0
    for it in range(1, cfg.n_iterations + 1):
        iterations_run = it
        moves = enumerate_moves(problem, current.layout)
        move_kind, cand_fitness, accepted = None, None, False

        if not moves:
            no_improve += 1
        else:
            move = rng.choice(moves)
            move_kind = move[0]
            new_layout = apply_move(problem, current.layout, move)
            if cfg.debug_validate:
                _validate_layout(problem, new_layout)
            cand = evaluator.evaluate(new_layout)
            cand_fitness = cand.fitness
            delta = cand.fitness - current.fitness
            if delta > 0 or (temp > 1e-12 and rng.random() < math.exp(delta / temp)):
                current, accepted = cand, True
            if cand.fitness > best.fitness + 1e-9:
                best, no_improve = cand, 0
            else:
                no_improve += 1

        temp *= cfg.cooling_rate
        history.append({"iteration": it, "temp": temp, "move": move_kind, "accepted": accepted,
                        "candidate_fitness": cand_fitness,
                        "current_fitness": current.fitness, "best_fitness": best.fitness})
        if no_improve >= cfg.no_improve_limit:
            break

    return build_result(problem, cfg, evaluator, best, history, iterations_run)


# =========================================================================== #
# 9. Result
# =========================================================================== #
def _describe(problem: Problem, ev: Evaluation) -> Dict[str, Any]:
    layout = ev.layout
    key = lambda sid: min(problem.order[t] for t in layout.stations[sid].tasks)  # noqa: E731
    display_order = list(nx.lexicographical_topological_sort(layout.station_graph, key=key))
    stations = []
    for i, sid in enumerate(display_order, start=1):
        s = layout.stations[sid]
        stations.append({
            "station_index": i,
            "tasks": sorted(s.tasks, key=problem.order.__getitem__),
            "required_workers": s.required_workers,
            "station_time": round(s.time, 4),
        })
    return {
        "stations": stations,
        "num_stations": len(stations),
        "units": ev.units,
        "workers": ev.workers,
        "avg_wip": ev.avg_wip,
        "fitness": ev.fitness,
        "flag": ev.flag,
    }


def build_result(problem: Problem, cfg: SearchConfig, evaluator: Evaluator, best: Evaluation,
                 history: List[Dict[str, Any]], iterations_run: int) -> Dict[str, Any]:
    others = sorted((e for e in evaluator.cache.values() if e.signature != best.signature),
                    key=lambda e: e.fitness, reverse=True)[:cfg.top_k]
    return {
        "best": _describe(problem, best),
        "alternatives": [_describe(problem, e) for e in others],
        "fitness_history": history,
        "iterations_run": iterations_run,
        "stopped_early": iterations_run < cfg.n_iterations,
        "layouts_evaluated": len(evaluator.cache),
    }


def format_report(result: Dict[str, Any]) -> str:
    b = result["best"]
    lines = [f"{b['flag']} best layout: {b['num_stations']} stations, {b['workers']} workers, "
             f"{b['units']:.1f} units, avg WIP {b['avg_wip']:.2f}, fitness {b['fitness']:.1f}"]
    for s in b["stations"]:
        lines.append(f"  station {s['station_index']}: tasks={s['tasks']} "
                     f"workers={s['required_workers']} time={s['station_time']}")
    lines.append(f"iterations={result['iterations_run']} "
                 f"(stopped early: {result['stopped_early']}), layouts evaluated={result['layouts_evaluated']}")
    for k, a in enumerate(result["alternatives"], start=1):
        lines.append(f"  alt {k}: {a['flag']} fitness {a['fitness']:.1f}, {a['num_stations']} stations, "
                     f"{a['workers']} workers, {a['units']:.1f} units")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Demo: python optimization/search_runner.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    demo = pd.DataFrame([
        # Id, Type,       Relation, Quantity, Available, CycleTime, TaskType, MinOp, MaxOp
        (1,  "Resource", "[]",      1, 500, None, None,      None, None),
        (2,  "Resource", "[]",      1, 500, None, None,      None, None),
        (3,  "Resource", "[]",      1, 500, None, None,      None, None),
        (10, "Task",     "[1]",     1, None, 1.0, "manual",  1, 2),
        (11, "Task",     "[10]",    1, None, 1.5, "machine", 1, 1),
        (12, "Task",     "[2]",     1, None, 2.0, "manual",  1, 3),
        (13, "Task",     "[12]",    1, None, 1.0, "manual",  1, 1),
        (14, "Task",     "[11, 13]", 1, None, 1.2, "manual", 1, 2),
        (15, "Task",     "[3]",     1, None, 0.8, "manual",  1, 1),
        (16, "Task",     "[14, 15]", 1, None, 2.0, "manual", 1, 2),
    ], columns=["Id", "Type", "Relation", "Quantity", "Available", "CycleTime",
                "TaskType", "MinOperators", "MaxOperators"])

    cfg = SearchConfig(shift_duration=480, unit_price=50, worker_salary=100, wip_holding_cost=2,
                       target_units=200, available_workers=8, n_iterations=150, start_temp=50,
                       cooling_rate=0.98, n_replications=3, no_improve_limit=60,
                       master_seed=7, num_seed_candidates=5)
    print(format_report(run_search(demo, cfg)))