#!/usr/bin/env -S uv run python3
# -*- coding: utf-8 -*-
"""HTML report generation for evaluation results."""
import argparse
import csv
import sys
from pathlib import Path
from typing import NamedTuple, Any

class RunResult(NamedTuple):
    benchmark: str
    kind: str
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
    ast_nodes_total: int | None
    ast_nodes_interpreted: int | None
    ast_coverage_pct: float | None


def generate_html_report(html_file: Path, run_results: list[RunResult], ran: int, skipped: int, failed: int, unknown: int, timed_out: int,
                         total_issues: int, detected_issues_expected: int, detected_issues_extra: int,
                         detected_issues_extra_unsat_preconds: int, detected_issues_extra_unset_vars: int,
                         total_exec_time: float, total_solver_time: float,
                         SE_timeout: float | None = None, solver_timeout: float | None = None):

    html_parts = []

    # ------------------------------------------------------------
    # Per-kind counts
    # ------------------------------------------------------------
    buggy_cnt = sum(1 for r in run_results if r.kind.startswith("buggy"))
    buggy_var_cnt = sum(1 for r in run_results if r.kind == "buggy_variant")
    fixed_cnt = sum(1 for r in run_results if r.kind.startswith("fixed"))
    fixed_var_cnt = sum(1 for r in run_results if r.kind == "fixed_variant")
    variant_cnt = sum(1 for r in run_results if r.kind.endswith("_variant"))
    unknown_cnt = sum(1 for r in run_results if r.kind == "unknown")

    succeeded = ran - failed

    # ------------------------------------------------------------
    # HTML + CSS
    # ------------------------------------------------------------
    html_parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Evaluation Results</title>

<style>
body {
    font-family: system-ui, sans-serif;
    background:#f5f5f5;
    padding:20px;
}

.container {
    max-width:1200px;
    margin:auto;
    background:white;
    border-radius:8px;
    padding:14px;
}

.summary {
    display:grid;
    grid-template-columns: repeat(auto-fit,minmax(170px,1fr));
    gap:10px;
    margin-bottom:14px;
}

.summary-item {
    background:#f8f9fa;
    padding:10px;
    border-left:4px solid #007bff;
    border-radius:4px;
}

