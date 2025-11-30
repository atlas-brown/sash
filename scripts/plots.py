import argparse
import pandas as pd
import numpy as np
import os
from io import StringIO
import sys

import matplotlib.pyplot as plt
from matplotlib_set_diagrams import EulerDiagram

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

    for _, row in data.iterrows():
        bench = row["benchmark"]

        sash_bugs = (str(row["actual_results"]).split(";")) if pd.notna(row["actual_results"]) else []
        shell_bugs = (str(row["shellcheck_codes"]).split(";")) if pd.notna(row["shellcheck_codes"]) else []
        all_bugs = (str(row["expected_results"]).split(";")) if pd.notna(row["expected_results"]) else []

        # strip out empty entries
        sash_bugs = [b.strip() for b in sash_bugs if b.strip()]
        shell_bugs = [b.strip() for b in shell_bugs if b.strip()]
        all_bugs = [b.strip() for b in all_bugs if b.strip()]

        # per-row occurrence counters, per bug code
        sash_counts = {}
        shell_counts = {}
        expected_counts = {}

        for bug in sash_bugs:
            idx = sash_counts.get(bug, 0)
            sash_counts[bug] = idx + 1
            sash_detected.add(f"{bench}_{bug}_{idx}")

        for bug in shell_bugs:
            idx = shell_counts.get(bug, 0)
            shell_counts[bug] = idx + 1
            shellcheck_detected.add(f"{bench}_{bug}_{idx}")

        for bug in all_bugs:
            idx = expected_counts.get(bug, 0)
            expected_counts[bug] = idx + 1
            all_bugs_expected.add(f"{bench}_{bug}_{idx}")

    # Add sash detected but without the false positives
    sash_detected = sash_detected & all_bugs_expected
    shellcheck_detected = shellcheck_detected & all_bugs_expected

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
    sash_detected = len(sash_detected)
    shellcheck_detected = len(shellcheck_detected)
    all_expected = len(all_bugs_expected)

    # create a single horizontal bar with 3 segments: sash only, both, shellcheck only
    plt.figure(figsize=figsize_small)
    labels = [sysname, "Both", "ShellCheck"]
    sizes = [sash_detected - both_detected, both_detected, shellcheck_detected - both_detected, missed]
    colors = color_scheme[:3]
    plt.barh(0, sizes[0], color=colors[0], label=labels[0])
    plt.barh(0, sizes[1], left=sizes[0], color=colors[1], label=labels[1])
    plt.barh(0, sizes[2], left=sizes[0] + sizes[1], color=colors[2], label=labels[2])
    plt.barh(0, sizes[3], left=sizes[0] + sizes[1] + sizes[2], color="lightgray", label="Missed")
    plt.xlabel("Detected Bugs", loc="right")
    plt.yticks([])
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
    results_data["loc"] = results_data["benchmark"].apply(get_loc)
    plot_bug_detection_euler(results_data, os.path.join(args.output_dir, "bug-detection-euler.pdf"))
    plot_bug_detection_bars(results_data, os.path.join(args.output_dir, "bug-detection-bars.pdf"))
    plot_runtime(results_data, os.path.join(args.output_dir, "runtime.pdf"))

    # Print bug stats
    total_benchmarks = len(results_data)
    sash_detected, shellcheck_detected, all_bugs_expected = _get_bug_sets(results_data)
    print(all_bugs_expected, file=sys.stderr)
    total_bugs = len(all_bugs_expected)
    print(f"% Total benchmarks: {total_benchmarks}", file=sys.stderr)
    print(f"% Total bugs: {total_bugs}", file=sys.stderr)
    print(f"% {sysname} detected bugs: {len(sash_detected)}", file=sys.stderr)
    print(f"% ShellCheck detected bugs: {len(shellcheck_detected)}", file=sys.stderr)
    print(f"% Both detected bugs: {len(sash_detected & shellcheck_detected)}", file=sys.stderr)
    print(f"% Missed bugs: {len(all_bugs_expected - (sash_detected | shellcheck_detected))}", file=sys.stderr)

if __name__ == "__main__":
    main()
