import argparse
import json
import logging
import pathlib
import sys
import threading
import time
from inspect import signature

import sash.symb
from sash.interpreter_config import InterpConfig
from sash.reporter import Report, Reporter
from sash.solver import reset_z3cache, run_solver
import sash.specs as specs
from sash.debugtools.logger import DebugLogger
from typing import Literal
from sash.formatters import JSONFormatter

def build_cli(options_only=False) -> argparse.ArgumentParser:
    SHOW_ADVANCED_HELP_STRS = {"a", "advanced"}

    # Custom help action to hide advanced options unless the user
    # passed -h/--help with one of the SHOW_ADVANCED_HELP_STRS values
    def make_custom_help_action(*advanced_groups: argparse._ArgumentGroup) -> type[argparse.Action]:
        class HelpAction(argparse.Action):
            def __call__(self, parser, namespace, values, option_string=None):
                if values not in SHOW_ADVANCED_HELP_STRS:
                    # Suppress all advanced groups and their actions from the help message
                    for group in advanced_groups:
                        group.title = None
                        group.description = None
                        for action in group._group_actions:
                            action.help = argparse.SUPPRESS
                parser.print_help()
                parser.exit()
        return HelpAction

    parser = argparse.ArgumentParser(
        description="Static analysis for POSIX shell scripts",
        add_help=False,  # Needed to allow defining a -h/--help argument
    )

    # Group advanced options by category
    timeouts_grp = parser.add_argument_group("timeout options (advanced)", "Options for configuring timeouts for different phases of the analysis")
    execution_grp = parser.add_argument_group("execution options (advanced)", "Options for configuring the symbolic execution phase")
    solver_grp = parser.add_argument_group("solver options (advanced)", "Options for configuring the solver phase")
    logging_grp = parser.add_argument_group("logging options (advanced)", "Options for configuring logging")
    debug_grp = parser.add_argument_group("debug options (advanced)")

    # Basic arguments
    if not options_only:
        parser.add_argument(
            "file",
            type=pathlib.Path,
            help="The shell script to analyze",
        )
    parser.add_argument(
        "-t",
        "--timeout",
        metavar="SEC",
        type=float,
        default=signature(main).parameters["timeout"].default,  # Define like this to avoid duplication of the defaults
        help=f"Timeout budget (in seconds); shared between the execution and solver phases; set to 'inf' for no timeout (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON-compliant output instead of the default user-facing, pretty plain-text output"
    )
    parser.add_argument(
        "-h",
        "--help",
        metavar="advanced",
        nargs="?",
        type=str,
        const="basic",
        choices=["b", "basic", *SHOW_ADVANCED_HELP_STRS],
        action=make_custom_help_action(timeouts_grp, execution_grp, solver_grp, logging_grp, debug_grp),
        help="Show this help message and exit; with "
        + " or ".join(f"'{s}'" for s in SHOW_ADVANCED_HELP_STRS)
        + " also show advanced options",
    )

    # Advanced arguments

    # Timeout-related arguments
    timeouts_grp.add_argument(
        "--exec-timeout-pct",
        metavar="PCT",
        type=float,
        default=signature(main).parameters["exec_timeout_pct"].default,
        help=f"Execution phase timeout budget as a percentage of the total timeout (0.0--1.0) (default: %(default)s)",
    )
    timeouts_grp.add_argument(
        "--dfs-timeout-pct",
        metavar="PCT",
        type=float,
        default=signature(main).parameters["dfs_timeout_pct"].default,
        help=f"DFS passes timeout budget as a percentage of the execution timeout (0.0--1.0) (default: %(default)s)",
    )
    timeouts_grp.add_argument(
        "--targeted-dfs-timeout-pct",
        metavar="PCT",
        type=float,
        default=signature(main).parameters["targeted_dfs_timeout_pct"].default,
        help=f"Targeted DFS pass timeout budget as a percentage of the DFS timeout (0.0--1.0) (default: %(default)s)",
    )

    # Execution-related arguments
    execution_grp.add_argument(
        "--disable-optimistic-forking",
        action="store_true",
        help=f"Force symbolic execution to fork even outside of checked positions",
    )
    execution_grp.add_argument(
        "--disable-trace-collapsing",
        action="store_true",
        help=f"Disable trace collapsing",
    )
    execution_grp.add_argument(
        "--disable-dfs",
        action="store_true",
        help="Disable all DFS passes (equivalent to '--disable-targeted-dfs --disable-unbound-as-empty-dfs')",
    )
    execution_grp.add_argument(
        "--disable-targeted-dfs",
        action="store_true",
        help=f"Disable the DFS pass that prioritizes paths containing potentially dangerous commands",
    )
    execution_grp.add_argument(
        "--disable-unbound-as-empty-dfs",
        action="store_true",
        help=f"Disable the DFS passes that treat unbound variables as empty strings",
    )

    # Solver-related arguments
    solver_grp.add_argument(
        "--disable-solver",
        action="store_true",
        help=f"Disable the solver phase; only run symbolic execution",
    )
    solver_grp.add_argument(
        "--disable-solver-optimizations",
        action="store_true",
        help=f"Disable solver optimizations (assertion prioritization, FS omission, obvious-assertion skipping)",
    )

    # Logging-related arguments
    logging_grp.add_argument(
        "-F",
        "--log-file",
        metavar="FILE",
        type=pathlib.Path,
        default=signature(main).parameters["log_file"].default,
        help=f"File to write logs to; will use stderr if not provided",
    )
    logging_grp.add_argument(
        "-L",
        "--log-level",
        metavar="LEVEL",
        type=str,
        default=signature(main).parameters["log_level"].default,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "DISABLED"],
        help=f"Set the logging level to one of DEBUG, INFO, WARNING, ERROR, CRITICAL or DISABLED (default: %(default)s)",
    )

    # Debug-related arguments
    debug_grp.add_argument(
        "--collect-debug-info",
        action="store_true",
        help=f"Enable debug instrumentation (WARNING: will slow down execution and produce large logs)",
    )

    return parser


