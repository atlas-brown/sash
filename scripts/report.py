#!/usr/bin/env -S uv run python3
# -*- coding: utf-8 -*-
"""HTML report generation for evaluation results."""
import argparse
import csv
import sys
from pathlib import Path
from typing import NamedTuple


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


def generate_html_report(html_file: Path, run_results: list[RunResult], ran: int, skipped: int, failed: int, unknown: int, timed_out: int,
                         total_issues: int, detected_issues_expected: int, detected_issues_extra: int,
                         detected_issues_extra_unsat_preconds: int, detected_issues_extra_unset_vars: int,
                         total_exec_time: float, total_solver_time: float,
                         SE_timeout: float | None = None, solver_timeout: float | None = None):
    """Generate a simple HTML report with expandable sections for each benchmark."""

    in_fixed_mode = all(r.benchmark.endswith("fixed.sh") for r in run_results)

    html_parts = []

    # HTML head and styles
    html_parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Evaluation Results</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            padding: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            border-bottom: 3px solid #007bff;
            padding-bottom: 8px;
            margin: 0 0 10px 0;
            font-size: 24px;
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px;
            margin: 10px 0;
        }
        .summary-item {
            background: #f8f9fa;
            padding: 10px 12px;
            border-left: 4px solid #007bff;
            border-radius: 4px;
        }
        .summary-item.success {
            border-left-color: #28a745;
        }
        .summary-item.danger {
            border-left-color: #dc3545;
        }
        .summary-item.warning {
            border-left-color: #ffc107;
        }
        .summary-item.info {
            border-left-color: #17a2b8;
        }
        .summary-item-value {
            font-size: 20px;
            font-weight: bold;
            color: #333;
        }
        .summary-item-label {
            font-size: 11px;
            color: #666;
            margin-top: 3px;
        }
        .benchmark-section {
            margin: 6px 0;
            border: 1px solid #ddd;
            border-radius: 3px;
            overflow: hidden;
        }
        .benchmark-header {
            background: #f8f9fa;
            padding: 8px 12px;
            cursor: pointer;
            user-select: none;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #ddd;
            font-size: 14px;
        }
        .benchmark-header:hover {
            background: #e9ecef;
        }
        .benchmark-header.passed {
            background-color: #d4edda;
            border-bottom-color: #28a745;
        }
        .benchmark-header.failed {
            background-color: #f8d7da;
            border-bottom-color: #dc3545;
        }
        .benchmark-header.unknown {
            background-color: #d1ecf1;
            border-bottom-color: #17a2b8;
        }
        .benchmark-header.skipped {
            background-color: #e2e3e5;
            border-bottom-color: #6c757d;
        }
        .benchmark-header.crashed {
            background-color: #f5c6cb;
            border-bottom-color: #721c24;
        }
        .benchmark-title {
            flex: 1;
            font-weight: 600;
            color: #333;
        }
        .benchmark-status {
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: bold;
            margin: 0 8px;
            white-space: nowrap;
        }
        .status-pass {
            background-color: #28a745;
            color: white;
        }
        .status-fail {
            background-color: #dc3545;
            color: white;
        }
        .status-unknown {
            background-color: #17a2b8;
            color: white;
        }
        .status-skipped {
            background-color: #6c757d;
            color: white;
        }
        .status-crashed {
            background-color: #721c24;
            color: white;
        }
        .status-timeout {
            background-color: #fd7e14;
            color: white;
        }
        .toggle-icon {
            font-size: 16px;
            margin-left: 8px;
            transition: transform 0.2s ease;
        }
        .benchmark-header.expanded .toggle-icon {
            transform: rotate(180deg);
        }
        .benchmark-content {
            display: none;
            padding: 10px 12px;
            background: white;
            font-size: 13px;
        }
        .benchmark-content.open {
            display: block;
        }
        .issue-list {
            margin: 6px 0;
            padding-left: 15px;
        }
        .issue-item {
            margin: 4px 0;
            padding: 5px 6px;
            background: #f8f9fa;
            border-radius: 2px;
            border-left: 3px solid #007bff;
            font-size: 12px;
        }
        .issue-item.found {
            border-left-color: #28a745;
            background: #f0f8f5;
        }
        .issue-item.missing {
            border-left-color: #dc3545;
            background: #fdf5f5;
        }
        .issue-item.extra {
            border-left-color: #ffc107;
            background: #fffef5;
        }
        .issue-code {
            font-weight: bold;
            font-family: monospace;
        }
        .issue-line {
            font-size: 12px;
            color: #666;
        }
        .info-row {
            margin: 4px 0;
            display: flex;
            gap: 15px;
            font-size: 13px;
        }
        .info-label {
            font-weight: 600;
            min-width: 130px;
        }
        .info-value {
            font-family: monospace;
            color: #555;
            font-size: 12px;
        }
    </style>
