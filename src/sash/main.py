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


def symbexec_main(file: str,
                  solver: bool = False,
                  stop_event: threading.Event | None = None) -> sash.symb.SymbexecResult:
    config = InterpConfig(trace_collapser = sash.symb.collapse_traces_if_too_many)

    Reporter.initialize(file)
    start_time = time.perf_counter()
    result = sash.symb.symbexec_file(file, config, stop=stop_event)
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

    if solver and (stop_event is None or not stop_event.is_set()):
        start_time = time.perf_counter()
        run_solver(result.traces, config)
        Reporter.set_solver_time(time.perf_counter() - start_time)
    elif solver and stop_event is not None and stop_event.is_set():
        logging.warning("Skipping solver due to timeout during symbolic execution")
    else:
        logging.info("Skipping solver as configured")

    return result


def main(file: str,
         log_level: str = "warning",
         log_file: pathlib.Path | None=None,
         solver=True,
         timeout: float | None = None) -> Report:
    Config.set("DEBUG", log_level.lower() == "debug")
    logging.basicConfig(
        format="[%(asctime)s %(filename)s:%(lineno)d] %(message)s",
        level=getattr(logging, log_level.upper()),
        filename=log_file
    )

    logging.info(f"Processing file {file} with solver={solver} and timeout={timeout}")
    stop_event = threading.Event()
    timer = None
    if timeout is not None and timeout > 0:
        logging.info(f"Setting timeout: {timeout} seconds")
        timer = threading.Timer(timeout, stop_event.set)
        timer.daemon = True
        timer.start()

    symbexec_main(file, solver, stop_event)

    if timer is not None:
        timer.cancel()

    return Reporter.get_report()


def cli_main():
    args = parse_cli()

    report = main(
        args.filename.resolve(strict=True).as_posix(),
        log_level=args.log_level,
        log_file=args.log_file.resolve().as_posix() if args.log_file else None,
        solver=args.solver,
        timeout=args.timeout,
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
        choices=["debug", "info", "warning", "error", "critical"],
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

    return parser.parse_args()


if __name__ == "__main__":
    cli_main()
