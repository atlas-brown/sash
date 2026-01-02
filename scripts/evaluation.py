#!/usr/bin/env -S uv run python3
import argparse
import json
import multiprocessing
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


class CheckResult(NamedTuple):
    code: str
    line: int | None

    def __str__(self):
        return f"L{self.line}:{self.code}"

class RunResult(NamedTuple):
    benchmark: str
    missing_gt: bool | None
    crashed: bool | None
    timed_out: bool | None
    time: float | None
    exec_time: float | None
    solver_time: float | None
    detected_all: bool | None
    expected_results: list[str] | None
    actual_results: list[str] | None
    shellcheck_codes: list[str] | None
    line_numbers: list[int | None] | None
    unknown_codes: list[str] | None = None
    exception_traceback: str | None = None
    report_issues: list | None = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--timeout', type=float, default=None, help='Timeout in seconds for symbolic execution in each benchmark (default: no timeout)')
    parser.add_argument('-T', '--solver-timeout', type=float, default=None, help='Timeout in seconds for solving in each benchmark (default: no timeout)')
    parser.add_argument('-b', '--benchmarks', type=Path, default=None, help='Path to the benchmarks directory, relative to the git toplevel (default: <git_toplevel>/benchmarks)')
    parser.add_argument('-O', '--only', type=str, default=None, help='Regex to filter benchmarks to run (default: run all)')
    parser.add_argument('-o', '--output', type=Path, default=None, help='CSV file to write results table to (default: stdout)')
    parser.add_argument('-H', '--html', type=Path, default=None, help='File to write HTML overview to (default: no HTML output)')
    parser.add_argument('-G', '--ground-truth-only', action='store_true', help='Only run benchmarks that have ground truth defined (default: run all)')
    parser.add_argument('-V', '--verbose', action='store_true', help='Enable printing of error reports or exceptions that occur, and raw output when ground truth is missing (default: false)')
    parser.add_argument('-N', '--no-color', action='store_true', help='Disable colored output to stderr (default: false)')
    parser.add_argument('-e', '--error-log', type=Path, default=Path("/dev/null"), help='File to write error logs to (default: /dev/null)')
    parser.add_argument('-D', '--enable-dfs', action='store_true', help='Enable depth-first symbolic execution passes (default: false)')
    parser.add_argument('-f', '--fixed', action='store_true', help='Run the evaluation on the fixed versions of the benchmarks (default: false)')
    parser.add_argument('-j', '--jobs', type=int, default=None, help='Number of parallel jobs (default: all available CPU cores)')
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

    symbexec_timeout: float | None = args.timeout
    solver_timeout: float | None = args.solver_timeout
    benchmark_filter = re.compile(args.only) if args.only else None
    output_file = args.output.resolve() if isinstance(args.output, Path) else None
    html_file = args.html.resolve() if isinstance(args.html, Path) else None
    ground_truth_only: bool = args.ground_truth_only
    verbose: bool = args.verbose
    error_log: Path = args.error_log.resolve()
    enable_dfs: bool = args.enable_dfs
    fixed: bool = args.fixed

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

    total_issues = 0
    detected_issues_expected = 0
    detected_issues_extra = 0
    detected_issues_extra_unsat_preconds = 0
    detected_issues_extra_unset_vars = 0

    tota_exec_time = 0.0
    total_solver_time = 0.0

    # Collect all benchmarks to run
    benchmarks_to_run = []
    for benchmark in find_benchmarks(bench_dir):
        if benchmark_filter and not benchmark_filter.search(benchmark.as_posix()):
            continue
        if fixed:
            benchmark = benchmark.parent / "fixed.sh"
        benchmarks_to_run.append(benchmark)

    # Process benchmarks in parallel
    num_cores = args.jobs if args.jobs else multiprocessing.cpu_count()
    print(f"Running {len(benchmarks_to_run)} benchmarks in parallel using {num_cores} cores", file=sys.stderr)

    with multiprocessing.Pool(processes=num_cores) as pool:
        run_results = pool.starmap(
            process_benchmark,
            [(benchmark, top, symbexec_timeout, solver_timeout, error_log,
              enable_dfs, out_of_scope_codes, known_codes, ground_truth_only, verbose, fixed)
             for benchmark in benchmarks_to_run]
        )

    # Aggregate results
    run_results = [r for r in run_results if r is not None]  # Filter out skipped benchmarks

    for result in run_results:
        print(file=sys.stderr)
        print(f"Benchmark: {MAGENTA}{result.benchmark}{RESET} (found in {top})", file=sys.stderr)

        # Log unknown codes warning
        if result.unknown_codes:
            print_warn(f"Unknown in-scope codes in ground truth: {result.unknown_codes}", file=sys.stderr)

        # Log no ground truth warning
        if result.missing_gt:
            print_warn("No ground truth found", file=sys.stderr)

        # Process crash or exception
        if result.crashed:
            print_fail(f"Exception raised during analysis", file=sys.stderr)
            if result.exception_traceback:
                print(result.exception_traceback, file=sys.stderr)
            failed += 1
        else:
            ran += 1

            # Log timeout or completion
            if result.timed_out:
                print_warn(f"Analysis timed out; exec time: {result.exec_time}s, solver time: {result.solver_time}s", file=sys.stderr)
                timed_out += 1
            else:
                print_info(f"Analysis completed; exec time: {result.exec_time}s, solver time: {result.solver_time}s", file=sys.stderr)

            tota_exec_time += result.exec_time or 0
            total_solver_time += result.solver_time or 0

            # Count issues and log pass/fail
            if result.expected_results:
                expected_set = set(result.expected_results)
                actual_set = set(result.actual_results) if result.actual_results else set()
                total_issues += len(expected_set)
                detected_issues_expected += len([e for e in expected_set if e in actual_set])
                if result.actual_results:
                    detected_issues_extra += len([a for a in actual_set if a not in expected_set])
                    detected_issues_extra_unsat_preconds += len([a for a in actual_set if a not in expected_set and ':unsat_precond' in a])
                    detected_issues_extra_unset_vars += len([a for a in actual_set if a not in expected_set and (':unbound' in a or ':unbound_setu' in a)])

                # Log pass/fail status
                if result.missing_gt:
                    if verbose and result.report_issues:
                        print_info(f"Report: {json.dumps([{'code': i.code.value, 'line': i.source_line} for i in result.report_issues], indent=2)}", file=sys.stderr)
                    unknown += 1
                elif result.detected_all:
                    print_pass("All expected results detected", file=sys.stderr)
                    if verbose and result.report_issues:
                        print_issue_details(Path(result.benchmark), result.report_issues, file=sys.stderr)
                else:
                    missing = [r for r in expected_set if r not in actual_set]
                    print_fail(f"Missing expected results: {missing}", file=sys.stderr)
                    failed += 1
                    if verbose and result.report_issues:
                        print_issue_details(Path(result.benchmark), result.report_issues, file=sys.stderr)
            elif result.missing_gt:
                unknown += 1

    skipped = len(benchmarks_to_run) - len(run_results)

    print(file=sys.stderr)
    print("Summary", file=sys.stderr)
    print(f"  Total benchmarks: {ran + skipped}", file=sys.stderr)
    print(f"  Skipped benchmarks: {skipped}", file=sys.stderr)
    print(f"  Ran benchmarks: {ran}", file=sys.stderr)
    print(f"    Succeeded: {ran - failed - unknown}", file=sys.stderr)
    print(f"    Failed: {failed}", file=sys.stderr)
    print(f"    Unknown (no ground truth): {unknown}", file=sys.stderr)
    print(f"    Timed out: {timed_out}", file=sys.stderr)
    print()
    print(f"  Total expected issues (in-scope): {total_issues}", file=sys.stderr)
    print(f"  Detected expected issues: {detected_issues_expected}", file=sys.stderr)
    print(f"  Detected issues not in ground truth: {detected_issues_extra}", file=sys.stderr)
    print(f"    Unsatisfied preconditions: {detected_issues_extra_unsat_preconds}", file=sys.stderr)
    print(f"    Unset variables (incl. setu): {detected_issues_extra_unset_vars}", file=sys.stderr)
    print(f"  Total execution time: {tota_exec_time}s", file=sys.stderr)
    print(f"  Total solver time: {total_solver_time}s", file=sys.stderr)

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
              f"{r.exec_time},"
              f"{r.solver_time},"
              f"{r.detected_all},"
              f"{';'.join(r.expected_results) if r.expected_results else ''},"
              f"{';'.join(r.actual_results) if r.actual_results else ''},"
              f"{';'.join(r.shellcheck_codes) if r.shellcheck_codes else ''},"
              f"{';'.join('' if line is None else str(line) for line in r.line_numbers) if r.line_numbers else ''}",
              file=output_file)

    if html_file:
        from report import generate_html_report, RunResult as ReportRunResult
        generate_html_report(html_file, run_results, ran, skipped, failed, unknown, timed_out,
                             total_issues, detected_issues_expected,
                             detected_issues_extra, detected_issues_extra_unsat_preconds, detected_issues_extra_unset_vars,
                             tota_exec_time, total_solver_time,
                             SE_timeout=symbexec_timeout, solver_timeout=solver_timeout)

    raise SystemExit(failed > 0)


