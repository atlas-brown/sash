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
color_scheme = ["#AA4465", "#FFA69E", "#998650", "#93E1D8"]


def plot_bug_detection(data, output_path):
    sash_detected = set()
    shellcheck_detected = set()

    sash_detected = set()
    shellcheck_detected = set()

    for _, row in data.iterrows():
        bench = row["benchmark"]

        sash_bugs = set(str(row["actual"]).split(";")) if pd.notna(row["actual"]) else set()
        shell_bugs = set(str(row["shellcheck"]).split(";")) if pd.notna(row["shellcheck"]) else set()

        sash_bugs.discard("")
        shell_bugs.discard("")

        if sash_bugs:
            sash_detected.add(bench)
        if shell_bugs:
            shellcheck_detected.add(bench)

    only_sash = len(sash_detected - shellcheck_detected)
    both = len(sash_detected & shellcheck_detected)
    only_shell = len(shellcheck_detected - sash_detected)

    combination_counts = {
        (1, 0): only_sash,          # Only SaSh
        (1, 1): both,               # Both
        (0, 1): only_shell,         # Only ShellCheck
    }

    plt.figure(figsize=figsize)
    dgm = EulerDiagram(combination_counts, set_labels=[sysname, "ShellCheck"], set_colors=color_scheme)
    plt.title(None)
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
    plot_bug_detection(results_data, os.path.join(args.output_dir, "bug-detection-overview.pdf"))
    plot_runtime(results_data, os.path.join(args.output_dir, "runtime.pdf"))

if __name__ == "__main__":
    main()
