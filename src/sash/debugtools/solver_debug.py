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
from dataclasses import dataclass, field
from typing import Any, Optional, Literal
from pathlib import Path
from pprint import pformat

import z3

from sash.debugtools.common import get_debugtools_dir
from sash.fs import FSModelSimple, FileInfo

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
    issue: str
    
    # Level 2: Detailed constraint info
    assertion_formula: str
    pathcond_constraints: list[str]
    fs_state_desc: str
    
    # Level 3: Solver info (only if not skipped)
    arb_z3_map: dict[str, str]  # variable name -> z3 representation
    solver_result: Optional[SolverResult] = None
    # Structured FS state graph (optional, only for FSModelSimple)
    fs_state_tree: Optional[dict[str, Any]] = None

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
                'issue': debug_info.issue,
                'pathcond_constraints': debug_info.pathcond_constraints,
                'fs_state_desc': debug_info.fs_state_desc,
                'fs_state_tree': debug_info.fs_state_tree,
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
                          issue: Any,
                          arb_z3_map: Optional[dict[Any, Any]],
                          result_type: str,
                          state_formula: Any,
                          solver_time: float = 0.0,
                          unsat_core: Optional[list[Any]] = None,
                          sat_model: Optional[Any] = None) -> AssertionDebugInfo:
    """Construct an AssertionDebugInfo from raw solver data."""
    pathcond_constraints = [pformat(pc) for pc in state_formula.children()[1].children()]
    fs_state_desc = pformat(assertion.producing_state.fs_model)

    # Try to build a structured FS state tree for FSModelSimple
    fs_state_tree: Optional[dict[str, Any]] = None
    try:
        fs_model = assertion.producing_state.fs_model
        if isinstance(fs_model, FSModelSimple):
            fs_state_tree = _build_fs_state_tree(fs_model)
    except Exception:
        logging.exception("Failed to build fs_state_tree for assertion")

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
        issue=str(issue),
        pathcond_constraints=pathcond_constraints,
        fs_state_desc=fs_state_desc,
        fs_state_tree=fs_state_tree,
        arb_z3_map=readable_arb_map,
        solver_result=solver_result,
    )


def log_assertion_result(assertion: Any,
                         assertion_formula: Any,
                         state_formula: Any,
                         issue: Any,
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
        state_formula=state_formula,
        issue=issue,
        arb_z3_map=arb_z3_map,
        result_type=result_type,
        solver_time=solver_time,
        unsat_core=unsat_core,
        sat_model=sat_model,
    )
    dbg.log_assertion(info)


@dataclass
class _FSStateNode:
    """Internal helper representing one concrete-ish FS snapshot along a branch."""
    fs_var: z3.ArrayRef
    paths: dict[str, tuple[str, str]]  # path -> (state, status)
    conditions: list[str]


def _extract_fileinfo_state_status(val: z3.ExprRef) -> tuple[str, str]:
    """Attempt to extract (state, status) from a FileInfo value.

    Falls back to stringifying the whole value if it is not a mk_pair(...).
    """
    try:
        if z3.is_app(val) and val.decl() == FileInfo.mk_pair and val.num_args() == 2:
            state, status = val.arg(0), val.arg(1)
            return str(state), str(status)
    except Exception:
        pass
    return str(val), ""


