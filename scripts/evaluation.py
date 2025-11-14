#!/usr/bin/env -S uv run python3
import argparse
import json
import pathlib
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

import sash.main
import sash.reporter

# ANSI color codes
MAGENTA = '\033[95m'
BLUE = '\033[94m'
CYAN = '\033[96m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'
BOLD = '\033[1m'
UNDERLINE = '\033[4m'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--timeout', type=float, default=None, help='Timeout in seconds for each benchmark (default: no timeout)')
    parser.add_argument('-b', '--benchmarks', type=Path, default=None, help='Path to the benchmarks directory, relative to the git toplevel (default: <git_toplevel>/benchmarks)')
    parser.add_argument('-O', '--only', type=str, default=None, help='Regex to filter benchmarks to run (default: run all)')
    parser.add_argument('-o', '--output', type=Path, default=None, help='File to write output to (default: stdout)')
    parser.add_argument('-G', '--ground-truth-only', action='store_true', help='Only run benchmarks that have ground truth defined (default: run all)')
    parser.add_argument('-V', '--verbose', action='store_true', help='Enable printing of exceptions that occur and raw output when ground truth is missing (default: false)')
    parser.add_argument('-N', '--no-color', action='store_true', help='Disable colored output to stderr (default: false)')
    args = parser.parse_args()

    if args.no_color:
        global MAGENTA, BLUE, CYAN, GREEN, YELLOW, RED, RESET, BOLD, UNDERLINE
        MAGENTA = ""
        BLUE = ""
        CYAN = ""
        GREEN = ""
        YELLOW = ""
        RED = ""
        RESET = ""
        BOLD = ""
        UNDERLINE = ""

    timeout: float | None = args.timeout
    benchmark_filter = re.compile(args.only) if args.only else None
    output_file = args.output.resolve() if isinstance(args.output, Path) else None
    ground_truth_only: bool = args.ground_truth_only
    verbose: bool = args.verbose

    top = get_git_toplevel()
    if args.benchmarks:
        bench_dir = top / args.benchmarks
    else:
        bench_dir = top / "benchmarks"

    known_codes = get_all_reporter_codes()
    with (bench_dir / "codes_out_of_scope.yaml").open() as f:
        out_of_scope_codes: list[str] = list(set(yaml.safe_load(f)))
        print(f"Out of scope codes: {out_of_scope_codes}", file=sys.stderr)

    ran = 0
    failed = 0
    unknown = 0
    # succeeded == ran - failed - unknown
    skipped = 0
    timed_out = 0

    run_results = []

    for benchmark in find_benchmarks(bench_dir):
        if benchmark_filter and not benchmark_filter.search(benchmark.as_posix()):
            continue

        print(file=sys.stderr)
        print(f"Benchmark: {MAGENTA}{benchmark.relative_to(top)}{RESET} (found in {top})", file=sys.stderr)

        gt_path = benchmark.parent / "info.yaml"
        gt_exists = gt_path.is_file()
        if not gt_exists:
            print_warn("No ground truth found", file=sys.stderr)
            if ground_truth_only:
                skipped += 1
                continue

        expected_codes = []
        shellcheck_results = []
        if gt_exists:
            expected_codes = [e for e in load_expected_codes(gt_path) if e not in out_of_scope_codes]
            unknown_codes = [e for e in expected_codes if e not in known_codes]
            if len(unknown_codes) > 0:
                print_warn(f"Unknown in-scope codes in ground truth: {unknown_codes}", file=sys.stderr)

            shellcheck_results = load_shellcheck_results(gt_path)

        try:
            report = sash.main.main(benchmark.as_posix(), timeout=timeout, log_file=Path("/dev/null"))
        except (AssertionError, BaseException) as e: # catch EVERYTHING, including KeyboardInterrupt
            err_type = "AssertionError" if isinstance(e, AssertionError) else "Exception"
            print_fail(f"{err_type} raised during analysis{f': {e}' if verbose else ''}", file=sys.stderr)
            failed += 1

            if verbose:
                import traceback
                traceback.print_exc(file=sys.stderr)

            run_results.append(RunResult(
                benchmark=benchmark.relative_to(top).as_posix(),
                missing_gt=not gt_exists,
                crashed=True,
                timed_out=None,
                time=None,
                detected_all=None,
                expected_codes=expected_codes,
                actual_codes=None,
                shellcheck_codes=shellcheck_results
            ))
            continue
        finally:
            ran += 1

        if report.timed_out:
            print_warn(f"Analysis timed out; time elapsed: {report.time + report.solver_time}s", file=sys.stderr)
            timed_out += 1
        else:
            print_info(f"Analysis completed; time elapsed: {report.time + report.solver_time}s", file=sys.stderr)

        actual_codes = [issue.code.value for issue in report.issues]
        if gt_exists and all(code in actual_codes for code in expected_codes):
            print_pass("All expected codes detected", file=sys.stderr)
        elif gt_exists:
            print_fail(f"Missing expected codes: {[code for code in expected_codes if code not in actual_codes]}", file=sys.stderr)
            failed += 1
        else:
            if verbose:
                print_info(f"Report: {json.dumps(report.to_dict(), indent=2)}", file=sys.stderr)
            unknown += 1

        run_results.append(RunResult(
            benchmark=benchmark.relative_to(top).as_posix(),
            missing_gt=not gt_exists,
            crashed=False,
            timed_out=report.timed_out,
            time=report.time + report.solver_time,
            detected_all=gt_exists and all(code in actual_codes for code in expected_codes),
            expected_codes=expected_codes,
            actual_codes=actual_codes,
            shellcheck_codes=shellcheck_results
        ))

    print(file=sys.stderr)
    print("Summary", file=sys.stderr)
    print(f"  Total: {ran + skipped}", file=sys.stderr)
    print(f"  Skipped: {skipped}", file=sys.stderr)
    print(f"  Ran: {ran}", file=sys.stderr)
    print(f"    Succeeded: {ran - failed - unknown}", file=sys.stderr)
    print(f"    Failed: {failed}", file=sys.stderr)
    print(f"    Unknown (no ground truth): {unknown}", file=sys.stderr)
    print(f"    Timed out: {timed_out}", file=sys.stderr)

    if output_file:
        output_file = output_file.open("w")
    else:
        output_file = sys.stdout
        print(file=sys.stderr) # Trick to ensure separation from previous stderr output, but make it invisivle if stderr has been redirected
        print("Detailed Results (CSV):", file=output_file)

    print(f"{','.join(RunResult._fields)}", file=output_file)
    for r in run_results:
        print(f"{r.benchmark},"
              f"{r.missing_gt},"
              f"{r.crashed},"
              f"{r.timed_out},"
              f"{r.time},"
              f"{r.detected_all},"
              f"{';'.join(r.expected_codes) if r.expected_codes else ''},"
              f"{';'.join(r.actual_codes) if r.actual_codes else ''},"
              f"{';'.join(r.shellcheck_codes) if r.shellcheck_codes else ''}",
              file=output_file)

    raise SystemExit(failed > 0)