def main(file: pathlib.Path,
         timeout: float = 60.0,
         exec_timeout_pct: float = 1/2,
         dfs_timeout_pct: float = 2/3,
         targeted_dfs_timeout_pct: float = 1/1,
         disable_optimistic_forking: bool = False,
         disable_trace_collapsing: bool = False,
         disable_targeted_dfs: bool = False,
         disable_unbound_as_empty_dfs: bool = False,
         disable_solver: bool = False,
         disable_solver_optimizations: bool = False,
         log_file: pathlib.Path | None = None,
         log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "DISABLED"] = "DISABLED",
         collect_debug_info: bool = False) -> Report:

    logging.basicConfig(
        format="[%(levelname)s:%(module)s:%(lineno)d] %(message)s",
        level=getattr(logging, log_level.upper()) if log_level.upper() != "DISABLED" else logging.CRITICAL + 10,
        filename=log_file
    )

    symbexec_main(
        file=file.as_posix(),
        timeout=timeout,
        exec_timeout_pct=exec_timeout_pct,
        dfs_timeout_pct=dfs_timeout_pct,
        targeted_dfs_timeout_pct=targeted_dfs_timeout_pct,
        disable_optimistic_forking=disable_optimistic_forking,
        disable_trace_collapsing=disable_trace_collapsing,
        disable_targeted_dfs=disable_targeted_dfs,
        disable_unbound_as_empty_dfs=disable_unbound_as_empty_dfs,
        disable_solver=disable_solver,
        disable_solver_optimizations=disable_solver_optimizations,
        collect_debug_info=collect_debug_info,
    )

    return Reporter.get_report()


def cli_main():
    args = build_cli().parse_args()

    report = main(
        file=args.file.resolve(strict=True),
        timeout=args.timeout,
        exec_timeout_pct=args.exec_timeout_pct,
        dfs_timeout_pct=args.dfs_timeout_pct,
        targeted_dfs_timeout_pct=args.targeted_dfs_timeout_pct,
        disable_optimistic_forking=args.disable_optimistic_forking,
        disable_trace_collapsing=args.disable_trace_collapsing,
        disable_targeted_dfs=args.disable_dfs or args.disable_targeted_dfs,
        disable_unbound_as_empty_dfs=args.disable_dfs or args.disable_unbound_as_empty_dfs,
        disable_solver=args.disable_solver,
        disable_solver_optimizations=args.disable_solver_optimizations,
        log_file=args.log_file.resolve(strict=True) if args.log_file else None,
        log_level=args.log_level,
        collect_debug_info=args.collect_debug_info,
    )

    if args.json:
        print(JSONFormatter().format(report))
        #print(json.dumps(report.to_dict(), indent=2))
    elif args.log_level != "DISABLED":
        print(report.to_plain_text())
    else:
        compact_output = report.to_compact_text()
        if compact_output:
            print(compact_output)

    sys.exit(1 if report.issues else 0)


