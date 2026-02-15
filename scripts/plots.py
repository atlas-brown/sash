import argparse
import pandas as pd
import numpy as np
import os
import re
from io import StringIO
import sys
import subprocess
from pathlib import Path
from collections import Counter
import yaml
from benchmark_metadata import benchmark_key, benchmark_display_name
from bug_depth_stats import compute_script_metrics

import matplotlib.pyplot as plt
from matplotlib_set_diagrams import EulerDiagram

def parse_issue_list(value):
    if pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(";") if item and item.strip()]


BUG_LINE_RE = re.compile(r"^L([0-9]+):")
_depth_metrics_cache = {}


def parse_issue_lines(issues):
    lines = []
    for issue in issues:
        match = BUG_LINE_RE.match(issue)
        if match:
            lines.append(int(match.group(1)))
    return lines


def get_depth_metrics(path):
    script_path = Path(path)
    if not script_path.is_absolute():
        script_path = ROOT_DIR / script_path
    script_key = str(script_path)

    if script_key not in _depth_metrics_cache:
        try:
            lines = script_path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
            _depth_metrics_cache[script_key] = compute_script_metrics(lines, script_key)
        except Exception:
            _depth_metrics_cache[script_key] = {
                "total_lines": 0,
                "depth_at_line": [0],
                "bfs_nodes_before_line": [0],
                "statements_before_line": [0],
                "final_depth": 0,
                "final_bfs_nodes_seen": 0,
                "final_statements_seen": 0,
            }
    return _depth_metrics_cache[script_key]


def deepest_bug_depth(path, expected_issues):
    bug_lines = parse_issue_lines(expected_issues)
    if not bug_lines:
        return 0

    metrics = get_depth_metrics(path)
    total_lines = metrics["total_lines"]
    bfs_nodes_before_line = metrics.get("bfs_nodes_before_line", [0])
    fallback_bfs = metrics.get("final_bfs_nodes_seen", 0)
    max_bfs_nodes = max(
        bfs_nodes_before_line[line] if 1 <= line <= total_lines else fallback_bfs
        for line in bug_lines
    )
    return max_bfs_nodes

def git_toplevel():
    return Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], encoding="utf-8"
        ).strip()
    )

ROOT_DIR = git_toplevel()

_benchmark_dir_cache = {}
_shellcheck_map_cache = {}

def find_benchmark_dir(benchmark_path):
    if benchmark_path in _benchmark_dir_cache:
        return _benchmark_dir_cache[benchmark_path]

    p = Path(benchmark_path)
    candidates = [p]
    if not p.is_absolute():
        candidates.append(ROOT_DIR / p)

    for candidate in candidates:
        for parent in [candidate.parent, *candidate.parents]:
            if (parent / "info.yaml").exists():
                _benchmark_dir_cache[benchmark_path] = parent
                return parent

    _benchmark_dir_cache[benchmark_path] = None
    return None

def get_shellcheck_map_for_benchmark(benchmark_path):
    benchmark_dir = find_benchmark_dir(benchmark_path)
    if benchmark_dir is None:
        return {}
    if benchmark_dir in _shellcheck_map_cache:
        return _shellcheck_map_cache[benchmark_dir]

    try:
        info = yaml.safe_load((benchmark_dir / "info.yaml").read_text(encoding="utf-8"))
    except Exception:
        _shellcheck_map_cache[benchmark_dir] = {}
        return {}

    code_to_shellcheck = {}
    for bug in (info or {}).get("bugs", {}).values():
        code = bug.get("code")
        shellcheck = bug.get("shellcheck")
        if code and shellcheck:
            code_to_shellcheck[code] = shellcheck
    _shellcheck_map_cache[benchmark_dir] = code_to_shellcheck
    return code_to_shellcheck

def load_csv(file_path):
    try:
        data = pd.read_csv(file_path)
        return data
    except Exception as e:
        print(f"Error loading CSV file: {e}")
        exit(1)

def get_loc(path):
    proc = os.popen(f"cloc --json {path}")
    output = proc.read()
    proc.close()
    data = None
    data = pd.read_json(StringIO(output))
    loc = int(data.get("SUM", {}).get("code", 0))
    # assert loc > 0, f"Failed to get LoC for path: {path}"
    return loc

def get_runtime_label(path):
    key = benchmark_key(path)
    return benchmark_display_name(path, default=key)

sysname = "SaSh"
figsize = (9, 3)
figsize_small = (6.0, 1.15)
color_scheme = ["#AA4465", "#FFA69E", "#998650", "#93E1D8"]

