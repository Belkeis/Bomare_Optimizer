import sys
import os

# Ensure the root directory (C:\Bomare) is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import pandas as pd
from engine.graph_manager import GraphManager


# ==============================================================================
# TEST 1: Happy Path (Valid Assembly Line)
# ==============================================================================
def test_valid_factory_line():
    """Verifies that a standard 3-task assembly line builds correctly and orders tasks."""
    print("\n--- Running: Test Valid Factory Line ---")
    
    # Assembly line: Task 10 -> Task 20 -> Task 30
    excel_data = {
        'Id': [10, 20, 30],
        'CycleTime': [15, 20, 10],
        'Relation': [None, 10, 20]
    }
    df = pd.DataFrame(excel_data)
    manager = GraphManager(df)

    # 1. Check total number of stations/tasks
    assert len(manager.graph.nodes) == 3
    
    # 2. Check that execution order respects dependencies (10 before 20, 20 before 30)
    order = manager.get_topological_order()
    print(f"Computed Execution Order: {order}")
    assert order == [10, 20, 30]


# ==============================================================================
# TEST 2: Excel String Parsing (Handling '[10]' or '[20]')
# ==============================================================================
def test_excel_string_parsing():
    """Verifies that stringified lists from Excel cells are converted safely."""
    print("\n--- Running: Test Excel String Parsing ---")
    
    # Excel sometimes stores lists as raw text strings like "[10]"
    excel_data = {
        'Id': [10, 20, 30],
        'CycleTime': [5, 5, 5],
        'Relation': [None, "[10]", "[20]"]
    }
    df = pd.DataFrame(excel_data)
    manager = GraphManager(df)

    # Verify that Task 30 correctly identifies Task 20 as its predecessor
    predecessors = list(manager.graph.predecessors(30))
    print(f"Task 30 Dependencies: {predecessors}")
    assert predecessors == [20]


# ==============================================================================
# TEST 3: Station Group Validation (Weak Connectivity)
# ==============================================================================
def test_station_grouping_logic():
    """Verifies whether a subset of tasks can be physically assigned to one workstation."""
    print("\n--- Running: Test Station Grouping Logic ---")
    
    # Flow: 10 -> 20 -> 30, and Task 99 is completely disconnected
    excel_data = {
        'Id': [10, 20, 30, 99],
        'CycleTime': [10, 10, 10, 10],
        'Relation': [None, 10, 20, None]
    }
    df = pd.DataFrame(excel_data)
    manager = GraphManager(df)

    # Task 10 and 20 are connected -> Valid station
    assert manager.is_valid_station_group([10, 20]) is True
    
    # Task 10 and 99 are disconnected -> Invalid station
    assert manager.is_valid_station_group([10, 99]) is False
    print("Station Grouping Checks Passed!")


# ==============================================================================
# TEST 4: Constraint Violation — Circular Loop
# ==============================================================================
def test_detects_circular_loop():
    """Verifies that circular dependencies (10 -> 20 -> 10) trigger an error."""
    print("\n--- Running: Test Circular Loop Detection ---")
    
    excel_data = {
        'Id': [10, 20],
        'CycleTime': [10, 10],
        'Relation': [20, 10]  # Loop!
    }
    df = pd.DataFrame(excel_data)

    # Must raise a ValueError
    with pytest.raises(ValueError, match="Circular dependencies detected"):
        GraphManager(df)
    print("Successfully blocked circular dependency!")


# ==============================================================================
# TEST 5: Constraint Violation — Branching (Out-Degree > 1)
# ==============================================================================
def test_detects_illegal_branching():
    """Verifies that splitting one task into multiple paths triggers an error."""
    print("\n--- Running: Test Branching Detection ---")
    
    excel_data = {
        'Id': [10, 20, 30],
        'CycleTime': [10, 10, 10],
        'Relation': [None, 10, 10]  # Task 10 branches into both 20 AND 30
    }
    df = pd.DataFrame(excel_data)

    # Must raise a ValueError
    with pytest.raises(ValueError, match="branches into multiple paths"):
        GraphManager(df)
    print("Successfully blocked illegal line branching!")