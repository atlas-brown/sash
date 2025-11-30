import argparse
import pandas as pd
import numpy as np
import os
from io import StringIO

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
    parts = subpath.split(os.sep)[1:]
    result = os.path.join(*parts)
    return result[-10:]

sysname = "SaSh"
figsize = (7, 4)
figsize_small = (4.2, 1)
color_scheme = ["#AA4465", "#FFA69E", "#998650", "#93E1D8"]

def _get_bug_sets(data):
    sash_detected = set()
    shellcheck_detected = set()

    sash_detected = set()
    shellcheck_detected = set()

    for _, row in data.iterrows():
        bench = row["benchmark"]

        sash_bugs = (str(row["actual_results"]).split(";")) if pd.notna(row["actual_results"]) else []
        shell_bugs = (str(row["shellcheck_codes"]).split(";")) if pd.notna(row["shellcheck_codes"]) else []

        if "" in sash_bugs:
            sash_bugs.remove("")
        if "" in shell_bugs:
            shell_bugs.remove("")

        for bug in sash_bugs:
            sash_detected.add(bench + "_" + bug)

        for bug in shell_bugs:
            shellcheck_detected.add(bench + "_" + bug)
    return sash_detected, shellcheck_detected


def plot_bug_detection_euler(data, output_path):
    sash_detected, shellcheck_detected = _get_bug_sets(data)

    only_sash = len(sash_detected - shellcheck_detected)
    both = len(sash_detected & shellcheck_detected)
    only_shell = len(shellcheck_detected - sash_detected)
    # neither = len(data) - (only_sash + both + only_shell)

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
    sash_detected, shellcheck_detected = _get_bug_sets(data)
    both_detected = len(sash_detected & shellcheck_detected)
    sash_detected = len(sash_detected)
    shellcheck_detected = len(shellcheck_detected)

    # create a single horizontal bar with 3 segments: sash only, both, shellcheck only
    plt.figure(figsize=figsize_small)
    labels = [sysname, "Both", "ShellCheck"]
    sizes = [sash_detected - both_detected, both_detected, shellcheck_detected - both_detected]
    colors = color_scheme[:3]
    plt.barh(0, sizes[0], color=colors[0], label=labels[0])
    plt.barh(0, sizes[1], left=sizes[0], color=colors[1], label=labels[1])
    plt.barh(0, sizes[2], left=sizes[0] + sizes[1], color=colors[2], label=labels[2])
    plt.xlabel("Detected Bugs", loc="right")
    plt.yticks([])
    plt.legend(
        fontsize=10,
        loc="upper left",
        frameon=True,
    )
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
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Time (s)")
    plt.yscale("log")
    for bar, loc in zip(bars, locs):
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2, height, f"{loc}", ha='center', va='bottom', fontsize=4)
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

if __name__ == "__main__":
    main()
