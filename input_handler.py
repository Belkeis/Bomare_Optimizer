"""
input_handler.py
----------------
The single bridge between the dashboard (server.py) and the optimizer.

    dashboard JSON  --normalize-->  clean rows  --to DataFrame-->  SearchRunner
    SearchRunner result  --format-->  JSON the dashboard can draw

Nothing in here does any optimizing itself; it only cleans input, validates it,
calls optimization.search_runner, and reshapes the answer.
"""

import csv
import io
import logging
import math
import re
import time
import warnings as _warnings
from typing import Any, Dict, List, Tuple

import networkx as nx
import numpy as np
import pandas as pd

from optimization.search_runner import SearchConfig, run_search, setup

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["Id", "Type", "Relation", "CycleTime", "Quantity", "Available",
               "operation_type", "min_operators", "max_operators"]


# --------------------------------------------------------------------------- #
# Small converters
# --------------------------------------------------------------------------- #
#Check whether a value is empty.
def _is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())

#Convert an ID into a clean string.
def _id_str(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()

#Convert a value to a float
def _to_float(v, default=0.0) -> float:
    try:
        return default if _is_blank(v) else float(v)
    except (TypeError, ValueError):
        return default

#Convert a value to an integer.
def _to_int(v, default=0) -> int:
    try:
        return default if _is_blank(v) else int(float(v))
    except (TypeError, ValueError):
        return default

#It converts different ways of writing relationships into a standard Python list of IDs.
def parse_relation(rel) -> List[str]:
    """Accepts a list, or a string like '1, 2' / '[1, 2]' / "['T1']" / 'R1;T2'."""
    if _is_blank(rel):
        return []
    if isinstance(rel, (list, tuple, set)):
        return [_id_str(x) for x in rel if not _is_blank(x)]
    text = re.sub(r"[\[\]'\"()]", "", str(rel))
    return [_id_str(x) for x in re.split(r"[,;|]", text) if x.strip()]


# --------------------------------------------------------------------------- #
# Normalization + validation
# --------------------------------------------------------------------------- #
def normalize_table(table: Any) -> Tuple[List[Dict], List[str], List[str]]:
    """Returns (clean_rows, errors, warnings). Never raises on bad user data."""
    errors: List[str] = []
    warnings: List[str] = []

    #If the table is not a list or is empty
    if not isinstance(table, list) or not table:
        return [], ["The assembly line table is empty."], []
    
    #check each row is valid 
    rows, seen = [], set()
    for pos, raw in enumerate(table, start=1):
        if not isinstance(raw, dict):
            errors.append(f"Row {pos}: not a valid row.")
            continue

        rid = "" if _is_blank(raw.get("Id")) else _id_str(raw.get("Id"))
        rtype = str(raw.get("Type", "Task")).strip().capitalize()
        if not rid:
            errors.append(f"Row {pos}: Id is empty.")
            continue
        if rid in seen:
            errors.append(f"Duplicate Id '{rid}'.")
            continue
        seen.add(rid)
        if rtype not in ("Task", "Resource"):
            errors.append(f"'{rid}': Type must be Task or Resource (got '{raw.get('Type')}').")
            continue

        
        rel = parse_relation(raw.get("Relation"))
        qty = max(1, _to_int(raw.get("Quantity"), 1))

        #check if the type is resource so it give it specific 
        if rtype == "Resource":
            if rel:
                warnings.append(f"Resource '{rid}' cannot have predecessors; its Relation was ignored.")
            rows.append({"Id": rid, "Type": "Resource", "Relation": [], "CycleTime": 0.0,
                         "Quantity": qty, "Available": max(0.0, _to_float(raw.get("Available"), 1000.0)),
                         "operation_type": "manual", "min_operators": 0, "max_operators": 0})
            continue

        #If it's a Task it check cycle time
        ct = _to_float(raw.get("CycleTime"), 0.0)
        if ct <= 0:
            errors.append(f"Task '{rid}': Cycle Time must be greater than 0 (seconds).")
        op = str(raw.get("operation_type") or raw.get("TaskType") or "manual").strip().lower()
        if op not in ("manual", "machine"):
            warnings.append(f"Task '{rid}': unknown operation type '{op}', using manual.")
            op = "manual"
        mn = max(1, _to_int(raw.get("min_operators"), 1))
        mx = max(mn, _to_int(raw.get("max_operators"), mn))
        if op == "machine":
            mx = mn                                    # a machine can't use extra workers
        rows.append({"Id": rid, "Type": "Task", "Relation": rel, "CycleTime": ct, "Quantity": qty,
                     "Available": 0.0, "operation_type": op, "min_operators": mn, "max_operators": mx})

    return rows, errors, warnings


def _structure_errors(rows: List[Dict]) -> List[str]:
    """Graph rules: known predecessors, no cycles, no branching, one final task."""
    errors: List[str] = []
    ids = {r["Id"] for r in rows}
    G = nx.DiGraph()
    G.add_nodes_from(ids)

    for r in rows:
        for p in r["Relation"]:
            if p == r["Id"]:
                errors.append(f"'{r['Id']}' lists itself as a predecessor.")
            elif p not in ids:
                errors.append(f"'{r['Id']}' has predecessor '{p}' which does not exist in the table.")
            else:
                G.add_edge(p, r["Id"])
    if errors:
        return errors

    if not nx.is_directed_acyclic_graph(G):
        cyc = nx.find_cycle(G)
        return ["Circular dependency detected: " + " → ".join(str(u) for u, _ in cyc) + f" → {cyc[0][0]}"]

    for n in G.nodes:
        if G.out_degree(n) > 1:
            errors.append(f"'{n}' feeds several rows ({', '.join(sorted(G.successors(n)))}); "
                          "branching is not allowed (each row may feed only one other row).")

    tasks = [r["Id"] for r in rows if r["Type"] == "Task"]
    if not tasks:
        errors.append("The table has no Task rows.")
    else:
        sinks = [t for t in tasks if G.out_degree(t) == 0]
        if len(sinks) != 1:
            errors.append("Expected exactly one final task (a task no other row depends on); "
                          f"found {len(sinks)}: {', '.join(sinks) or 'none'}.")
    return errors


def validate_table(table: Any) -> Dict:
    rows, errors, warnings = normalize_table(table)
    if not errors:
        errors = _structure_errors(rows)

    summary: Dict[str, Any] = {}
    if not errors:
        # last line of defence: the real optimizer input parser
        try:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                setup(to_dataframe(rows))
        except Exception as e:                              # noqa: BLE001
            errors.append(str(e))

    if not errors:
        G = nx.DiGraph()
        G.add_nodes_from(r["Id"] for r in rows)
        G.add_edges_from((p, r["Id"]) for r in rows for p in r["Relation"])
        tasks = [r for r in rows if r["Type"] == "Task"]
        summary = {"tasks_count": len(tasks),
                   "resources_count": len(rows) - len(tasks),
                   "total_work_content": round(sum(t["CycleTime"] for t in tasks), 3),   # seconds
                   "topological_order": list(nx.topological_sort(G))}

    return {"is_valid": not errors, "errors": errors, "warnings": warnings, "summary": summary}


def to_dataframe(rows: List[Dict]) -> pd.DataFrame:
    """Clean dashboard rows -> the raw table SearchRunner expects."""
    return pd.DataFrame([{
        "Id": r["Id"], "Relation": list(r["Relation"]), "Type": r["Type"],
        "CycleTime": r["CycleTime"], "Quantity": r["Quantity"], "Available": r["Available"],
        "TaskType": r["operation_type"], "MinOperators": r["min_operators"],
        "MaxOperators": r["max_operators"],
    } for r in rows])


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
# Search-algorithm settings are no longer user inputs; they are fixed here.
ALGO_SETTINGS = {
    "num_seed_candidates": 5,
    "n_iterations": 200,
    "start_temp": 100.0,
    "cooling_rate": 0.98,
    "n_replications": 3,
    "no_improve_limit": 100,
    "master_seed": 42,
}


SECONDS_PER_HOUR = 3600


def _required_number(p: Dict, key: str, label: str, errors: List[str], minimum: float, strict: bool = False):
    """Reads a REQUIRED numeric parameter. No default is substituted: missing/invalid -> error."""
    raw = p.get(key)
    if _is_blank(raw):
        errors.append(f"{label} is required.")
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{label} must be a number.")
        return None
    if not math.isfinite(val) or (val <= minimum if strict else val < minimum):
        errors.append(f"{label} must be {'greater than' if strict else 'at least'} {minimum:g}.")
        return None
    return val


def build_config(params: Dict) -> Tuple[Any, List[str]]:
    """Returns (SearchConfig or None, errors).
    Required (all must be > 0): shift_hours (hours -> converted to seconds here), unit_price, worker_salary, wip_holding_cost.
    Optional: available_workers, target_quantity (empty = no limit / no target)."""
    p = params or {}
    errors: List[str] = []

    # The dashboard sends the shift in HOURS; the optimizer works in SECONDS (like the cycle times).
    shift_hours = _required_number(p, "shift_hours", "Shift Duration (hours)", errors, 0, strict=True)
    shift = shift_hours * SECONDS_PER_HOUR if shift_hours is not None else None
    price = _required_number(p, "unit_price", "Unit Revenue", errors, 0, strict=True)
    salary = _required_number(p, "worker_salary", "Worker Salary", errors, 0, strict=True)
    wip = _required_number(p, "wip_holding_cost", "WIP Holding Cost", errors, 0, strict=True)

    workers = _to_int(p.get("available_workers"), 0)
    target = _to_float(p.get("target_quantity"), 0.0)

    if errors:
        return None, errors

    return SearchConfig(
        target_units=target if target > 0 else None,
        available_workers=workers if workers > 0 else None,
        shift_duration=shift,
        unit_price=price,
        worker_salary=salary,
        wip_holding_cost=wip,
        **ALGO_SETTINGS,
    ), []


# --------------------------------------------------------------------------- #
# Result formatting  (run_search dict  ->  JSON the dashboard draws)
# --------------------------------------------------------------------------- #
def _plain(obj):
    """Recursively turn numpy / non-JSON types into plain Python."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.generic):
        return _plain(obj.item())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    return obj


def _key(task_id) -> str:
    """search_runner turns digit-string ids into ints; compare everything as clean strings."""
    s = str(task_id).strip()
    return str(int(s)) if re.fullmatch(r"[+-]?\d+", s) else s


# search_runner flags are bare emojis; app.js looks for these words to pick the badge colour.
_FLAG_TEXT = {
    "\U0001F7E2": "\U0001F7E2 OPTIMAL",                       # green:  everything satisfied
    "\U0001F7E1": "\U0001F7E1 OVERFLOW - worker budget exceeded",   # yellow: target met, too many workers
    "\U0001F534": "\U0001F534 TARGET NOT MET",                # red:    target missed
}


def _layout_for_ui(desc: Dict, rows: List[Dict]) -> Dict:
    """One search_runner layout description -> the fields app.js reads
    (stations[], units, workers, avg_wip, fitness, efficiency_pct)."""
    by_id = {_key(r["Id"]): r for r in rows if r["Type"] == "Task"}
    slowest = max((s["station_time"] for s in desc["stations"]), default=0.0)

    stations = []
    for st in desc["stations"]:
        crew = st["required_workers"]
        tasks = [_key(t) for t in st["tasks"]]
        # same rule as search_runner.refresh(): machines = 1 worker, manual = min(crew, max_operators)
        per_task = {t: (1 if by_id[t]["operation_type"] == "machine"
                        else max(1, min(crew, by_id[t]["max_operators"]))) for t in tasks}
        # balance-based utilisation: a station is busy station_time / slowest_station_time of the cycle
        busy = 100.0 * st["station_time"] / slowest if slowest > 0 else 0.0
        stations.append({
            "station_id": st["station_index"],
            "station_index": st["station_index"],
            "tasks": tasks,
            "required_workers": crew,
            "headcount": crew,
            "station_time": st["station_time"],
            "workers_per_task": per_task,
            "busy_pct": round(busy, 1),
            "starved_pct": round(100.0 - busy, 1),
        })

    efficiency = sum(s["busy_pct"] for s in stations) / len(stations) if stations else 0.0
    return {
        "stations": stations,
        "num_stations": len(stations),
        "units": desc["units"],
        "workers": desc["workers"],
        "avg_wip": desc["avg_wip"],
        "fitness": desc["fitness"],
        "efficiency_pct": round(efficiency, 1),
        "flag": _FLAG_TEXT.get(desc["flag"], desc["flag"]),
    }


# --------------------------------------------------------------------------- #
# Main entry point (called by server.py)
# --------------------------------------------------------------------------- #
def run_optimization_pipeline(payload: Dict) -> Dict:
    """
    payload = {"table": [...rows...], "params": {...}}   (exactly what app.js sends)
    returns  {"status": "success", "flag", "metrics", "best_layout", "alternatives", "history", ...}
          or {"status": "error", "errors": [...]}
    """
    if not isinstance(payload, dict):
        return {"status": "error", "errors": ["Request body must be a JSON object."]}

    check = validate_table(payload.get("table"))
    if not check["is_valid"]:
        return {"status": "error", "errors": check["errors"]}

    try:
        rows, _, _ = normalize_table(payload["table"])
        cfg, param_errors = build_config(payload.get("params"))
        if param_errors:
            return {"status": "error", "errors": param_errors}
        logger.info("Search started: %d rows, %d iterations, %d replications",
                    len(rows), cfg.n_iterations, cfg.n_replications)

        t0 = time.time()
        with _warnings.catch_warnings(record=True) as caught:   # surface search_runner warnings
            _warnings.simplefilter("always")
            result = run_search(to_dataframe(rows), cfg)
        elapsed = time.time() - t0

        best = _layout_for_ui(result["best"], rows)
        logger.info("Search finished in %.1fs, flag=%s", elapsed, best["flag"])

        seen, run_warnings = set(), list(check["warnings"])
        for w in caught:
            msg = str(w.message)
            if msg not in seen:
                seen.add(msg)
                run_warnings.append(msg)

        out = {
            "status": "success",
            "flag": best["flag"],
            "metrics": {k: best[k] for k in ("units", "workers", "avg_wip", "fitness",
                                             "efficiency_pct", "num_stations")},
            "best_layout": best["stations"],
            "alternatives": [_layout_for_ui(a, rows) for a in result["alternatives"]],
            "history": [h["best_fitness"] for h in result["fitness_history"]],
            "iterations_run": result["iterations_run"],
            "stopped_early": result["stopped_early"],
            "layouts_evaluated": result["layouts_evaluated"],
            "warnings": run_warnings,
            "run_time_sec": round(elapsed, 2),
        }
        return _plain(out)

    except Exception as e:                                   # noqa: BLE001
        logger.error("Optimization failed: %s", e, exc_info=True)
        return {"status": "error", "errors": [f"Optimization failed: {e}"]}


# --------------------------------------------------------------------------- #
# CSV import / export
# --------------------------------------------------------------------------- #
_CSV_ALIASES = {
    "id": "Id", "type": "Type", "relation": "Relation", "cycletime": "CycleTime",
    "cycle_time": "CycleTime", "quantity": "Quantity", "available": "Available",
    "initialstock": "Available", "operation_type": "operation_type", "tasktype": "operation_type",
    "min_operators": "min_operators", "minoperators": "min_operators",
    "max_operators": "max_operators", "maxoperators": "max_operators",
}


def table_to_csv(table: List[Dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in table:
        rel = r.get("Relation")
        rel = ", ".join(parse_relation(rel))
        w.writerow([r.get("Id", ""), r.get("Type", ""), rel, r.get("CycleTime", 0),
                    r.get("Quantity", 1), r.get("Available", 0), r.get("operation_type", "manual"),
                    r.get("min_operators", 1), r.get("max_operators", 1)])
    return buf.getvalue()


def csv_to_table(text: str) -> Tuple[List[Dict], List[str]]:
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except Exception as e:                                   # noqa: BLE001
        return [], [f"Could not read CSV: {e}"]

    df.columns = [_CSV_ALIASES.get(re.sub(r"[\s]+", "", c.strip().lower()), c.strip()) for c in df.columns]
    missing = [c for c in ("Id", "Type") if c not in df.columns]
    if missing:
        return [], [f"CSV is missing required column(s): {', '.join(missing)}."]

    table = []
    for _, r in df.iterrows():
        rows, errs, _ = normalize_table([{
            "Id": r.get("Id"), "Type": r.get("Type"), "Relation": r.get("Relation", ""),
            "CycleTime": r.get("CycleTime", 0), "Quantity": r.get("Quantity", 1),
            "Available": r.get("Available", 0), "operation_type": r.get("operation_type", "manual"),
            "min_operators": r.get("min_operators", 1), "max_operators": r.get("max_operators", 1),
        }])
        if rows:
            table.append(rows[0])
    if not table:
        return [], ["CSV contains no usable rows."]
    return table, []