.summary-item.success { border-left-color:#28a745; }
.summary-item.danger  { border-left-color:#dc3545; }
.summary-item.warning { border-left-color:#ffc107; }
.summary-item.info    { border-left-color:#17a2b8; }

/* ------------------------------------------------------------
   filter bar
------------------------------------------------------------ */

.filter-bar {
    margin:10px 0 18px 0;
    padding:10px;
    background:#f8f9fa;
    border-radius:6px;
    display:flex;
    gap:18px;
    flex-wrap:wrap;
    font-size:13px;
}

.filter-bar label {
    cursor:pointer;
}

/* ------------------------------------------------------------
   benchmark cards
------------------------------------------------------------ */

.benchmark-section {
    margin:6px 0;
    border:1px solid #ddd;
    border-radius:4px;
    overflow:hidden;
}

.benchmark-header {
    padding:8px 12px;
    display:flex;
    justify-content:space-between;
    align-items:center;
    cursor:pointer;
    font-size:14px;
    background:#f8f9fa;
}

.benchmark-header.passed {
    background:#d4edda;
}

.benchmark-header.failed {
    background:#f8d7da;
}

.benchmark-content {
    display:none;
    padding:10px 12px;
    font-size:13px;
}

.benchmark-content.open {
    display:block;
}

.info-row {
    display:flex;
    gap:12px;
    margin:3px 0;
    font-size:12px;
}

.info-label {
    font-weight:600;
    min-width:100px;
}

.info-value {
    font-family:monospace;
}

/* ------------------------------------------------------------
   issue coloring (unchanged)
------------------------------------------------------------ */

.issue-list { margin-top:8px; }

.issue-item {
    margin:4px 0;
    padding:4px 6px;
    border-left:3px solid #007bff;
    background:#f8f9fa;
    border-radius:3px;
    font-family:monospace;
}

.issue-item.found {
    border-left-color:#28a745;
    background:#f0f8f5;
}

.issue-item.missing {
    border-left-color:#dc3545;
    background:#fdf5f5;
}

.issue-item.extra {
    border-left-color:#ffc107;
    background:#fffef5;
}

/* ------------------------------------------------------------
   BUG/FIX/VAR tags (neutral palette)
------------------------------------------------------------ */

.badge {
    font-size:10px;
    font-weight:700;
    padding:2px 6px;
    border-radius:3px;
    margin-right:5px;
    color:white;
}

.badge.bug { background:#6c757d; }   /* gray */
.badge.fix { background:#0d6efd; }   /* blue */
.badge.var { background:#6f42c1; }   /* purple */

.benchmark-title {
    display:flex;
    align-items:center;
}
</style>
</head>
<body>
<div class="container">
<h1>📊 Evaluation Results</h1>
""")

    # ------------------------------------------------------------
    # Pass/Fail summary
    # ------------------------------------------------------------
    html_parts.append('<div class="summary">')
    html_parts.append(f'<div class="summary-item success"><b>{succeeded}</b><br>Analyses Finished</div>')
    html_parts.append(f'<div class="summary-item danger"><b>{failed}</b><br>Analyses Crashed</div>')
    html_parts.append(f'<div class="summary-item warning"><b>{timed_out}</b><br>Analyses Timed Out</div>')
    html_parts.append(f'<div class="summary-item"><b>{skipped}</b><br>Benchmarks Skipped</div>')
    html_parts.append('</div>')

    # ------------------------------------------------------------
    # Per-kind summary
    # ------------------------------------------------------------
    html_parts.append('<h3>Benchmark Types</h3>')
    html_parts.append('<div class="summary">')
    html_parts.append(f'<div class="summary-item"><b>{buggy_cnt - buggy_var_cnt} (+ {buggy_var_cnt})</b><br>Buggy</div>')
    html_parts.append(f'<div class="summary-item"><b>{fixed_cnt - fixed_var_cnt} (+ {fixed_var_cnt})</b><br>Fixed</div>')
    html_parts.append(f'<div class="summary-item"><b>{variant_cnt}</b><br>Variants</div>')
    html_parts.append(f'<div class="summary-item"><b>{unknown_cnt}</b><br>Unknown</div>')
    html_parts.append('</div>')

    # ------------------------------------------------------------
    # Filters (NEW)
    # ------------------------------------------------------------
    html_parts.append("""
<div class="filter-bar">
<b>Filters:</b>
<label><input type="checkbox" id="f-bug" checked> BUG</label>
<label><input type="checkbox" id="f-fix" checked> FIX</label>
<label><input type="checkbox" id="f-var" checked> VAR</label>
<label><input type="checkbox" id="f-pass" checked> PASS</label>
<label><input type="checkbox" id="f-fail" checked> FAIL</label>
</div>
""")

    # ------------------------------------------------------------
    # Benchmarks
    # ------------------------------------------------------------
    for result in run_results:

        status_class = "passed" if result.detected_all else "failed"

        parts = result.benchmark.split("/")
        keep = 3 if result.kind.endswith("_variant") else 2
        short_name = "/".join(parts[-keep:])

        is_bug = result.kind.startswith("buggy")
        is_fix = result.kind.startswith("fixed")
        is_var = result.kind.endswith("_variant")

        badges = []
        if is_bug:
            badges.append('<span class="badge bug">BUG</span>')
        if is_fix:
            badges.append('<span class="badge fix">FIX</span>')
        if is_var:
            badges.append('<span class="badge var">VAR</span>')

        data_attrs = f'data-bug="{int(is_bug)}" data-fix="{int(is_fix)}" data-var="{int(is_var)}" data-pass="{int(result.detected_all)}"'

        html_parts.append(f"""
<div class="benchmark-section" {data_attrs}>
  <div class="benchmark-header {status_class}">
    <span class="benchmark-title">{''.join(badges)}{short_name}</span>
    <span>{"✓ PASS" if result.detected_all else "✗ FAIL"}</span>
  </div>
  <div class="benchmark-content">
""")

        # ---------- NEW info block ----------
        html_parts.append(f"""
<div class="info-row"><span class="info-label">Path:</span><span class="info-value">{result.benchmark}</span></div>
<div class="info-row"><span class="info-label">Exec. time:</span><span class="info-value">{(result.exec_time or 0):.2f}s</span></div>
<div class="info-row"><span class="info-label">Solver time:</span><span class="info-value">{(result.solver_time or 0):.2f}s</span></div>
""")

        # ---------- issues ----------
        expected = set(result.expected_results or [])
        actual = set(result.actual_results or [])
        is_fixed = result.kind.startswith("fixed")

        html_parts.append('<div class="issue-list">')

        for issue in expected:
            success = (issue not in actual) if is_fixed else (issue in actual)
            cls = "found" if success else "missing"
            mark = "✓" if success else "✗"
            html_parts.append(f'<div class="issue-item {cls}">{mark} {issue}</div>')

        for issue in (actual - expected):
            html_parts.append(f'<div class="issue-item extra">+ {issue}</div>')

        html_parts.append('</div></div></div>')

    # ------------------------------------------------------------
    # JS (dropdowns + filters)
    # ------------------------------------------------------------
    html_parts.append("""
<script>
document.querySelectorAll('.benchmark-header').forEach(h=>{
  h.onclick = ()=>h.nextElementSibling.classList.toggle('open');
});

const filters = ['bug','fix','var','pass','fail'];

function applyFilters() {
  const show = {
    bug:  document.getElementById('f-bug').checked,
    fix:  document.getElementById('f-fix').checked,
    var:  document.getElementById('f-var').checked,
    pass: document.getElementById('f-pass').checked,
    fail: document.getElementById('f-fail').checked,
  };

  document.querySelectorAll('.benchmark-section').forEach(el=>{
    const isBug  = el.dataset.bug === "1";
    const isFix  = el.dataset.fix === "1";
    const isVar  = el.dataset.var === "1";
    const isPass = el.dataset.pass === "1";
    const isFail = !isPass;

    const visible =
      (show.bug  || !isBug)  &&
      (show.fix  || !isFix)  &&
      (show.var  || !isVar)  &&
      (show.pass || !isPass) &&
      (show.fail || !isFail);

    el.style.display = visible ? "" : "none";
  });
}

filters.forEach(f => document.getElementById('f-'+f).onchange = applyFilters);
</script>
</div></body></html>
""")

    html_file.write_text("\n".join(html_parts))
    print(f"HTML report generated: {html_file}", file=sys.stderr)


def parse_csv_field(value: str) -> Any:
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
                kind=row.get("kind", "unknown"),
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
                ast_nodes_total=int(row['ast_nodes_total']) if row.get('ast_nodes_total') else None,
                ast_nodes_interpreted=int(row['ast_nodes_interpreted']) if row.get('ast_nodes_interpreted') else None,
                ast_coverage_pct=float(row['ast_coverage_pct']) if row.get('ast_coverage_pct') else None,
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
