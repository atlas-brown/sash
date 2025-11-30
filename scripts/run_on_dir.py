#!/usr/bin/env -S uv run python3
import argparse
import pathlib
import subprocess
import sys
from pathlib import Path

import sash.main


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path,
                        help="Directory to search recursively for .sh files")
    parser.add_argument("-t", "--timeout", type=float, default=None,
                        help="Timeout for symbolic execution")
    parser.add_argument("-T", "--solver-timeout", type=float, default=None,
                        help="Solver timeout")
    parser.add_argument("-V", "--verbose", action="store_true",
                        help="Print full reports")
    parser.add_argument("-D", "--enable-dfs", action="store_true",
                        help="Enable DFS branch policy")
    parser.add_argument("-e", "--error-log", type=Path, default=Path("/dev/null"),
                        help="Where to write error logs")
    args = parser.parse_args()

    directory = args.directory.resolve()

    if not directory.is_dir():
        print(f"Not a directory: {directory}", file=sys.stderr)
        sys.exit(1)

    print(f"Walking directory: {directory}", file=sys.stderr)

    # Counters
    ran = 0
    crashed = 0
    timed_out = 0
    succeeded = 0

    for path in directory.rglob("*.sh"):
        ran += 1

        print()
        print(f"=== Running SaSh on: {path} ===", file=sys.stderr)

        try:
            report = sash.main.main(
                path.as_posix(),
                timeout=args.timeout,
                solver_timeout=args.solver_timeout,
                log_file=args.error_log,
                log_level="error",
                enable_dfs=args.enable_dfs,
            )
        except Exception as e:
            crashed += 1
            print(f"[CRASH] SaSh crashed on {path}: {e}", file=sys.stderr)
            continue

        if report.timed_out:
            timed_out += 1
            print(f"[TIMEOUT] exec={report.time}s solver={report.solver_time}s", file=sys.stderr)
        else:
            succeeded += 1
            print(f"[DONE] exec={report.time}s solver={report.solver_time}s", file=sys.stderr)

        if args.verbose:
            import json
            print(json.dumps(report.to_dict(), indent=2))

    # Summary
    print("\n=== Summary ===", file=sys.stderr)
    print(f"  Files scanned: {ran}", file=sys.stderr)
    print(f"  Succeeded:     {succeeded}", file=sys.stderr)
    print(f"  Crashed:       {crashed}", file=sys.stderr)
    print(f"  Timed out:     {timed_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
