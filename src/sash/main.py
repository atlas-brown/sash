import argparse
import json
import logging
import pathlib
import threading
import time

import sash.symb
from sash.config import Config
from sash.interpreter_config import InterpConfig
from sash.reporter import Report, Reporter
from sash.solver import run_solver

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

def symbexec_main(file: str,
                  solver: bool = False,
                  symbexec_timeout: float | None = None,
                  solver_timeout: float | None = None,
                  enable_dfs: bool = False) -> sash.symb.SymbexecResult:
    global timers
    timers = []

    config = InterpConfig(trace_collapser = sash.symb.collapse_traces_if_too_many,
                          DFS_first = enable_dfs)

    Reporter.initialize(file)
    start_time = time.perf_counter()
    result = sash.symb.symbexec_file(file, config, stop=set_timer(symbexec_timeout, "symbexec"))
    Reporter.set_exec_time(time.perf_counter() - start_time)

    match result.status:
        case sash.symb.SymbexecStatus.COMPLETED:
            logging.info("Symbolic execution completed")
        case sash.symb.SymbexecStatus.INTERRUPTED:
            logging.warning("Symbolic execution timed out; running solver with partial results")
        case sash.symb.SymbexecStatus.FAILED:
            logging.error("Symbolic execution failed")
            raise SystemExit(1)
        case _:
            assert False, "unreachable"

    if solver:
        start_time = time.perf_counter()
        run_solver(result.traces, config, stop=set_timer(solver_timeout, "solver"))
        Reporter.set_solver_time(time.perf_counter() - start_time)
    else:
        logging.info("Skipping solver as configured")

    return result


def main(file: str,
         log_level: str = "warning",
         log_file: pathlib.Path | None=None,
         solver=True,
         timeout: float | None = None,
         solver_timeout: float | None = None,
         enable_dfs: bool = False) -> Report:
    Config.set("DEBUG", log_level.lower() == "debug")
    logging.basicConfig(
        format="[%(asctime)s %(filename)s:%(lineno)d] %(message)s",
        level=getattr(logging, log_level.upper()) if log_level.lower() != "disabled" else logging.CRITICAL + 10,
        filename=log_file
    )

    logging.info("Processing file %s with solver=%s and timeout=%s", file, solver, timeout)

    symbexec_main(file, solver, timeout, solver_timeout, enable_dfs)

    return Reporter.get_report()


def cli_main():
    args = parse_cli()

    report = main(
        args.filename.resolve(strict=True).as_posix(),
        log_level=args.log_level,
        log_file=args.log_file.resolve().as_posix() if args.log_file else None,
        solver=args.solver,
        timeout=args.timeout,
        solver_timeout=args.solver_timeout,
        enable_dfs=args.enable_dfs,
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
        "-S",
        "--solver",
        action="store_false",
        help="Enable the solver and get additional reports",
    )

    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=None,
        help="Set a timeout (in seconds) for the symbolic execution (not including the solver step)",
    )

    # solver timeout
    parser.add_argument(
        "--solver-timeout",
        type=float,
        default=None,
        help="Set a timeout (in seconds) for the solver step",
    )

    return parser.parse_args()


if __name__ == "__main__":
    cli_main()