</head>
""")

    html_parts.append(f"""
<body>
    <div class="container">
        <h1>📊 Evaluation Results: {'Fixed' if in_fixed_mode else 'Buggy'} scripts (timeout SE:{SE_timeout if SE_timeout else '?'}s | Z3:{solver_timeout if solver_timeout else '?'}s) --- <a href="results{'' if in_fixed_mode else '-fixed'}.html">{'Buggy' if in_fixed_mode else 'Fixed'} Report</a></h1>
""")

    # Summary section
    succeeded = ran - failed - unknown
    html_parts.append("""        <div class="summary">""")
    html_parts.append(f"""            <div class="summary-item success">
                <div class="summary-item-value">{succeeded}</div>
                <div class="summary-item-label">Passed</div>
            </div>""")
    html_parts.append(f"""            <div class="summary-item danger">
                <div class="summary-item-value">{failed}</div>
                <div class="summary-item-label">Failed</div>
            </div>""")
    html_parts.append(f"""            <div class="summary-item info">
                <div class="summary-item-value">{unknown}</div>
                <div class="summary-item-label">No Ground Truth</div>
            </div>""")
    html_parts.append(f"""            <div class="summary-item warning">
                <div class="summary-item-value">{timed_out}</div>
                <div class="summary-item-label">Timed Out</div>
            </div>""")
    html_parts.append(f"""            <div class="summary-item">
                <div class="summary-item-value">{skipped}</div>
                <div class="summary-item-label">Skipped</div>
            </div>""")
    html_parts.append("""        </div>""")

    # Issues summary
    html_parts.append("""        <div style="margin: 8px 0; padding: 10px 12px; background: #f8f9fa; border-radius: 4px;">""")
    html_parts.append(f"""            <h3 style="margin: 0 0 6px 0; font-size: 14px;">Issue Summary</h3>""")
    html_parts.append(f"""            <div class="info-row">
                <span class="info-label">Expected {'UN' if in_fixed_mode else ''}reported issues:</span>
                <span class="info-value">{total_issues}</span>
            </div>""")
    html_parts.append(f"""            <div class="info-row">
                <span class="info-label">✓ Detected Expected:</span>
                <span class="info-value" style="color: #28a745; font-weight: bold;">{detected_issues_expected}</span>
            </div>""")
    html_parts.append(f"""            <div class="info-row">
                <span class="info-label">+ Extra Issues Found:</span>
                <span class="info-value" style="color: #ffc107; font-weight: bold;">{detected_issues_extra}</span>
            </div>""")
    if detected_issues_extra > 0:
        html_parts.append(f"""            <div class="info-row" style="margin-left: 12px; font-size: 12px;">
                <span>• Unsat preconditions: {detected_issues_extra_unsat_preconds}</span>
            </div>""")
        html_parts.append(f"""            <div class="info-row" style="margin-left: 12px; font-size: 12px;">
                <span>• Unset variables: {detected_issues_extra_unset_vars}</span>
            </div>""")
    html_parts.append(f"""            <div class="info-row">
                <span class="info-label">Exec Time:</span>
                <span class="info-value">{total_exec_time:.2f}s</span>
            </div>""")
    html_parts.append(f"""            <div class="info-row">
                <span class="info-label">Solver Time:</span>
                <span class="info-value">{total_solver_time:.2f}s</span>
            </div>""")
    html_parts.append("""        </div>""")

    # Benchmark details
    html_parts.append("""        <h2 style="margin: 10px 0 6px 0; font-size: 16px;">Benchmarks</h2>""")

    for result in run_results:
        # Determine status and styling - color code based only on whether all expected issues are detected
        # Build status text with additional info
        status_parts = []
        if result.crashed:
            status_parts.append("💥 CRASHED")
        if result.timed_out:
            status_parts.append("⏱ TIMEOUT")
        
        # Color coding: green if all expected issues found, red if not, gray if no ground truth
        if result.missing_gt:
            status_class = "unknown"
            status_text = "❓ NO GROUND TRUTH"
            status_badge = "status-unknown"
        elif result.detected_all:
            status_class = "passed"
            status_text = "✓ PASS" + (" " + " ".join(status_parts) if status_parts else "")
            status_badge = "status-pass"
        else:
            status_class = "failed"
            status_text = "✗ FAIL" + (" " + " ".join(status_parts) if status_parts else "")
            status_badge = "status-fail"

        benchmark_name = result.benchmark.split('/')[-2] if '/' in result.benchmark else result.benchmark

        html_parts.append(f"""        <div class="benchmark-section">
            <div class="benchmark-header {status_class}">
                <span class="benchmark-title">{benchmark_name}</span>
                <span class="benchmark-status {status_badge}">{status_text}</span>
                <span class="toggle-icon">▼</span>
            </div>
            <div class="benchmark-content">""")
        
        # Basic info
        html_parts.append(f"""                <div class="info-row">
                    <span class="info-label">Benchmark:</span>
                    <span class="info-value" style="font-family: monospace; font-size: 11px;">{result.benchmark}</span>
                </div>""")
        
        if result.time is not None:
            time_str = f"{result.time:.2f}"
            exec_str = f"{result.exec_time:.2f}"
            solver_str = f"{result.solver_time:.2f}"
            html_parts.append(f"""                <div class="info-row">
                    <span class="info-label">Total Time:</span>
                    <span class="info-value">{time_str}s</span>
                </div>""")
            html_parts.append(f"""                <div class="info-row">
                    <span class="info-label">Exec / Solver:</span>
                    <span class="info-value">{exec_str}s / {solver_str}s</span>
                </div>""")

        # Expected vs Actual
        if not result.crashed and not result.missing_gt:
            html_parts.append("""                <div style="margin-top: 6px;">""")
            
            if result.expected_results:
                html_parts.append("""                    <h4 style="margin: 4px 0 3px 0; font-size: 12px; font-weight: 600;">Ground Truth Issues:</h4>""")
                html_parts.append("""                    <div class="issue-list">""")
                expected_set = set(result.expected_results) if result.expected_results else set()
                actual_set = set(result.actual_results) if result.actual_results else set()
                
                for issue in result.expected_results:
                    found = "✓" if issue in actual_set else "✗"
                    if in_fixed_mode:
                        found_class = "missing" if issue in actual_set else "found"
                    else:
                        found_class = "found" if issue in actual_set else "missing"
                    html_parts.append(f"""                        <div class="issue-item {found_class}"><span class="issue-code">{found} {issue}</span></div>""")
                html_parts.append("""                    </div>""")
            
            if result.actual_results:
                extra_issues = [a for a in result.actual_results if a not in expected_set] if result.expected_results else result.actual_results
                if extra_issues:
                    html_parts.append(f"""                    <h4 style="margin: 4px 0 3px 0; font-size: 12px; font-weight: 600;">Extra Issues Detected:</h4>""")
                    html_parts.append("""                    <div class="issue-list">""")
                    for issue in extra_issues:
                        html_parts.append(f"""                        <div class="issue-item extra"><span class="issue-code">+ {issue}</span></div>""")
                    html_parts.append("""                    </div>""")

            html_parts.append("""                </div>""")

        html_parts.append("""            </div>
        </div>""")

    html_parts.append("""    </div>
    <script>
        // Add click handlers to all headers
        document.querySelectorAll('.benchmark-header').forEach(header => {
            header.addEventListener('click', function() {
                const content = this.nextElementSibling;
                content.classList.toggle('open');
                this.classList.toggle('expanded');
            });
        });
    </script>
