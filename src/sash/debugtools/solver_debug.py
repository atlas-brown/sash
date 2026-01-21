"""
Structured logging for solver debugging.
Logs solver events to JSON for visualization in the debug viewer.

Hierarchical structure:
- Level 1 (Assertion): source code location, overall outcome
- Level 2 (Details): assertion formula, path conditions, filesystem state
- Level 3 (Solver): arb z3 map, unsat core / sat model (only for non-skipped)
"""

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Optional, Literal
from pathlib import Path
from pprint import pformat
from sash.debugtools.common import get_debugtools_dir

@dataclass
class SolverResult:
    """Result from Z3 solver for a single assertion"""
    result_type: Literal["UNSAT", "SAT", "PATHCOND_UNSAT"]  # outcome
    unsat_core: Optional[list[str]] = None  # Only for UNSAT
    sat_model: Optional[str] = None  # Only for SAT
    solver_time: float = 0.0

@dataclass
class AssertionDebugInfo:
    """Complete debug info for a single assertion check"""
    timestamp: float
    assertion_id: int
    source_str: str
    source_line: int
    
    # Level 2: Detailed constraint info
    assertion_formula: str
    pathcond_constraints: list[str]
    fs_state_desc: str
    
    # Level 3: Solver info (only if not skipped)
    arb_z3_map: dict[str, str]  # variable name -> z3 representation
    solver_result: Optional[SolverResult] = None

class SolverDebugger:
    """Collects hierarchical debug info from the solver"""
    
    def __init__(self, output_file: str | None = None):
        if output_file is None:
            debugtools_dir = get_debugtools_dir()
            if debugtools_dir is not None:
                output_file = debugtools_dir / "solver_debug.jsonl"
            else:
                output_file = "solver_debug.jsonl"
        self.output_file = Path(output_file)
        self.assertions: list[AssertionDebugInfo] = []
        self._setup_file()
    
    def _setup_file(self):
        """Initialize the output file"""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        # Clear the file if it exists
        self.output_file.write_text("")
    
    def log_assertion(self, debug_info: AssertionDebugInfo):
        """Log a complete assertion with all hierarchical info"""
        self.assertions.append(debug_info)
        # Write to file immediately (JSONL format - one JSON per line)
        with open(self.output_file, 'a') as f:
            # Convert to dict, handling non-serializable types
            data = {
                'timestamp': debug_info.timestamp,
                'assertion_id': debug_info.assertion_id,
                'source_str': debug_info.source_str,
                'source_line': debug_info.source_line,
                'assertion_formula': debug_info.assertion_formula,
                'pathcond_constraints': debug_info.pathcond_constraints,
                'fs_state_desc': debug_info.fs_state_desc,
                'arb_z3_map': debug_info.arb_z3_map,
                'solver_result': {
                    'result_type': debug_info.solver_result.result_type,
                    'unsat_core': debug_info.solver_result.unsat_core,
                    'sat_model': debug_info.solver_result.sat_model,
                    'solver_time': debug_info.solver_result.solver_time,
                } if debug_info.solver_result else None
            }
            f.write(json.dumps(data) + '\n')
    
    def get_summary(self):
        """Get a summary of all logged assertions"""
        if not self.assertions:
            return {"total_assertions": 0}
        
        outcomes = {}
        for info in self.assertions:
            if info.solver_result:
                result = info.solver_result.result_type
            else:
                result = "SKIPPED"
            outcomes[result] = outcomes.get(result, 0) + 1
        
        return {
            "total_assertions": len(self.assertions),
            "outcomes": outcomes,
            "output_file": str(self.output_file)
        }


def _build_assertion_info(assertion: Any,
                          assertion_formula: Any,
                          arb_z3_map: Optional[dict[Any, Any]],
                          result_type: str,
                          solver_time: float = 0.0,
                          unsat_core: Optional[list[Any]] = None,
                          sat_model: Optional[Any] = None) -> AssertionDebugInfo:
    """Construct an AssertionDebugInfo from raw solver data."""
    pathcond_constraints = [pformat(pc.constraint) for pc in assertion.producing_state.pathcond]
    fs_state_desc = pformat(assertion.producing_state.fs_model)

    # Make the arbitrary map readable and truncatable for the UI filter
    readable_arb_map = {}
    if arb_z3_map:
        for key, val in arb_z3_map.items():
            readable_arb_map[str(key)] = str(val)

    solver_result: Optional[SolverResult]
    if result_type == "UNSAT":
        solver_result = SolverResult(
            result_type="UNSAT",
            unsat_core=[pformat(c) for c in (unsat_core or [])],
            solver_time=solver_time,
        )
    elif result_type == "SAT":
        solver_result = SolverResult(
            result_type="SAT",
            sat_model=pformat(sat_model) if sat_model is not None else None,
            solver_time=solver_time,
        )
    else:  # PATHCOND_UNSAT or any other skip-like outcome
        solver_result = SolverResult(result_type="PATHCOND_UNSAT", solver_time=solver_time)

    return AssertionDebugInfo(
        timestamp=time.time(),
        assertion_id=id(assertion),
        source_str=assertion.source_str or "unknown",
        source_line=assertion.source_line or -1,
        assertion_formula=pformat(assertion_formula),
        pathcond_constraints=pathcond_constraints,
        fs_state_desc=fs_state_desc,
        arb_z3_map=readable_arb_map,
        solver_result=solver_result,
    )


def log_assertion_result(assertion: Any,
                         assertion_formula: Any,
                         arb_z3_map: Optional[dict[Any, Any]],
                         result_type: str,
                         solver_time: float = 0.0,
                         unsat_core: Optional[list[Any]] = None,
                         sat_model: Optional[Any] = None,
                         debugger: Optional[SolverDebugger] = None) -> None:
    """Convenience entry point for the solver: build and log a single assertion result."""
    dbg = debugger or get_debugger()
    info = _build_assertion_info(
        assertion=assertion,
        assertion_formula=assertion_formula,
        arb_z3_map=arb_z3_map,
        result_type=result_type,
        solver_time=solver_time,
        unsat_core=unsat_core,
        sat_model=sat_model,
    )
    dbg.log_assertion(info)


# Global debugger instance
_debugger: Optional[SolverDebugger] = None

def get_debugger(output_file: str | None = None) -> SolverDebugger:
    """Get or create the global debugger instance"""
    global _debugger
    if _debugger is None:
        _debugger = SolverDebugger(output_file)
    return _debugger

def reset_debugger():
    """Reset the global debugger (useful for testing)"""
    global _debugger
    _debugger = None
