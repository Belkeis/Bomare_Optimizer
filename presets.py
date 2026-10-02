"""
presets.py
Ready-made assembly-line tables for the dashboard's "Load Preset" dropdown.

Row format = exactly what the dashboard table uses:
    Id, Type ('Task'|'Resource'), Relation (list of predecessor ids), CycleTime (seconds),
    Quantity, Available (stock, resources only), operation_type ('manual'|'machine'),
    min_operators, max_operators

Rules every preset respects (same rules the backend validates):
    - every row feeds at most ONE other row (converging tree, no branching)
    - exactly one final task (no successor)
"""


def _task(id_, rel, ct, op="manual", mn=1, mx=1, qty=1):
    return {"Id": id_, "Type": "Task", "Relation": rel, "CycleTime": ct, "Quantity": qty,
            "Available": 0, "operation_type": op, "min_operators": mn,
            "max_operators": mn if op == "machine" else max(mn, mx)}


def _res(id_, stock=1000, qty=1):
    return {"Id": id_, "Type": "Resource", "Relation": [], "CycleTime": 0.0, "Quantity": qty,
            "Available": stock, "operation_type": "manual", "min_operators": 0, "max_operators": 0}


PRESETS = {
    "bomare_tv_line": {
        "name": "Bomare TV Assembly Line",
        "description": "8 tasks and 4 raw-material stocks, three sub-assemblies converging into one final line.",
        "parameters": {"available_workers": 18, "target_quantity": 180, "shift_hours": 8,
                       "unit_price": 65, "worker_salary": 28, "wip_holding_cost": 0.4,
                       "n_iterations": 200},
        "table": [
            _res("R1", 1000), _res("R2", 1000), _res("R3", 1000), _res("R4", 1000),
            _task("T1", ["R1"], 240, "manual", 1, 3),     # panel preparation
            _task("T2", ["R2"], 300, "manual", 1, 3),     # mainboard mounting
            _task("T3", ["R3"], 180, "manual", 1, 2),     # power-supply fitting
            _task("T4", ["T1", "T2", "T3"], 240, "manual", 1, 3),  # chassis assembly
            _task("T5", ["T4"], 144, "machine", 1, 1),    # automated wiring test
            _task("T6", ["T5", "R4"], 210, "manual", 1, 2),  # back-cover fixing
            _task("T7", ["T6"], 150, "manual", 1, 2),     # final QC
            _task("T8", ["T7"], 120, "machine", 1, 1),    # packaging
        ],
    },
    "converging_3_station": {
        "name": "Standard 3-Station Converging Line",
        "description": "Two feeder branches (A, B) merging into a final assembly (C).",
        "parameters": {"available_workers": 10, "target_quantity": 200, "shift_hours": 8,
                       "unit_price": 50, "worker_salary": 30, "wip_holding_cost": 0.5,
                       "n_iterations": 200},
        "table": [
            _res("R1", 1000), _res("R2", 1000),
            _task("A1", ["R1"], 180, "manual", 1, 2),
            _task("A2", ["A1"], 120, "manual", 1, 2),
            _task("B1", ["R2"], 240, "manual", 1, 2),
            _task("C1", ["A2", "B1"], 180, "manual", 1, 2),
            _task("C2", ["C1"], 120, "machine", 1, 1),
        ],
    },
    "serial_assembly_line": {
        "name": "Serial Modular Assembly Line",
        "description": "A single serial chain of 6 operations fed by one raw-material stock.",
        "parameters": {"available_workers": 12, "target_quantity": 200, "shift_hours": 8,
                       "unit_price": 45, "worker_salary": 25, "wip_holding_cost": 0.3,
                       "n_iterations": 200},
        "table": [
            _res("R1", 1000),
            _task("S1", ["R1"], 150, "manual", 1, 2),
            _task("S2", ["S1"], 180, "manual", 1, 3),
            _task("S3", ["S2"], 120, "machine", 1, 1),
            _task("S4", ["S3"], 210, "manual", 1, 3),
            _task("S5", ["S4"], 120, "manual", 1, 2),
            _task("S6", ["S5"], 90, "manual", 1, 1),
        ],
    },
}