</body>
</html>""")

    with open(html_file, 'w') as f:
        f.write('\n'.join(html_parts))

    print(f"HTML report generated: {html_file}", file=sys.stderr)


def parse_csv_field(value: str) -> any:
    """Parse a CSV field value, handling booleans, None, and lists."""
    if value == '' or value == 'None':
        return None
    if value == 'True':
        return True
    if value == 'False':
        return False
    if ';' in value:
        return value.split(';')
    try:
        return float(value)
    except ValueError:
        return value


def load_results_from_csv(csv_file: Path) -> tuple[list[RunResult], int, int, int, int, int, int, int, int, int, int, int, float, float]:
    """Load results from a CSV file generated by evaluation.py."""
    run_results = []

    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            result = RunResult(
                benchmark=row['benchmark'],
                missing_gt=row['missing_gt'] == 'True' if row['missing_gt'] else None,
                crashed=row['crashed'] == 'True' if row['crashed'] else None,
                timed_out=row['timed_out'] == 'True' if row['timed_out'] else None,
                time=float(row['time']) if row['time'] else None,
                exec_time=float(row['exec_time']) if row['exec_time'] else None,
                solver_time=float(row['solver_time']) if row['solver_time'] else None,
                detected_all=row['detected_all'] == 'True' if row['detected_all'] else None,
                expected_results=row['expected_results'].split(';') if row['expected_results'] else None,
                actual_results=row['actual_results'].split(';') if row['actual_results'] else None,
                shellcheck_codes=row['shellcheck_codes'].split(';') if row['shellcheck_codes'] else None,
                line_numbers=[int(x) if x else None for x in row['line_numbers'].split(';')] if row['line_numbers'] else None,
            )
            run_results.append(result)

    # Calculate summary statistics
    ran = len([r for r in run_results if not r.missing_gt])
    skipped = len([r for r in run_results if r.missing_gt])
    failed = len([r for r in run_results if not r.missing_gt and not r.detected_all and not r.crashed and not r.timed_out])
    unknown = 0  # Not tracked in CSV, would need additional column
    timed_out = len([r for r in run_results if r.timed_out])
    crashed = len([r for r in run_results if r.crashed])

    total_issues = len([issue for r in run_results if r.expected_results for issue in r.expected_results])
    detected_issues_expected = len([issue for r in run_results if r.expected_results and r.actual_results 
                                     for issue in r.expected_results if issue in r.actual_results])
    detected_issues_extra = len([issue for r in run_results if r.actual_results and r.expected_results
                                  for issue in r.actual_results if issue not in r.expected_results])
    detected_issues_extra_unsat_preconds = len([issue for r in run_results if r.actual_results and r.expected_results
                                                 for issue in r.actual_results 
                                                 if issue not in r.expected_results and issue == "unsat_precond"])
    detected_issues_extra_unset_vars = len([issue for r in run_results if r.actual_results and r.expected_results
                                             for issue in r.actual_results 
                                             if issue not in r.expected_results and issue in ["unbound", "unbound_setu"]])

    total_exec_time = sum(r.exec_time for r in run_results if r.exec_time)
    total_solver_time = sum(r.solver_time for r in run_results if r.solver_time)

    return (run_results, ran, skipped, failed, unknown, timed_out, crashed,
            total_issues, detected_issues_expected, detected_issues_extra,
            detected_issues_extra_unsat_preconds, detected_issues_extra_unset_vars,
            total_exec_time, total_solver_time)


def main():
    parser = argparse.ArgumentParser(description='Generate HTML report from CSV evaluation results')
    parser.add_argument('csv', type=Path, help='CSV file with evaluation results')
    parser.add_argument('-o', '--output', type=Path, required=True, help='Output HTML file path')
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    try:
        (run_results, ran, skipped, failed, unknown, timed_out, crashed,
         total_issues, detected_issues_expected, detected_issues_extra,
         detected_issues_extra_unsat_preconds, detected_issues_extra_unset_vars,
         total_exec_time, total_solver_time) = load_results_from_csv(args.csv)

        generate_html_report(
            args.output, run_results, ran, skipped, failed, unknown, timed_out,
            total_issues, detected_issues_expected, detected_issues_extra,
            detected_issues_extra_unsat_preconds, detected_issues_extra_unset_vars,
            total_exec_time, total_solver_time
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
