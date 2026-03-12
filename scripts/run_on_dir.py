#!/usr/bin/env -S uv run python3
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import sash.main

ENTRYPOINT_SCRIPTS = {
    "fetch.sh",
    "validate.sh",
    "clean.sh",
    "install.sh",
    "execute.sh",
}


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
        return {"timed_out": True, "crashed": False, "time": None, "raw": None, "issues": []}

    timed_out = False
    # shellcheck returns:
    # - 0 when no issues found
    # - 1 when issues were found
    # - >=2 on usage/config/runtime failures
    crashed = proc.returncode >= 2

    raw = proc.stdout.strip()
    issues = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for issue in parsed:
                    issues.append(
                        {
                            "line": issue.get("line"),
                            "code": f"SC{issue.get('code')}" if issue.get("code") is not None else "",
                            "severity": issue.get("level", ""),
                            "message": issue.get("message", ""),
                        }
                    )
        except json.JSONDecodeError:
            # Keep raw for debugging; CSV will show no parsed issues in this case.
            pass

    if verbose:
        print(raw)

    return {
        "timed_out": timed_out,
        "crashed": crashed,
        "time": None,
        "raw": raw,
        "issues": issues,
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
                        help="Print full reports to stdout")
    parser.add_argument("-D", "--enable-dfs", action="store_true",
                        help="Enable DFS for SaSh")
    parser.add_argument(
        "--fork-everywhere", action="store_true",
        help="Force symbolic execution to fork outside checked positions and disable trace collapsing."
    )
    parser.add_argument(
        "--disable-solver-optimizations", action="store_true",
        help="Disable solver optimizations."
    )
    parser.add_argument(
        "--shellcheck", action="store_true",
        help="Run ShellCheck instead of SaSh"
    )
    parser.add_argument(
        "-c", "--csv", type=Path, default=Path("results/run_on_dir_results.csv"),
        help="Where to write the aggregated CSV results (default: results/run_on_dir_results.csv)"
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
    rows = []

    for path in sorted(directory.rglob("*.sh")):
        # Default Koala mode: run core scripts under */scripts/* plus lifecycle entrypoints.
        if not ("scripts" in path.parts or path.name in ENTRYPOINT_SCRIPTS):
            continue
        ran += 1
        print(file=sys.stderr)
        print(f"=== Running on: {path} ===", file=sys.stderr)

        if args.shellcheck:
            # Run ShellCheck
            report = run_shellcheck(
                path.as_posix(),
                timeout=args.timeout,
                verbose=args.verbose
            )
            issues = report.get("issues", [])

            if report["timed_out"]:
                timed_out += 1
                print("[TIMEOUT] ShellCheck", file=sys.stderr)
            elif report["crashed"]:
                crashed += 1
                print("[CRASH] ShellCheck exited nonzero", file=sys.stderr)
            else:
                succeeded += 1
                print("[DONE] ShellCheck ok", file=sys.stderr)

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
                    fork_everywhere=args.fork_everywhere,
                    disable_solver_optimizations=args.disable_solver_optimizations,
                )
            except SystemExit as e:
                crashed += 1
                print(f"// [CRASH] SaSh assertion failed on {path}: {e}", file=sys.stderr)
                rows.append(
                    {
                        "benchmark": path.as_posix(),
                        "tool": "sash",
                        "timed_out": False,
                        "crashed": True,
                        "time": None,
                        "exec_time": None,
                        "solver_time": None,
                        "actual_results": "",
                        "line_numbers": "",
                        "issue_codes": "",
                        "issue_severities": "",
                        "issue_messages_json": json.dumps([]),
                        "issue_count": 0,
                        "ast_nodes_total": None,
                        "ast_nodes_interpreted": None,
                        "ast_coverage_pct": None,
                    }
                )
                continue
            except Exception as e:
                crashed += 1
                print(f"// [CRASH] SaSh crashed on {path}: {e}", file=sys.stderr)
                rows.append(
                    {
                        "benchmark": path.as_posix(),
                        "tool": "sash",
                        "timed_out": False,
                        "crashed": True,
                        "time": None,
                        "exec_time": None,
                        "solver_time": None,
                        "actual_results": "",
                        "line_numbers": "",
                        "issue_codes": "",
                        "issue_severities": "",
                        "issue_messages_json": json.dumps([]),
                        "issue_count": 0,
                        "ast_nodes_total": None,
                        "ast_nodes_interpreted": None,
                        "ast_coverage_pct": None,
                    }
                )
                continue

            if report.timed_out:
                timed_out += 1
                print(f"// [TIMEOUT] exec={report.time}s solver={report.solver_time}s", file=sys.stderr)
            else:
                succeeded += 1
                print(f"// [DONE] exec={report.time}s solver={report.solver_time}s", file=sys.stderr)

            report_dict = report.to_dict()
            issues = report_dict.get("issues", [])
            if args.verbose:
                print(json.dumps(report_dict, indent=2))

        issue_codes = [str(i.get("code", "")) for i in issues]
        issue_lines = [("" if i.get("line") is None else str(i.get("line"))) for i in issues]
        issue_messages = [str(i.get("message", "")) for i in issues]
        issue_severities = [str(i.get("severity", "")) for i in issues]
        actual_results = []
        for issue in issues:
            line = issue.get("line")
            code = str(issue.get("code", ""))
            if line is None:
                actual_results.append(code)
            else:
                actual_results.append(f"L{line}:{code}")

        if args.shellcheck:
            exec_time = None
            solver_time = None
            total_time = None
            ast_nodes_total = None
            ast_nodes_interpreted = None
            ast_coverage_pct = None
        else:
            exec_time = report.time
            solver_time = report.solver_time
            total_time = (exec_time or 0.0) + (solver_time or 0.0)
            ast_nodes_total = report.ast_nodes_total
            ast_nodes_interpreted = report.ast_nodes_interpreted
            ast_coverage_pct = report.ast_coverage_pct

        rows.append(
            {
                "benchmark": path.as_posix(),
                "tool": "shellcheck" if args.shellcheck else "sash",
                "timed_out": bool(report["timed_out"]) if args.shellcheck else bool(report.timed_out),
                "crashed": bool(report["crashed"]) if args.shellcheck else False,
                "time": total_time,
                "exec_time": exec_time,
                "solver_time": solver_time,
                "actual_results": ";".join(actual_results),
                "line_numbers": ";".join(issue_lines),
                "issue_codes": ";".join(issue_codes),
                "issue_severities": ";".join(issue_severities),
                "issue_messages_json": json.dumps(issue_messages, ensure_ascii=False),
                "issue_count": len(issues),
                "ast_nodes_total": ast_nodes_total,
                "ast_nodes_interpreted": ast_nodes_interpreted,
                "ast_coverage_pct": ast_coverage_pct,
            }
        )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "benchmark",
        "tool",
        "timed_out",
        "crashed",
        "time",
        "exec_time",
        "solver_time",
        "actual_results",
        "line_numbers",
        "issue_codes",
        "issue_severities",
        "issue_messages_json",
        "issue_count",
        "ast_nodes_total",
        "ast_nodes_interpreted",
        "ast_coverage_pct",
    ]
    with args.csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    print("\n=== Summary ===", file=sys.stderr)
    print(f"  Files scanned: {ran}", file=sys.stderr)
    print(f"  Succeeded:     {succeeded}", file=sys.stderr)
    print(f"  Crashed:       {crashed}", file=sys.stderr)
    print(f"  Timed out:     {timed_out}", file=sys.stderr)
    print(f"  CSV:           {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
