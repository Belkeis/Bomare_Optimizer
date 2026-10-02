# Bomare Optimization And Simulation Project

A web dashboard for **designing and optimizing assembly lines**. Describe your tasks, raw materials, and costs, and finds a good station layout and worker assignment, then simulates a shift to show the expected output, WIP, and profit.

## How it works

1. **Input** – Enter (or import) a table of tasks and resources with their precedence relations, cycle times, and operator limits.
2. **Balance** – A line balancer builds feasible station layouts based on takt time and available workers.
3. **Optimize** – Simulated annealing searches for better layouts (changing crew sizes, splitting, and merging stations).
4. **Simulate** – A discrete-event (next-event, delta-t) simulator runs a shift for each candidate and scores it on revenue, worker cost, and WIP holding cost.
5. **Results** – The dashboard shows the precedence graph (DAG), the best layout, and key metrics.




## Using the dashboard

- Set production targets: workers, target quantity, shift duration, unit revenue, worker salary, and WIP holding cost.
- Fill the assembly line table by hand, **load a preset**, or **import a CSV**.
- Click optimize to see the results. The table can be exported back to CSV.

## Quick start

```bash
pip install -r requirements.txt
python run.py
```

Then open **http://localhost:5000**.


### Table columns

| Column | Meaning |
| --- | --- |
| `Id` | Unique name of the task or resource (e.g. `T1`, `R1`) |
| `Type` | `Task` or `Resource` (raw material stock) |
| `Relation` | Predecessor ids (e.g. `T1, T2`) |
| `CycleTime` | Task duration in seconds |
| `Quantity` | Units consumed per cycle |
| `Available` | Starting stock (resources only) |
| `operation_type` | `manual` or `machine` |
| `min_operators` / `max_operators` | Worker range for the task |

### Line rules

- No circular dependencies.
- Every row feeds **at most one** other row (a converging tree, no branching).
- Exactly one final task.


## API

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/` | Dashboard |
| GET | `/api/templates` | Preset tables |
| POST | `/api/validate` | Validate a table |
| POST | `/api/optimize` | Run optimization and simulation |
| POST | `/api/export-csv` | Download table as CSV |
| POST | `/api/import-csv` | Upload CSV, get table back |
| GET | `/health` | Health check |

## Project structure

```
Bomare/
├── run.py                     # Start the app
├── server.py                  # Flask app and API routes
├── input_handler.py           # Input cleaning, validation, and pipeline glue
├── presets.py                 # Example assembly lines
├── engine/
│   ├── delta_t_simulator.py   # Discrete-event shift simulator
│   └── graph_manager.py       # DAG building and validation
├── optimization/
│   ├── line_generator.py      # Takt time and station balancing
│   ├── search_runner.py       # Simulated annealing search
│   └── final_line_generator.py
├── static/                    # Frontend (index.html, app.js, style.css)

```

## Requirements

Python 3.12+ (recommended), Flask, pandas, numpy, networkx. pytest is optional.

