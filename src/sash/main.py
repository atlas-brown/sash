import argparse
import json
import logging
import pathlib
import traceback

import sash.symb
from sash.config import Config
from sash.reporter import Reporter


def main(file: str) -> dict | None:
    logging.info(f"Processing file {file}")
    Reporter.initialize(file)

    try:
        sash.symb.symbexec_file(file)
        report_dict = Reporter.get_report()
        logging.info("Symbolic execution completed successfully")
        logging.info(f"Time taken: {str(report_dict['time'])}")
        return report_dict
    except Exception:
        logging.error("Symbolic execution failed")
        logging.error(f"{traceback.format_exc()}")
        raise SystemExit(1)


def cli_main():
    args = parse_cli()

    if args.debug:
        logging.basicConfig(
            format="[%(filename)s:%(lineno)d] %(message)s", level=logging.DEBUG
        )
        Config.set("DEBUG", True)
    else:
        logging.basicConfig(level=logging.CRITICAL)

    logging.debug(f"Received filename: {args.filename.resolve(strict=True)}")
    report = main(args.filename.as_posix())
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

    return parser.parse_args()


if __name__ == "__main__":
    cli_main()