def process_benchmark(benchmark: Path, top: Path, symbexec_timeout: float | None,
                     solver_timeout: float | None, error_log: Path,
                     enable_dfs: bool, out_of_scope_codes: list[str],
                     known_codes: set[str], ground_truth_only: bool,
                     verbose: bool, fixed_mode: bool) -> RunResult | None:
    """Process a single benchmark and return its result."""
    import traceback

    print(f"Processing benchmark: {benchmark}", file=sys.stderr)

    gt_path = benchmark.parent / "info.yaml"
    gt_exists = gt_path.is_file()

    if not gt_exists and ground_truth_only:
        return None  # Skip this benchmark

    expected_results: list[CheckResult] = []
    shellcheck_results = []
    unknown_codes: list[str] = []
    if gt_exists:
        expected_results = [r for r in load_expected_results(gt_path) if r.code not in out_of_scope_codes]
        unknown_codes = [e.code for e in expected_results if e.code not in known_codes]
        shellcheck_results = load_shellcheck_results(gt_path)

    exception_traceback_str = None
    report_issues = None

    try:
        sash.reporter.Reporter.reset()
        report = sash.main.main(benchmark.as_posix(),
                                timeout=symbexec_timeout, solver_timeout=solver_timeout,
                                log_level="error", log_file=error_log,
                                enable_dfs=enable_dfs)
    except (AssertionError, BaseException) as e:
        if isinstance(e, KeyboardInterrupt):
            raise  # Re-raise KeyboardInterrupt to stop all processes

        exception_traceback_str = traceback.format_exc() if verbose else None

        return RunResult(
            benchmark=benchmark.relative_to(top).as_posix(),
            missing_gt=not gt_exists,
            crashed=True,
            timed_out=None,
            time=None,
            exec_time=None,
            solver_time=None,
            detected_all=None,
            expected_results=[str(e) for e in expected_results],
            actual_results=None,
            shellcheck_codes=shellcheck_results,
            line_numbers=None,
            unknown_codes=unknown_codes,
            exception_traceback=exception_traceback_str,
            report_issues=None
        )

    actual_results: list[CheckResult] = [
        CheckResult(code=issue.code.value, line=issue.source_line)
        for issue in report.issues
        if issue.code.value not in out_of_scope_codes
    ]

    if fixed_mode:
        detected_all = gt_exists and all(e not in actual_results for e in expected_results)
    else:
        detected_all = gt_exists and all(e in actual_results for e in expected_results)

    return RunResult(
        benchmark=benchmark.relative_to(top).as_posix(),
        missing_gt=not gt_exists,
        crashed=False,
        timed_out=report.timed_out,
        time=report.time + report.solver_time,
        exec_time=report.time,
        solver_time=report.solver_time,
        detected_all=detected_all,
        expected_results=[str(e) for e in expected_results],
        actual_results=[str(a) for a in actual_results],
        shellcheck_codes=shellcheck_results,
        line_numbers=[a.line for a in actual_results],
        unknown_codes=unknown_codes if unknown_codes else None,
        exception_traceback=None,
        report_issues=report.issues
    )