def _get_bug_sets(data):
    sash_detected = set()
    shellcheck_detected = set()
    all_bugs_expected = set()

    data_view = data
    if "kind" in data_view.columns:
        data_view = data_view[data_view["kind"] == "buggy"]
    elif "benchmark" in data_view.columns:
        data_view = data_view[
            ~data_view["benchmark"].astype(str).str.endswith("/fixed.sh")
        ]

    for _, row in data_view.iterrows():
        bench = row["benchmark"]
        actual_bugs = parse_issue_list(row["actual_results"])
        shell_bugs = parse_issue_list(row["shellcheck_codes"])
        expected_bugs = parse_issue_list(row["expected_results"])

        expected_counter = Counter()
        expected_keys = []
        for issue in expected_bugs:
            idx = expected_counter[issue]
            expected_counter[issue] += 1
            key = f"{bench}_{issue}_{idx}"
            expected_keys.append(key)
            all_bugs_expected.add(key)

        # SaSh matching is exact against expected issue IDs.
        actual_counter = Counter(actual_bugs)
        for issue, count in expected_counter.items():
            matched = min(count, actual_counter[issue])
            for idx in range(matched):
                sash_detected.add(f"{bench}_{issue}_{idx}")

        # ShellCheck matching uses benchmark-specific mapping: sash_code -> shellcheck_code.
        shell_counter = Counter(shell_bugs)
        code_to_shellcheck = get_shellcheck_map_for_benchmark(bench)
        expected_keys_by_shellcheck = {}
        for key in expected_keys:
            issue = key.split(f"{bench}_", 1)[1].rsplit("_", 1)[0]
            sash_code = issue.split(":", 1)[1] if ":" in issue else issue
            shell_code = code_to_shellcheck.get(sash_code)
            if shell_code is not None:
                expected_keys_by_shellcheck.setdefault(shell_code, []).append(key)

        for shell_code, keys in expected_keys_by_shellcheck.items():
            matched = min(len(keys), shell_counter[shell_code])
            for key in keys[:matched]:
                shellcheck_detected.add(key)

    return sash_detected, shellcheck_detected, all_bugs_expected


def plot_bug_detection_euler(data, output_path):
    sash_detected, shellcheck_detected, all_bugs_expected = _get_bug_sets(data)

    only_sash = len(sash_detected - shellcheck_detected)
    both = len(sash_detected & shellcheck_detected)
    only_shell = len(shellcheck_detected - sash_detected)
    # neither = len(all_bugs_expected - (sash_detected | shellcheck_detected))

    combination_counts = {
        (1, 0): only_sash,          # Only SaSh
        (1, 1): both,               # Both
        (0, 1): only_shell,         # Only ShellCheck
        # (0, 0): neither             # Neither
    }

    plt.figure(figsize=figsize_small)
    dgm = EulerDiagram(combination_counts, set_labels=[sysname, "ShellCheck"], set_colors=color_scheme)
    plt.title(None)
    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()

def plot_bug_detection_bars(data, output_path):
    sash_detected, shellcheck_detected, all_bugs_expected = _get_bug_sets(data)
    missed = len(all_bugs_expected - (sash_detected | shellcheck_detected))
    both_detected = len(sash_detected & shellcheck_detected)
    only_sash = len(sash_detected - shellcheck_detected)
    only_shell = len(shellcheck_detected - sash_detected)
    all_expected = len(all_bugs_expected)

    fixed_total, sash_success, shell_success = _get_fixed_fp_counts(data)
    sash_fp = fixed_total - sash_success
    shell_fp = fixed_total - shell_success

    # Single axis: two tool groups; each group has thin, touching buggy/fixed bars.
    fig, ax = plt.subplots(1, 1, figsize=(figsize_small[0], 2.2))

    sash_buggy_detected = both_detected + only_sash
    shell_buggy_detected = both_detected + only_shell
    buggy_detected = [sash_buggy_detected, shell_buggy_detected]
    buggy_missed = [all_expected - buggy_detected[0], all_expected - buggy_detected[1]]
    fixed_no_fp = [sash_success, shell_success]
    fixed_fp = [sash_fp, shell_fp]

    # Keep tool groups close to each other.
    y_positions = [0.42, 0.0]  # SaSh, ShellCheck
    bar_height = 0.18
    pair_offset = bar_height / 2
    buggy_rows = [y + pair_offset for y in y_positions]
    fixed_rows = [y - pair_offset for y in y_positions]

    detected_color = color_scheme[1]
    missed_color = "lightgray"
    no_fp_color = color_scheme[3]
    fp_color = color_scheme[0]

    # Buggy bars
    ax.barh(buggy_rows, buggy_detected, height=bar_height, color=detected_color, label="Detected")
    ax.barh(
        buggy_rows,
        buggy_missed,
        height=bar_height,
        left=buggy_detected,
        color=missed_color,
        label="Missed",
    )

    # Fixed bars
    ax.barh(fixed_rows, fixed_no_fp, height=bar_height, color=no_fp_color, label="No false positive")
    ax.barh(
        fixed_rows,
        fixed_fp,
        height=bar_height,
        left=fixed_no_fp,
        color=fp_color,
        label="False positive",
    )

    max_total = max(all_expected, fixed_total, 1)
    ax.set_xlim(0, max_total)
    ax.set_yticks(y_positions, [sysname, "ShellCheck"])
    ax.set_ylim(-0.24, 0.66)
    ax.set_xlabel("Count", loc="right")
    x_points = sorted(set(
        [0, all_expected, fixed_total, buggy_detected[0], buggy_detected[1], fixed_no_fp[0], fixed_no_fp[1]]
    ))
    ax.set_xticks(x_points)
    ax.set_xticklabels([str(int(x)) for x in x_points])
    ax.legend(fontsize=8, loc="upper left", ncol=1, frameon=True)

    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


