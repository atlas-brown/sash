import argparse
import json
import logging
import pathlib
import traceback

import sash.symb
from sash.config import Config
from sash.reporter import Reporter, Report
from sash.solver import run_solver
from sash.interpreter_config import InterpConfig


def main(file: str, debug=False, solver=False) -> Report:
    if debug:
        logging.basicConfig(
            format="[%(filename)s:%(lineno)d] %(message)s", level=logging.DEBUG
        )
        Config.set("DEBUG", True)
    else:
        logging.basicConfig(level=logging.CRITICAL)

    logging.info(f"Processing file {file}")
    Reporter.initialize(file)
    config = InterpConfig(trace_collapser = sash.symb.collapse_traces_if_too_many)

    try:
        traces = sash.symb.symbexec_file(file, config)
        if solver:
            run_solver(traces, config)
        report = Reporter.get_report()
        logging.info("Symbolic execution completed successfully")
        logging.info(f"Time taken: {str(report.time)}")
        return report
    except Exception:
        logging.error("Symbolic execution failed")
        logging.error(f"{traceback.format_exc()}")
        raise SystemExit(1)


def cli_main():
    args = parse_cli()
    report = main(args.filename.resolve(strict=True).as_posix(), debug=args.debug, solver=args.solver)
    print(json.dumps(report, indent=2))


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
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    parser.add_argument(
        "--solver",
        action="store_true",
        help="Enable the solver and get additional reports",
    )


    return parser.parse_args()


if __name__ == "__main__":
    cli_main()