def _build_fs_state_tree(fs_model: FSModelSimple) -> dict[str, Any]:
    """Build a simple tree of FS states from an FSModelSimple.

    The result is JSON-serializable and intended for the HTML viewer.
    Each branch corresponds to one possible leaf after following all Ifs.
    """

    history = fs_model.history
    if not history:
        return {"root_fs": None, "final_fs": None, "branches_by_fs": {}, "fs_order": []}

    # Map each FS array variable to the list of possible concrete-ish states.
    fs_states: dict[z3.ArrayRef, list[_FSStateNode]] = {}

    # Ensure we have at least one node for the initial fs var.
    root_fs, root_expr = history[0]
    if root_expr is None:
        fs_states[root_fs] = [
            _FSStateNode(fs_var=root_fs, paths={}, conditions=[])
        ]
    else:
        # Even if it's a K(...), we don't need to track it explicitly yet.
        fs_states[root_fs] = [
            _FSStateNode(fs_var=root_fs, paths={}, conditions=[])
        ]

    for fs_var, arr_expr in history[1:]:
        # Some entries might not have an associated expression (should be rare).
        if arr_expr is None:
            # Just propagate previous mapping if we have it.
            fs_states[fs_var] = fs_states.get(fs_var, fs_states.get(root_fs, []))
            continue

        try:
            decl_kind = arr_expr.decl().kind() if z3.is_app(arr_expr) else None
        except Exception:
            decl_kind = None

        # Store: update path in all predecessor states.
        if decl_kind == z3.Z3_OP_STORE and arr_expr.num_args() == 3:
            base_fs, key_expr, val_expr = arr_expr.arg(0), arr_expr.arg(1), arr_expr.arg(2)
            prev_nodes = fs_states.get(base_fs) or [
                _FSStateNode(fs_var=base_fs, paths={}, conditions=[])
            ]
            path_str = str(key_expr)
            state_str, status_str = _extract_fileinfo_state_status(val_expr)

            new_nodes: list[_FSStateNode] = []
            for node in prev_nodes:
                new_paths = dict(node.paths)
                new_paths[path_str] = (state_str, status_str)
                new_nodes.append(
                    _FSStateNode(fs_var=fs_var, paths=new_paths, conditions=list(node.conditions))
                )
            fs_states[fs_var] = new_nodes
            continue

        # If: branch into then/else FS states.
        if decl_kind == z3.Z3_OP_ITE and arr_expr.num_args() == 3:
            cond, then_fs, else_fs = arr_expr.arg(0), arr_expr.arg(1), arr_expr.arg(2)
            cond_str = pformat(cond)

            new_nodes: list[_FSStateNode] = []

            then_nodes = fs_states.get(then_fs) or [
                _FSStateNode(fs_var=then_fs, paths={}, conditions=[])
            ]
            for node in then_nodes:
                new_nodes.append(
                    _FSStateNode(
                        fs_var=fs_var,
                        paths=dict(node.paths),
                        conditions=list(node.conditions) + [cond_str],
                    )
                )

            else_nodes = fs_states.get(else_fs) or [
                _FSStateNode(fs_var=else_fs, paths={}, conditions=[])
            ]
            for node in else_nodes:
                new_nodes.append(
                    _FSStateNode(
                        fs_var=fs_var,
                        paths=dict(node.paths),
                        conditions=list(node.conditions) + [f"NOT ({cond_str})"],
                    )
                )

            # If pruning removed both branches (shouldn't happen), fall back to a single unknown state.
            if not new_nodes:
                new_nodes = [
                    _FSStateNode(fs_var=fs_var, paths={}, conditions=[cond_str])
                ]

            fs_states[fs_var] = new_nodes
            continue

        # For other expression shapes (e.g., K arrays or something unexpected),
        # just treat the FS as inheriting the previous state's paths unchanged.
        prev_nodes = []
        # Prefer to copy from the immediately preceding fs in history, if any.
        for prev_fs, _ in reversed(history):
            if prev_fs in fs_states:
                prev_nodes = fs_states[prev_fs]
                break
        if not prev_nodes:
            prev_nodes = [_FSStateNode(fs_var=fs_var, paths={}, conditions=[])]
        fs_states[fs_var] = [
            _FSStateNode(fs_var=fs_var, paths=dict(n.paths), conditions=list(n.conditions))
            for n in prev_nodes
        ]

    # Build branch lists for every FS version we know about, in history order.
    branches_by_fs: dict[str, list[dict[str, Any]]] = {}
    fs_order: list[str] = []

    for fs_var, _ in history:
        fs_key = str(fs_var)
        fs_order.append(fs_key)
        nodes = fs_states.get(fs_var, [])
        branch_list: list[dict[str, Any]] = []
        for idx, node in enumerate(nodes):
            paths_serialized = [
                {"path": path, "state": state, "status": status}
                for path, (state, status) in sorted(node.paths.items(), key=lambda kv: kv[0])
            ]
            branch_list.append(
                {
                    "id": idx,
                    "fs_id": str(node.fs_var),
                    "conditions": node.conditions,
                    "paths": paths_serialized,
                }
            )
        branches_by_fs[fs_key] = branch_list

    final_fs, _ = history[-1]

    return {
        "root_fs": str(root_fs),
        "final_fs": str(final_fs),
        "branches_by_fs": branches_by_fs,
        "fs_order": fs_order,
        # Backwards-compat: keep top-level 'branches' for the final FS.
        "branches": branches_by_fs.get(str(final_fs), []),
    }


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
