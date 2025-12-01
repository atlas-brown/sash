#!/usr/bin/env -S uv run python3
import argparse
import subprocess
import sys
from pathlib import Path

import sash.main


def run_shellcheck(path, timeout, verbose):
    """Run ShellCheck on one file and return a report-like dict."""

    try:
        proc = subprocess.run(
            ["shellcheck", "-f", "json", path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"timed_out": True, "crashed": False, "time": None, "raw": None}

    timed_out = False
    crashed = proc.returncode != 0  # shellcheck returns 1 if warnings exist

    raw = proc.stdout.strip()

    if verbose:
        print(raw)

    return {
        "timed_out": timed_out,
        "crashed": crashed,
        "time": None,
        "raw": raw,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "directory", type=Path,
        help="Directory to search recursively for .sh files"
    )
    parser.add_argument("-t", "--timeout", type=float, default=None,
                        help="Timeout for symbolic execution or ShellCheck")
    parser.add_argument("-T", "--solver-timeout", type=float, default=None,
                        help="Solver timeout (SaSh only)")
    parser.add_argument("-V", "--verbose", action="store_true",
                        help="Print full reports")
    parser.add_argument("-D", "--enable-dfs", action="store_true",
                        help="Enable DFS for SaSh")
    parser.add_argument(
        "--shellcheck", action="store_true",
        help="Run ShellCheck instead of SaSh"
    )
    parser.add_argument("-e", "--error-log", type=Path, default=Path("/dev/null"),
                        help="Where to write error logs (SaSh only)")
    args = parser.parse_args()

    directory: Path = args.directory.resolve()

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
        if not "scripts" in path.parts:
            continue
        ran += 1
        print(file=sys.stderr)
        print(f"=== Running on: {path} ===", file=sys.stderr)

        # Get the part of the path just before "scripts", e.g. /path/to/scripts/lol should yield 'to'
        part_before_scripts = path.parent.parts[path.parent.parts.index("scripts") - 1]
        filepath = Path("./results") / part_before_scripts / (path.stem + ".jsonc")
        filepath.parent.mkdir(parents=True, exist_ok=True)
        file = filepath.open("w")

        if args.shellcheck:
            # Run ShellCheck
            report = run_shellcheck(
                path.as_posix(),
                timeout=args.timeout,
                verbose=args.verbose
            )

            if report["timed_out"]:
                timed_out += 1
                print("[TIMEOUT] ShellCheck", file=file)
            elif report["crashed"]:
                crashed += 1
                print("[CRASH] ShellCheck exited nonzero", file=file)
            else:
                succeeded += 1
                print("[DONE] ShellCheck ok", file=file)

        else:
            # Run SaSh
            try:
                report = sash.main.main(
                    path.as_posix(),
                    timeout=args.timeout,
                    solver_timeout=args.solver_timeout,
                    log_file=args.error_log,
                    log_level="error",
                    enable_dfs=args.enable_dfs,
                )
            except SystemExit as e:
                crashed += 1
                print(f"// [CRASH] SaSh assertion failed on {path}: {e}", file=file)
                continue
            except Exception as e:
                crashed += 1
                print(f"// [CRASH] SaSh crashed on {path}: {e}", file=file)
                continue

            if report.timed_out:
                timed_out += 1
                print(f"// [TIMEOUT] exec={report.time}s solver={report.solver_time}s", file=file)
            else:
                succeeded += 1
                print(f"// [DONE] exec={report.time}s solver={report.solver_time}s", file=file)

            if args.verbose:
                import json
                print(json.dumps(report.to_dict(), indent=2), file=file)

        file.close()

    # Summary
    print("\n=== Summary ===", file=sys.stderr)
    print(f"  Files scanned: {ran}", file=sys.stderr)
    print(f"  Succeeded:     {succeeded}", file=sys.stderr)
    print(f"  Crashed:       {crashed}", file=sys.stderr)
    print(f"  Timed out:     {timed_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
