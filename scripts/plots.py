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
figsize_small = (4.2, 1.15)
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

    # Two separate bars, one per tool total:
    # SaSh bar = both + SaSh-only + missed
    # ShellCheck bar = both + ShellCheck-only + missed
    plt.figure(figsize=(figsize_small[0], 1.9))
    both_color = color_scheme[1]
    sash_only_color = color_scheme[0]
    shell_only_color = color_scheme[2]
    missed_color = "lightgray"

    y_positions = [1, 0]
    # First row = SaSh, second row = ShellCheck
    both_sizes = [both_detected, both_detected]
    only_sizes = [only_sash, only_shell]
    missed_sizes = [missed, missed]

    plt.barh(y_positions, both_sizes, color=both_color, label="Both")
    plt.barh(y_positions, only_sizes, left=both_sizes, color=[sash_only_color, shell_only_color])
    plt.barh(
        y_positions,
        missed_sizes,
        left=[both_sizes[i] + only_sizes[i] for i in range(2)],
        color=missed_color,
        label="Missed",
    )

    plt.xlabel("Detected Bugs", loc="right")
    plt.yticks([0, 1], ["ShellCheck", sysname])
    plt.legend(
        fontsize=10,
        loc="upper left",
        frameon=True,
    )
    plt.xlim(0, all_expected)
    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


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

    results_data = load_csv(args.results_csv)
    results_data = results_data[results_data["kind"] == "buggy"].copy()
    results_data["loc"] = results_data["benchmark"].apply(get_loc)
    plot_bug_detection_euler(results_data, os.path.join(args.output_dir, "bug-detection-euler.pdf"))
    plot_bug_detection_bars(results_data, os.path.join(args.output_dir, "bug-detection-bars.pdf"))
    plot_runtime(results_data, os.path.join(args.output_dir, "runtime.pdf"))

    # Print bug stats
    total_benchmarks = len(results_data)
    sash_detected, shellcheck_detected, all_bugs_expected = _get_bug_sets(results_data)
    total_bugs = len(all_bugs_expected)
    print(f"% Total benchmarks: {total_benchmarks}", file=sys.stderr)
    print(f"% Total bugs: {total_bugs}", file=sys.stderr)
    print(f"% {sysname} detected bugs: {len(sash_detected)}", file=sys.stderr)
    print(f"% ShellCheck detected bugs: {len(shellcheck_detected)}", file=sys.stderr)
    print(f"% Both detected bugs: {len(sash_detected & shellcheck_detected)}", file=sys.stderr)
    print(f"% Missed bugs: {len(all_bugs_expected - (sash_detected | shellcheck_detected))}", file=sys.stderr)

if __name__ == "__main__":
    main()