def _get_fixed_fp_counts(data):
    data_view = data
    if "kind" in data_view.columns:
        fixed_view = data_view[data_view["kind"] == "fixed"]
        buggy_view = data_view[data_view["kind"] == "buggy"]
    elif "benchmark" in data_view.columns:
        fixed_view = data_view[
            data_view["benchmark"].astype(str).str.endswith("/fixed.sh")
        ]
        buggy_view = data_view[
            ~data_view["benchmark"].astype(str).str.endswith("/fixed.sh")
        ]
    else:
        fixed_view = data_view
        buggy_view = data_view.iloc[0:0]

    # Per benchmark: expected issue instances for the original buggy script.
    # We keep multiplicity (Counter) to count per-bug-instance, not per-script.
    buggy_expected_ids = {}
    buggy_expected_shellcheck = {}
    for _, row in buggy_view.iterrows():
        bench = row["benchmark"]
        bench_path = Path(bench)
        if not bench_path.is_absolute():
            bench_path = ROOT_DIR / bench_path
        bench_dir = str(bench_path.parent)
        expected_ids = parse_issue_list(row["expected_results"])
        expected_ids_counter = Counter(expected_ids)
        buggy_expected_ids[bench_dir] = expected_ids_counter

        code_to_shellcheck = get_shellcheck_map_for_benchmark(bench)
        expected_shell_counter = Counter()
        for issue in expected_ids:
            sash_code = issue.split(":", 1)[1] if ":" in issue else issue
            shell_code = code_to_shellcheck.get(sash_code, f"UNMAPPED::{issue}")
            expected_shell_counter[shell_code] += 1
        buggy_expected_shellcheck[bench_dir] = expected_shell_counter

    total = 0
    sash_fp = 0
    shell_fp = 0

    for _, row in fixed_view.iterrows():
        bench = row["benchmark"]
        bench_path = Path(bench)
        if not bench_path.is_absolute():
            bench_path = ROOT_DIR / bench_path
        bench_dir = str(bench_path.parent)
        sash_reports = Counter(parse_issue_list(row["actual_results"]))
        shell_reports = Counter(parse_issue_list(row["shellcheck_codes"]))

        expected_ids = buggy_expected_ids.get(bench_dir, Counter())
        expected_shell = buggy_expected_shellcheck.get(bench_dir, Counter())
        total += sum(expected_ids.values())

        # False positive for this metric means reporting the corresponding original bug.
        for issue, expected_count in expected_ids.items():
            sash_fp += min(expected_count, sash_reports[issue])
        for issue, expected_count in expected_shell.items():
            shell_fp += min(expected_count, shell_reports[issue])

    sash_success = total - sash_fp
    shell_success = total - shell_fp
    return total, sash_success, shell_success


