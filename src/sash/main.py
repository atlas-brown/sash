import argparse
import json
import logging
import pathlib
import threading
import time

import sash.symb
from sash.interpreter_config import InterpConfig
from sash.reporter import Report, Reporter
from sash.solver import run_solver
import sash.specs as specs
from sash.debugtools.logger import DebugLogger


def symbexec_main(file: str,
                  solver: bool = False,
                  symbexec_timeout: float | None = None,
                  dfs_timeout: float | None = None,
                  solver_timeout: float | None = None,
                  enable_dfs: bool = False,
                  debug_instrumentation: bool = False) -> sash.symb.SymbexecResult:
    global timers
    timers = []

    if debug_instrumentation:
        logging.info(f"Debug instrumentation enabled: detailed json execution logging to {DebugLogger.default_log_file}")
        DebugLogger.initialize(file)

    config = InterpConfig(trace_collapser = sash.symb.collapse_traces_if_too_many,
                          debug_instrumentation = debug_instrumentation,
                          DFS_first = enable_dfs)

    Reporter.initialize(file)
    start_time = time.perf_counter()
    if enable_dfs and dfs_timeout is None:
        dfs_timeout = symbexec_timeout
    stop = None
    result = sash.symb.symbexec_file(
        file,
        config,
        stop=stop,
        dfs_timeout=dfs_timeout,
        main_timeout=symbexec_timeout,
    )
    Reporter.set_exec_time(time.perf_counter() - start_time)

    match result.status:
        case sash.symb.SymbexecStatus.COMPLETED:
            logging.info("Symbolic execution completed")
        case sash.symb.SymbexecStatus.INTERRUPTED:
            logging.warning("Symbolic execution timed out; got partial results")
        case sash.symb.SymbexecStatus.FAILED:
            logging.error("Symbolic execution failed; exiting")
            raise SystemExit(1)
        case _:
            assert False, "unreachable"

    if solver:
        logging.info("Running solver")
        start_time = time.perf_counter()
        run_solver(result.traces, config, stop=set_timer(solver_timeout, "solver"))
        Reporter.set_solver_time(time.perf_counter() - start_time)
        logging.info("Solver finished running")
    else:
        logging.info("Skipping solver")

    return result


def main(file: str,
         log_level: str = "warning",
         log_file: pathlib.Path | None=None,
         solver=True,
         timeout: float | None = None,
         dfs_timeout: float | None = None,
         solver_timeout: float | None = None,
         enable_dfs: bool = False,
         debug_instrumentation: bool = False) -> Report:

    logging.basicConfig(
        format="[%(levelname)s:%(module)s:%(lineno)d] %(message)s",
        level=getattr(logging, log_level.upper()) if log_level.lower() != "disabled" else logging.CRITICAL + 10,
        filename=log_file
    )

    logging.info("Processing file %s with solver=%s, exec_timeout=%s, solver_timeout=%s", file, solver, timeout, solver_timeout)
    logging.info("Commands with specs: %s", [name for name, _ in specs.CMD_SPECS.items()])

    symbexec_main(file, solver, timeout, dfs_timeout, solver_timeout, enable_dfs, debug_instrumentation)
    return Reporter.get_report()


def cli_main():
    args = parse_cli()

    report = main(
        args.filename.resolve(strict=True).as_posix(),
        log_level=args.log_level,
        log_file=args.log_file.resolve().as_posix() if args.log_file else None,
        solver=True,
        timeout=args.timeout,
        dfs_timeout=args.dfs_timeout,
        solver_timeout=args.solver_timeout,
        enable_dfs=args.enable_dfs,
        debug_instrumentation=args.enable_debug_instrumentation,
    )

    print(json.dumps(report.to_dict(), indent=2))


def parse_cli():
    parser = argparse.ArgumentParser(
        description="Static analysis for POSIX shell scripts",
    )

    parser.add_argument(
        "filename",
        type=pathlib.Path,
        help="Path to the shell script to analyze",
    )

    parser.add_argument(
        "-L",
        "--log-level",
        type=str,
        default="warning",
        choices=["debug", "info", "warning", "error", "critical", "disabled"],
        help="Set the logging level (default: warning)",
    )

    parser.add_argument(
        "-l",
        "--log-file",
        type=pathlib.Path,
        default=None,
        help="Path to a file to write logs to (default: stdout)",
    )

    parser.add_argument(
        "-D",
        "--enable-dfs",
        action="store_true",
        help="Use depth-first search strategy for symbolic execution (default: breadth-first search)",
    )

    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=None,
        help="Set a timeout (in seconds) for the symbolic execution (not including the solver step)",
    )

    parser.add_argument(
        "-dfsT",
        type=float,
        dest="dfs_timeout",
        default=None,
        help="Set a timeout (in seconds) for the DFS-first phase only (defaults to --timeout when -D is used)",
    )

    # solver timeout
    parser.add_argument(
        "-T",
        "--solver-timeout",
        type=float,
        default=None,
        help="Set a timeout (in seconds) for the solver step",
    )

    # enable debug instrumentation flag
    parser.add_argument(
        "-I",
        "--enable-debug-instrumentation",
        action="store_true",
        help="Enable debug instrumentation (for development purposes)",
    )

    return parser.parse_args()


timers = [] # keep references to timers to prevent garbage collection
def set_timer(timeout: float | None, name: str) -> threading.Event | None:
    stop_event = threading.Event()
    if timeout is None or timeout <= 0:
        return None
    timer = threading.Timer(timeout, stop_event.set)
    timer.daemon = True
    timers.append(timer)
    timer.start()
    return stop_event


if __name__ == "__main__":
    cli_main()
