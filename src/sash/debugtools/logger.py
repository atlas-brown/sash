# Need:
# a logger constructor: filepath -> logger instance
# logging methods, which log json to the file:
# - every line of the program-under-analysis reached with a list of all active state IDs at that point. This will also include a "pass ID" field, since the engine does multiple analysis passes over the program.
# - the details of every unique state reached
# - a trace extension relation: pairs of states where the predecessor state becomes the successor state as part of an execution trace
# - Every assertion generated (which have a line number, and an assertion formula, a state ID, and a pass ID)
# - Every error report generated (which have a line number, ID, and message)

# Need a cache of logged states to avoid duplicates

import json
from pathlib import Path
from typing import Any, Iterable, Optional
from sash.constraints import Empty
from pprint import pformat
from sash.debugtools.common import get_debugtools_dir

State = 'State'
Trace = 'Trace'
Traces = Iterable[Trace]
Assertion = 'Assertion'
Issue = 'Issue'

def pretty(obj: Any) -> str:
    if hasattr(obj, 'pretty'):
        return obj.pretty()
    elif isinstance(obj, tuple) and len(obj) > 0:
        return "(" + "\n ,\n ".join(pretty(x) for x in obj) + "\n)"
    else:
        return pformat(obj)

class DebugLogger:
    default_log_file = "interp_log.jsonl"

    # Class-level singleton instance
    _instance: Optional['DebugLogger'] = None
    _initialized: bool = False

    def __init__(self):
        # Private constructor - use initialize() instead
        self._path: Optional[Path] = None
        self._fh: Optional[Any] = None
        self._state_cache: set[Any] = set()
        self._ancestor_cache: set[tuple[Any, Any]] = set()

    @classmethod
    def initialize(cls, source_file: str | Path, logpath: str | Path | None = None) -> None:
        """Initialize the singleton logger. Must be called before logging methods will work."""
        if cls._instance is None:
            cls._instance = cls()

        if logpath is None:
            debugtools_dir = get_debugtools_dir()
            if debugtools_dir is not None:
                logpath = debugtools_dir / cls.default_log_file
            else:
                logpath = cls.default_log_file

        cls._instance._path = Path(logpath)
        cls._instance._path.parent.mkdir(parents=True, exist_ok=True)
        # clear an old log file if it exists
        cls._instance._path.write_text("")
        cls._instance._fh = cls._instance._path.open("a", encoding="utf-8")
        cls._instance._state_cache.clear()
        cls._instance._ancestor_cache.clear()
        cls._initialized = True
        cls._log_source_file(source_file)

    @classmethod
    def close(cls) -> None:
        """Close the logger file handle."""
        if cls._instance and cls._instance._fh and not cls._instance._fh.closed:
            cls._instance._fh.close()
        cls._initialized = False

    @classmethod
    def __enter__(cls) -> type['DebugLogger']:
        return cls

    @classmethod
    def __exit__(cls, exc_type, exc, tb) -> None:
        cls.close()

    @classmethod
    def _log(cls, record_type: str, payload: dict[str, Any]) -> None:
        """Internal logging method - only logs if initialized."""
        if not cls._initialized or cls._instance is None or cls._instance._fh is None:
            return
        cls._instance._fh.write(json.dumps({"type": record_type, **payload}, ensure_ascii=False) + "\n")
        cls._instance._fh.flush()

    @classmethod
    def _log_source_file(cls, path: str | Path) -> None:
        """Internal method to log source file."""
        if not cls._initialized:
            return
        code = (Path(path) if not isinstance(path, Path) else path).read_text(encoding="utf-8")
        cls._log("source_file", {"code": code, "path": str(path)})

    @classmethod
    def log_interp_line(cls, line_no: int, active_traces: Traces, pass_id: str) -> None:
        """Log interpreter line with active traces."""
        if not cls._initialized:
            return
        state_ids = {hash(trace.latest_state) for trace in active_traces}
        cls._log("line", {"line": line_no, "states": list(state_ids), "pass": pass_id})

    @classmethod
    def _log_state(cls, state: State) -> None:
        """Internal method to log a state."""
        if not cls._initialized or cls._instance is None:
            return
        state_id = hash(state)
        if state_id in cls._instance._state_cache:
            return
        cls._instance._state_cache.add(state_id)
        cls._log("state", {"id": state_id, "details": {k: pretty(v) for k, v in state.__dict__.items()}})

    @classmethod
    def _log_traces(cls, traces: Traces) -> None:
        """Internal method to log multiple traces."""
        if not cls._initialized or cls._instance is None:
            return
        for trace in traces:
            if hash(trace.latest_state) in cls._instance._state_cache:
                continue
            cls._log_trace(trace)

    @classmethod
    def _log_trace(cls, trace: Trace) -> None:
        """Internal method to log a single trace."""
        if not cls._initialized:
            return
        for i in range(len(trace.states) - 1):
            predecessor = trace.states[i]
            successor = trace.states[i + 1]
            cls.log_trace_extension(predecessor, successor)

    @classmethod
    def log_trace_extension(cls, from_state: State, to_state: State) -> None:
        """Log a trace extension from one state to another."""
        if not cls._initialized or cls._instance is None:
            return
        from_id = hash(from_state)
        to_id = hash(to_state)
        if (from_id, to_id) in cls._instance._ancestor_cache or from_id == to_id:
            return
        cls._log_state(from_state)
        cls._log_state(to_state)
        cls._log("trace", {"from": from_id, "to": to_id})
        cls._instance._ancestor_cache.add((from_id, to_id))

    @classmethod
    def log_assertion(cls, constraint: 'Constraint', state: State, line: int, pass_id: str) -> None:
        """Log an assertion constraint."""
        if not cls._initialized:
            return
        if constraint != Empty():
            cls._log("assertion", {"line": line, "formula": pformat(constraint), "state": hash(state), "pass": pass_id})

    @classmethod
    def log_issue(cls, issue: Issue, pass_id: str | None = None) -> None:
        """Log an issue report."""
        if not cls._initialized:
            return
        cls._log("report", {"line": issue.source_line, "code": str(issue.code), "message": issue.message, "pass": pass_id})