def plot_runtime(data, output_path):
    plt.figure(figsize=(figsize[0], 3.8))
    data = data.copy()
    data["depth_bfs"] = data.apply(
        lambda row: deepest_bug_depth(
            row["benchmark"], parse_issue_list(row["expected_results"])
        ),
        axis=1,
    )
    data = data.sort_values(by=["depth_bfs", "time"], ascending=[True, True])
    benchmarks = data["benchmark"].apply(get_runtime_label)
    symexec_times = data["exec_time"].to_numpy()
    solver_times = data["solver_time"].to_numpy()
    depth_labels = data["depth_bfs"]
    x = np.arange(len(data))
    width = 0.36

    bars_sym = plt.bar(
        x - (width / 2),
        symexec_times,
        color=color_scheme[0],
        width=width,
        label="Symbolic Execution",
    )
    bars_solver = plt.bar(
        x + (width / 2),
        solver_times,
        color=color_scheme[2],
        width=width,
        label="Solver",
    )
    timeout_rows = data[data["timed_out"] == True] if "timed_out" in data.columns else data.iloc[0:0]
    if timeout_rows.empty:
        symexec_timeout = None
        solver_timeout = None
    else:
        solver_timeout = float(round(timeout_rows["solver_time"].median()))
        long_exec = timeout_rows[timeout_rows["exec_time"] > (solver_timeout * 1.5)]["exec_time"]
        if long_exec.empty:
            symexec_timeout = float(round(timeout_rows["exec_time"].median()))
        else:
            symexec_timeout = float(round(long_exec.median() / 10.0) * 10.0)

    tol = 0.25  # seconds tolerance for numeric jitter
    for sym_t, sol_t, bar_sym, bar_solver in zip(symexec_times, solver_times, bars_sym, bars_solver):
        sym_timed_out = symexec_timeout is not None and sym_t >= (symexec_timeout - tol)
        solver_timed_out = solver_timeout is not None and sol_t >= (solver_timeout - tol)
        if sym_timed_out:
            bar_sym.set_hatch("/")
        if solver_timed_out:
            bar_solver.set_hatch("/")
    plt.margins(x=0.02)  # keep a slight gap at plot borders
    plt.margins(y=0.08)  # keep a slight gap at top for bar labels

    plt.xticks(x, benchmarks, rotation=45, ha="right", rotation_mode="anchor", fontsize=7)
    plt.ylabel("Time (s)")
    plt.yscale("log")
    for xi, depth, sym_t, sol_t in zip(x, depth_labels, symexec_times, solver_times):
        top_h = max(sym_t, sol_t)
        y = top_h * 1.06 if top_h > 0 else 1e-3
        plt.text(xi, y, f"{int(depth)}", ha='center', va='bottom', fontsize=7)

    plt.legend(fontsize=8, loc="lower right", frameon=True)
    plt.subplots_adjust(bottom=0.30)
    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "results_csv",
        type=str,
        help="Path to the input CSV file (e.g., results.csv)."
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=".",
        help="Path to the output directory (default: current directory)."
    )
    args = parser.parse_args()
    # Ensure the output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    plt.rcParams.update({
        #"text.usetex": True, # doesnt work in container
        "font.family": "serif",
        #"font.serif": ["Times New Roman"], # doesnt work in container
        "font.size": 12,
    })

    all_results = load_csv(args.results_csv)
    buggy_results = all_results[all_results["kind"] == "buggy"].copy()
    buggy_results["loc"] = buggy_results["benchmark"].apply(get_loc)
    plot_bug_detection_euler(buggy_results, os.path.join(args.output_dir, "bug-detection-euler.pdf"))
    plot_bug_detection_bars(all_results, os.path.join(args.output_dir, "bug-detection-bars.pdf"))
    plot_runtime(buggy_results, os.path.join(args.output_dir, "runtime.pdf"))

    # Print bug stats
    total_benchmarks = len(buggy_results)
    sash_detected, shellcheck_detected, all_bugs_expected = _get_bug_sets(buggy_results)
    total_bugs = len(all_bugs_expected)
    print(f"% Total benchmarks: {total_benchmarks}", file=sys.stderr)
    print(f"% Total bugs: {total_bugs}", file=sys.stderr)
    print(f"% {sysname} detected bugs: {len(sash_detected)}", file=sys.stderr)
    print(f"% ShellCheck detected bugs: {len(shellcheck_detected)}", file=sys.stderr)
    print(f"% Both detected bugs: {len(sash_detected & shellcheck_detected)}", file=sys.stderr)
    print(f"% Missed bugs: {len(all_bugs_expected - (sash_detected | shellcheck_detected))}", file=sys.stderr)
    fixed_total, sash_success, shell_success = _get_fixed_fp_counts(all_results)
    print(f"% Fixed bug instances: {fixed_total}", file=sys.stderr)
    print(f"% {sysname} fixed no-FP (bug-level): {sash_success}", file=sys.stderr)
    print(f"% ShellCheck fixed no-FP (bug-level): {shell_success}", file=sys.stderr)

if __name__ == "__main__":
    main()