def print_pass(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{GREEN}PASS{RESET}] {msg}", file=file)


def print_fail(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{RED}FAIL{RESET}] {msg}", file=file)


def print_warn(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{YELLOW}WARN{RESET}] {msg}", file=file)


def print_info(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{CYAN}INFO{RESET}] {msg}", file=file)


class RunResult(NamedTuple):
    benchmark: str
    missing_gt: bool | None
    crashed: bool | None
    timed_out: bool | None
    time: float | None
    detected_all: bool | None
    expected_codes: list[str] | None
    actual_codes: list[str] | None
    shellcheck_codes: list[str] | None


# Note: if `timeout` supplied, may raise subprocess.TimeoutExpired
def run_cmd(cmd, check=False, capture_stdout=True, timeout=None):
    import os
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
        return result
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), 9)
        proc.wait()
        raise


def get_git_toplevel():
    proc = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        print("Failed to determine git top-level directory", file=sys.stderr)
        sys.exit(1)
    return pathlib.Path(proc.stdout.strip()).resolve(strict=True)


def find_benchmarks(bench_dir: Path):
    for path in bench_dir.rglob("posix.sh"):
        if "_not_integrated" not in path.parts:
            yield path.resolve(strict=True)


def load_expected_codes(gt_path) -> list[str]:
    with open(gt_path, "r") as f:
        data = yaml.safe_load(f)
    codes = []
    for entry in data.get("ground_truth", []).get("errors", []):
        code = entry.get("code")
        if isinstance(code, str):
            codes.append(code)
        elif isinstance(code, list):
            codes.extend(code)
    return codes


def load_shellcheck_results(gt_path) -> list[str]:
    results = []
    with open(gt_path, "r") as f:
        data = yaml.safe_load(f)
        errors = data.get("ground_truth", {}).get("errors", [])
        for error in errors:
            if error["shellcheck"]["detects"]:
                if isinstance(error["code"], str):
                    results.append(error["code"])
                elif isinstance(error["code"], list):
                    results.extend(error["code"])
    return results


def extract_codes_from_output(output) -> tuple[set[str], dict] | None:
    try:
        data = json.loads(output)
        codes = [e.get("code") for e in data.get("errors", [])]
        return set(codes), data
    except Exception:
        return None


def get_all_reporter_codes() -> set[str]:
    return sash.reporter.Issue.all_codes()


if __name__ == "__main__":
    main()
