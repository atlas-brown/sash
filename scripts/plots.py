import argparse
import pandas as pd
import numpy as np
import os
from io import StringIO
import sys
import subprocess
from pathlib import Path
from collections import Counter
import yaml

import matplotlib.pyplot as plt
from matplotlib_set_diagrams import EulerDiagram

def parse_issue_list(value):
    if pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(";") if item and item.strip()]

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

def get_bm_name(path):
    subpath = os.path.dirname(path)
    parts = subpath.split(os.sep)[2:]
    result = os.path.join(*parts)
    return result

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
    plt.figure(figsize=figsize)
    data = data.sort_values(by="time", ascending=False)
    benchmarks = data["benchmark"].apply(get_bm_name)
    times = data["time"]
    locs = data["loc"]
    bars = plt.bar(benchmarks, times, color=color_scheme[0])
    plt.margins(x=0)  # remove gap left/right

    # plt.xticks(rotation=45, ha="right")
    plt.xticks([], [])
    plt.ylabel("Time (s)")
    plt.yscale("log")
    for bar, loc in zip(bars, locs):
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2, height, f"{loc}", ha='center', va='bottom', fontsize=7)
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