def print_pass(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{GREEN}PASS{RESET}] {msg}", file=file)


def print_fail(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{RED}FAIL{RESET}] {msg}", file=file)


def print_warn(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{YELLOW}WARN{RESET}] {msg}", file=file)


def print_info(msg: str, file = None, indent=2):
    print(f"{' ' * indent}[{CYAN}INFO{RESET}] {msg}", file=file)


def print_issue_details(benchmark: Path, issues: list[sash.reporter.Issue], file=None, indent=2):
    if len(issues) == 0:
        print_info(f"No issues detected for {benchmark}", file=file, indent=indent)
        return

    print_info(f"Issues detected for {benchmark}:", file=file, indent=indent)
    for issue in issues:
        location = "L" + str(issue.source_line) if issue.source_line is not None else "?"
        print(f"{' ' * (indent + 3)}{location} | {BOLD}{issue.code.value}{RESET} ({issue.severity.value}): {issue.message}", file=file)


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


def load_expected_results(gt_path) -> list[CheckResult]:
    with open(gt_path, "r") as f:
        data = yaml.safe_load(f)
    results = []
    for entry in data.get("ground_truth", []).get("errors", []):
        code = entry.get("code")
        line = entry.get("line")
        if entry.get("duplicate", False):
            continue

        if isinstance(code, str) and line is not None:
            results.append(CheckResult(code=code, line=int(line)))
        elif isinstance(code, list) and isinstance(line, list):
            results.extend(CheckResult(code=c, line=int(l)) for c, l in zip(code, line))
    return results


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
