import argparse
import pandas as pd
import numpy as np
import os

import matplotlib.pyplot as plt
from matplotlib_set_diagrams import EulerDiagram

def load_csv(file_path):
    try:
        data = pd.read_csv(file_path)
        return data
    except Exception as e:
        print(f"Error loading CSV file: {e}")
        exit(1)

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
    plot_bug_detection(results_data, os.path.join(args.output_dir, "bug-detection-overview.pdf"))

if __name__ == "__main__":
    main()