def symbexec_main(file: str,
                  timeout: float,
                  exec_timeout_pct: float,
                  dfs_timeout_pct: float,
                  targeted_dfs_timeout_pct: float,
                  disable_optimistic_forking: bool,
                  disable_trace_collapsing: bool,
                  disable_targeted_dfs: bool,
                  disable_unbound_as_empty_dfs: bool,
                  disable_solver: bool,
                  disable_solver_optimizations: bool,
                  collect_debug_info: bool) -> sash.symb.SymbexecResult:
    global timers
    timers = []
    # Per-analysis reset: symbolic execution builds FS formulas using field_to_z3.
    # Reset once at analysis start so symbexec+solver share one cache per run,
    # while avoiding cross-analysis leakage.
    reset_z3cache()

    if collect_debug_info:
        logging.info("Debug instrumentation enabled: detailed JSON execution logging to '%s'", DebugLogger.default_log_file)
        DebugLogger.initialize(file)

    config = InterpConfig(
        trace_collapser=sash.symb.collapse_traces_if_too_many,
        disable_trace_collapsing=disable_trace_collapsing,
        force_fork_all=disable_optimistic_forking,
        debug_instrumentation=collect_debug_info,
        disable_solver_optimizations=disable_solver_optimizations,
        DFS_first=not disable_targeted_dfs and not disable_unbound_as_empty_dfs, # TODO: remove field?
    )

    Reporter.initialize(file)
    # Clamp percentages to [0.0, 1.0]
    exec_timeout_pct = max(0.0, min(exec_timeout_pct, 1.0))
    dfs_timeout_pct = max(0.0, min(dfs_timeout_pct, 1.0))
    targeted_dfs_timeout_pct = max(0.0, min(targeted_dfs_timeout_pct, 1.0))

    disable_solver = disable_solver or exec_timeout_pct == 1.0
    total_timeout = timeout
    symbexec_timeout_cap = total_timeout
    if not disable_solver:
        symbexec_timeout_cap = symbexec_timeout_cap * exec_timeout_pct

    disable_dfs = (disable_targeted_dfs and disable_unbound_as_empty_dfs) or dfs_timeout_pct == 0.0
    dfs_timeout = 0.0
    targeted_dfs_timeout = 0.0
    if not disable_dfs:
        dfs_timeout = symbexec_timeout_cap * dfs_timeout_pct
        targeted_dfs_timeout = dfs_timeout * targeted_dfs_timeout_pct

    logging.info(
        "Analyzing '%s'; solver phase is %s; total timeout budget is %.2fs",
        file,
        "disabled" if disable_solver else "enabled",
        timeout,
    )

    logging.info(
        "Targeted DFS pass is %s; unbound-as-empty DFS passes are %s",
        "disabled" if disable_targeted_dfs else "enabled",
        "disabled" if disable_unbound_as_empty_dfs else "enabled",
    )

    if not disable_solver:
        logging.info(
            "Execution phase budget: at least %.2fs; solver phase budget: at least %.2fs",
            symbexec_timeout_cap,
            total_timeout - symbexec_timeout_cap,
        )

    if not disable_dfs:
        logging.info(
            "Total DFS passes budget: %.2fs; targeted DFS pass budget: %.2fs",
            dfs_timeout,
            targeted_dfs_timeout,
        )

    logging.debug("Commands with specs: %s", [name for name, _ in specs.CMD_SPECS.items()])

    start_time = time.perf_counter()
    stop = None
    result = sash.symb.symbexec_file(
        file=file,
        exec_timeout=symbexec_timeout_cap,
        dfs_timeout=dfs_timeout,
        targeted_dfs_timeout=targeted_dfs_timeout,
        enable_unbound_empty_dfs=not disable_unbound_as_empty_dfs,
        config=config,
        stop=stop,
    )
    exec_elapsed = time.perf_counter() - start_time
    Reporter.set_exec_time(exec_elapsed)

    match result.status:
        case sash.symb.SymbexecStatus.COMPLETED:
            logging.info("Symbolic execution completed in %.2fs", exec_elapsed)
        case sash.symb.SymbexecStatus.INTERRUPTED:
            logging.info("Symbolic execution timed out after %.2fs", exec_elapsed)
        case sash.symb.SymbexecStatus.FAILED:
            logging.error("Symbolic execution failed; exiting")
            assert result.exception is not None
            raise result.exception # Re-raise exception for now
        case _:
            assert False, "unreachable"

    if not disable_solver:
        solver_timeout = total_timeout - exec_elapsed
        if exec_elapsed >= symbexec_timeout_cap:
            logging.info(
                "Execution phase exceeded its timeout cap of %.2fs by %.2fs",
                symbexec_timeout_cap,
                exec_elapsed - symbexec_timeout_cap
            )
            # Since timeout is soft, we give the solver some extra time if exec took too long
            solver_timeout = total_timeout - symbexec_timeout_cap


        logging.info(
            "Starting solver phase with timeout budget: %.2fs",
            solver_timeout,
        )

        start_time = time.perf_counter()
        if solver_timeout <= 0.0:
            solver_stop = threading.Event()
            solver_stop.set()
        else:
            solver_stop = set_timer(solver_timeout, "solver")
        run_solver(result.traces, config, stop=solver_stop)
        Reporter.set_solver_time(time.perf_counter() - start_time)

    return result


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
