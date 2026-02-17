import argparse
import pandas as pd
import numpy as np
import os
import re
import glob
from io import StringIO
import sys
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
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
_benchmark_info_cache = {}

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


def get_benchmark_info(benchmark_path):
    benchmark_dir = find_benchmark_dir(benchmark_path)
    if benchmark_dir is None:
        return None
    if benchmark_dir in _benchmark_info_cache:
        return _benchmark_info_cache[benchmark_dir]

    try:
        info = yaml.safe_load((benchmark_dir / "info.yaml").read_text(encoding="utf-8"))
    except Exception:
        info = None
    _benchmark_info_cache[benchmark_dir] = info
    return info


def get_info_shellcheck_expected_counter(benchmark_path, kind, fallback_to_bug_default=True):
    info = get_benchmark_info(benchmark_path)
    if not info:
        return Counter()

    benchmark_dir = find_benchmark_dir(benchmark_path)
    if benchmark_dir is None:
        return Counter()

    script_path = Path(benchmark_path)
    if not script_path.is_absolute():
        script_path = ROOT_DIR / script_path
    try:
        rel_path = script_path.relative_to(benchmark_dir).as_posix()
    except ValueError:
        rel_path = script_path.name

    gt = None
    for gt_entry in (info.get("ground_truths") or []):
        if gt_entry.get("kind") == kind and gt_entry.get("path") == rel_path:
            gt = gt_entry
            break

    if gt is None:
        return Counter()

    bugs = info.get("bugs") or {}
    counter = Counter()
    for bug_id, bug_gt in (gt.get("bugs") or {}).items():
        bug_def = bugs.get(bug_id) or {}
        code = bug_gt.get("code") or bug_def.get("code")
        if not code:
            continue

        # Ground-truth value overrides bug-level defaults when present.
        if "shellcheck" in bug_gt:
            shellcheck_code = bug_gt.get("shellcheck")
        elif fallback_to_bug_default:
            shellcheck_code = bug_def.get("shellcheck")
        else:
            shellcheck_code = None
        if not shellcheck_code:
            continue

        lines = bug_gt.get("lines")
        if lines is None:
            lines = bug_gt.get("regression_lines", [])
        for line in lines:
            counter[f"L{line}:{code}"] += 1

    return counter


def benchmark_group_key(benchmark_path):
    benchmark_dir = find_benchmark_dir(benchmark_path)
    if benchmark_dir is not None:
        return str(benchmark_dir)
    p = Path(benchmark_path)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return str(p.parent)

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

def _get_bug_sets_for_kind(data, kind):
    sash_detected = set()
    shellcheck_detected = set()
    all_bugs_expected = set()

    data_view = data
    if "kind" in data_view.columns:
        data_view = data_view[data_view["kind"] == kind]
    elif "benchmark" in data_view.columns:
        benchmarks = data_view["benchmark"].astype(str)
        if kind == "buggy":
            data_view = data_view[
                ~benchmarks.str.endswith("/fixed.sh")
                & ~benchmarks.str.contains("/variants/")
            ]
        elif kind == "fixed":
            data_view = data_view[benchmarks.str.endswith("/fixed.sh")]
        elif kind == "buggy_variant":
            data_view = data_view[benchmarks.str.contains("/variants/bug-")]
        elif kind == "fixed_variant":
            data_view = data_view[benchmarks.str.contains("/variants/fix-")]
        else:
            data_view = data_view.iloc[0:0]

    for _, row in data_view.iterrows():
        bench = row["benchmark"]
        actual_bugs = parse_issue_list(row["actual_results"])
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

        # ShellCheck matching comes from per-ground-truth metadata in info.yaml.
        expected_shell_counter = get_info_shellcheck_expected_counter(
            bench,
            kind,
            fallback_to_bug_default=(kind in {"buggy", "fixed"}),
        )
        expected_keys_by_issue = {}
        for key in expected_keys:
            issue = key.split(f"{bench}_", 1)[1].rsplit("_", 1)[0]
            expected_keys_by_issue.setdefault(issue, []).append(key)

        for issue, keys in expected_keys_by_issue.items():
            matched = min(len(keys), expected_shell_counter[issue])
            for key in keys[:matched]:
                shellcheck_detected.add(key)

    return sash_detected, shellcheck_detected, all_bugs_expected


def _get_bug_sets(data):
    return _get_bug_sets_for_kind(data, "buggy")


def _get_variant_overlay_deltas(data):
    """
    Compute overlay sizes for Original bars using only benchmark families that
    have buggy variants. For each family we compare Original vs Variant on a
    comparable bug budget equal to min(original_bug_count, variant_bug_count),
    so original-only bugs are ignored for highlighting.
    Returns two lists [SaSh, ShellCheck]:
      - detected_to_missed: variant detects fewer than original
      - missed_to_detected: variant detects more than original
    """
    if "kind" in data.columns:
        buggy_rows = data[data["kind"] == "buggy"]
        variant_rows = data[data["kind"] == "buggy_variant"]
    elif "benchmark" in data.columns:
        benchmarks = data["benchmark"].astype(str)
        buggy_rows = data[
            ~benchmarks.str.endswith("/fixed.sh")
            & ~benchmarks.str.contains("/variants/")
        ]
        variant_rows = data[benchmarks.str.contains("/variants/bug-")]
    else:
        return [0, 0], [0, 0]

    buggy_by_family = defaultdict(list)
    variant_by_family = defaultdict(list)
    for _, row in buggy_rows.iterrows():
        buggy_by_family[benchmark_group_key(row["benchmark"])].append(row)
    for _, row in variant_rows.iterrows():
        variant_by_family[benchmark_group_key(row["benchmark"])].append(row)

    orig_detected = [0, 0]  # [SaSh, ShellCheck]
    variant_detected = [0, 0]

    for family in sorted(set(buggy_by_family) & set(variant_by_family)):
        orig_expected_total = 0
        variant_expected_total = 0
        orig_sash_detected = 0
        variant_sash_detected = 0
        orig_shell_detected = 0
        variant_shell_detected = 0

        for row in buggy_by_family[family]:
            kind = str(row.get("kind", "buggy"))
            expected_counter = Counter(parse_issue_list(row["expected_results"]))
            actual_counter = Counter(parse_issue_list(row["actual_results"]))
            shell_counter = get_info_shellcheck_expected_counter(
                row["benchmark"], kind, fallback_to_bug_default=True
            )
            orig_expected_total += sum(expected_counter.values())
            for issue, count in expected_counter.items():
                orig_sash_detected += min(count, actual_counter[issue])
                orig_shell_detected += min(count, shell_counter[issue])

        for row in variant_by_family[family]:
            kind = str(row.get("kind", "buggy_variant"))
            expected_counter = Counter(parse_issue_list(row["expected_results"]))
            actual_counter = Counter(parse_issue_list(row["actual_results"]))
            shell_counter = get_info_shellcheck_expected_counter(
                row["benchmark"], kind, fallback_to_bug_default=False
            )
            variant_expected_total += sum(expected_counter.values())
            for issue, count in expected_counter.items():
                variant_sash_detected += min(count, actual_counter[issue])
                variant_shell_detected += min(count, shell_counter[issue])

        comparable_total = min(orig_expected_total, variant_expected_total)
        if comparable_total <= 0:
            continue

        orig_detected[0] += min(orig_sash_detected, comparable_total)
        variant_detected[0] += min(variant_sash_detected, comparable_total)
        orig_detected[1] += min(orig_shell_detected, comparable_total)
        variant_detected[1] += min(variant_shell_detected, comparable_total)

    detected_to_missed = [
        max(orig_detected[0] - variant_detected[0], 0),
        max(orig_detected[1] - variant_detected[1], 0),
    ]
    missed_to_detected = [
        max(variant_detected[0] - orig_detected[0], 0),
        max(variant_detected[1] - orig_detected[1], 0),
    ]
    return detected_to_missed, missed_to_detected


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
    both_detected = len(sash_detected & shellcheck_detected)
    only_sash = len(sash_detected - shellcheck_detected)
    only_shell = len(shellcheck_detected - sash_detected)
    all_expected = len(all_bugs_expected)

    fixed_total, sash_success, shell_success = _get_fixed_fp_counts(data)
    sash_fp = fixed_total - sash_success
    shell_fp = fixed_total - shell_success
    variant_detected_to_missed, variant_missed_to_detected = _get_variant_overlay_deltas(data)

    # Single axis: two benchmark-kind groups; each group has SaSh/ShellCheck rows.
    fig, ax = plt.subplots(1, 1, figsize=figsize)

    sash_buggy_detected = both_detected + only_sash
    shell_buggy_detected = both_detected + only_shell
    buggy_detected = [sash_buggy_detected, shell_buggy_detected]
    buggy_missed = [all_expected - buggy_detected[0], all_expected - buggy_detected[1]]
    fixed_no_fp = [sash_success, shell_success]
    fixed_fp = [sash_fp, shell_fp]

    # Group by benchmark kind on y-axis; inside each group:
    # upper row = SaSh, lower row = ShellCheck.
    group_buggy = 0.72
    group_fixed = 0.24
    row_gap = 0.14
    bar_height = 0.11
    buggy_rows = [group_buggy + (row_gap / 2), group_buggy - (row_gap / 2)]
    fixed_rows = [group_fixed + (row_gap / 2), group_fixed - (row_gap / 2)]

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

    # Overlay hatch only for bugs that exist in both Original and buggy-variants.
    for i, y in enumerate(buggy_rows):
        detected_hatch = min(variant_detected_to_missed[i], buggy_detected[i])
        missed_hatch = min(variant_missed_to_detected[i], buggy_missed[i])

        if detected_hatch > 0:
            ax.barh(
                y,
                detected_hatch,
                height=bar_height,
                left=buggy_detected[i] - detected_hatch,
                color="none",
                hatch="////",
                edgecolor="0.35",
                linewidth=0.0,
            )
        if missed_hatch > 0:
            ax.barh(
                y,
                missed_hatch,
                height=bar_height,
                left=buggy_detected[i],
                color="none",
                hatch="////",
                edgecolor="0.35",
                linewidth=0.0,
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
    ax.set_yticks([group_buggy, group_fixed], ["Original", "Fixed"])
    ax.set_ylim(0.03, 0.90)
    ax.set_xlabel("Count", loc="right")

    # Annotate row identity on the right side as axis tick labels.
    row_ticks = [
        buggy_rows[0], buggy_rows[1], fixed_rows[0], fixed_rows[1],
    ]
    row_labels = [sysname, "ShellCheck", sysname, "ShellCheck"]
    ax_right = ax.twinx()
    ax_right.set_ylim(ax.get_ylim())
    ax_right.set_yticks(row_ticks)
    ax_right.set_yticklabels(row_labels)
    ax_right.tick_params(axis="y", labelsize=7, length=0, pad=4)
    ax_right.spines["top"].set_visible(False)
    ax_right.spines["left"].set_visible(False)
    ax_right.spines["bottom"].set_visible(False)

    x_points = sorted(set(
        [
            0,
            all_expected,
            fixed_total,
            buggy_detected[0],
            buggy_detected[1],
            fixed_no_fp[0],
            fixed_no_fp[1],
        ]
    ))
    # Keep all data-point ticks, but for close runs (<3 apart) only label the largest one.
    int_points = [int(x) for x in x_points]
    labels = [""] * len(x_points)
    if int_points:
        for i in range(1, len(int_points) + 1):
            is_break = (i == len(int_points)) or ((int_points[i] - int_points[i - 1]) >= 3)
            if is_break:
                labels[i - 1] = str(int_points[i - 1])  # largest tick in this close run
    ax.set_xticks(x_points)
    ax.set_xticklabels(labels)
    ax.tick_params(axis="x", labelsize=8)
    # Deduplicate legend labels because multiple grouped bars reuse the same names.
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    by_label["Variant differs from original"] = plt.Rectangle(
        (0, 0),
        1,
        1,
        facecolor="none",
        hatch="////",
        edgecolor="0.35",
        linewidth=0.0,
    )
    ax.legend(
        by_label.values(),
        by_label.keys(),
        fontsize=8,
        loc="center right",
        ncol=1,
        frameon=True,
    )

    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


def _get_kind_fp_counts(data, kind_selector):
    data_view = data
    if "kind" in data_view.columns:
        kind_mask = data_view["kind"].apply(kind_selector)
        selected_view = data_view[kind_mask]
    elif "benchmark" in data_view.columns:
        selected_view = data_view[data_view["benchmark"].apply(kind_selector)]
    else:
        selected_view = data_view

    total = 0
    sash_fp = 0
    shell_fp = 0

    for _, row in selected_view.iterrows():
        bench = row["benchmark"]
        kind = str(row.get("kind", "fixed"))
        sash_reports = Counter(parse_issue_list(row["actual_results"]))
        expected_ids = Counter(parse_issue_list(row["expected_results"]))
        expected_shell = get_info_shellcheck_expected_counter(
            bench, kind, fallback_to_bug_default=True
        )
        total += sum(expected_ids.values())

        # False positive for this metric means reporting the corresponding original bug.
        for issue, expected_count in expected_ids.items():
            sash_fp += min(expected_count, sash_reports[issue])
        for issue, expected_count in expected_shell.items():
            shell_fp += min(expected_count, expected_ids[issue])

    sash_success = total - sash_fp
    shell_success = total - shell_fp
    return total, sash_success, shell_success


def _get_fixed_fp_counts(data):
    return _get_kind_fp_counts(data, lambda k: str(k) == "fixed")


def _get_variant_detection_counts(data):
    data_view = data
    if "kind" in data_view.columns:
        data_view = data_view[data_view["kind"] == "buggy_variant"]
    elif "benchmark" in data_view.columns:
        data_view = data_view[data_view["benchmark"].astype(str).str.contains("/variants/bug-")]
    else:
        data_view = data_view.iloc[0:0]

    sash_detected = set()
    shellcheck_detected = set()
    all_bugs_expected = set()

    for _, row in data_view.iterrows():
        bench = row["benchmark"]
        expected_bugs = parse_issue_list(row["expected_results"])
        actual_bugs = parse_issue_list(row["actual_results"])

        expected_counter = Counter()
        expected_keys = []
        for issue in expected_bugs:
            idx = expected_counter[issue]
            expected_counter[issue] += 1
            key = f"{bench}_{issue}_{idx}"
            expected_keys.append(key)
            all_bugs_expected.add(key)

        actual_counter = Counter(actual_bugs)
        for issue, count in expected_counter.items():
            matched = min(count, actual_counter[issue])
            for idx in range(matched):
                sash_detected.add(f"{bench}_{issue}_{idx}")

        expected_shell_counter = get_info_shellcheck_expected_counter(
            bench,
            row.get("kind", "buggy_variant"),
            fallback_to_bug_default=False,
        )
        expected_keys_by_issue = {}
        for key in expected_keys:
            issue = key.split(f"{bench}_", 1)[1].rsplit("_", 1)[0]
            expected_keys_by_issue.setdefault(issue, []).append(key)

        for issue, keys in expected_keys_by_issue.items():
            matched = min(len(keys), expected_shell_counter[issue])
            for key in keys[:matched]:
                shellcheck_detected.add(key)

    return len(all_bugs_expected), len(sash_detected), len(shellcheck_detected)


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


def plot_timeout_sweep_bug_catch(timeout_sweep_dir, output_path):
    series_specs = [
        ("dfs_on", "Full SaSh", color_scheme[0], re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_on\.csv$")),
        ("dfs_no_targeted", "No targeted DFS", color_scheme[1], re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_no_targeted\.csv$")),
        ("dfs_no_unbound_empty", "No unbound-empty DFS", color_scheme[3], re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_no_unbound_empty\.csv$")),
        ("dfs_off", "No DFS", color_scheme[2], re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_off\.csv$")),
    ]
    series_paths = {
        key: glob.glob(os.path.join(timeout_sweep_dir, f"results_t*_{key}.csv"))
        for key, _, _, _ in series_specs
    }

    if not any(series_paths.values()):
        # Backward-compatible fallback: legacy single-series files.
        series_paths["dfs_on"] = glob.glob(os.path.join(timeout_sweep_dir, "results_t*.csv"))
        series_specs = [
            ("dfs_on", "Full SaSh", color_scheme[0], re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)\.csv$")),
        ]

    if not any(series_paths.values()):
        print(
            f"% No timeout-sweep CSV files found in {timeout_sweep_dir}; skipping timeout plot",
            file=sys.stderr,
        )
        return

    def collect_series(paths, regex):
        timeout_points = []
        bugs_caught = []
        bug_totals = []
        for path in paths:
            match = regex.search(os.path.basename(path))
            if not match:
                continue
            timeout_value = float(match.group(1))
            data = load_csv(path)
            buggy_data = data[data["kind"] == "buggy"].copy() if "kind" in data.columns else data
            sash_detected, _, all_expected = _get_bug_sets(buggy_data)
            timeout_points.append(timeout_value)
            bugs_caught.append(len(sash_detected))
            bug_totals.append(len(all_expected))
        if not timeout_points:
            return np.array([]), np.array([]), []
        order = np.argsort(timeout_points)
        x = np.array(timeout_points)[order]
        y = np.array(bugs_caught)[order]
        return x, y, bug_totals

    plt.figure(figsize=(figsize_small[0], 2.3))
    all_x_arrays = []
    all_totals = []
    for key, label, color, regex in series_specs:
        x_vals, y_vals, totals = collect_series(series_paths.get(key, []), regex)
        all_totals.extend(totals)
        if len(x_vals) == 0:
            continue
        all_x_arrays.append(x_vals)
        plt.plot(
            x_vals,
            y_vals,
            marker="o",
            color=color,
            linewidth=1.8,
            markersize=4,
            label=label,
        )

    if all_totals:
        total = int(round(float(np.median(all_totals))))
        plt.axhline(
            y=total,
            linestyle="--",
            linewidth=1.0,
            color="gray",
            label=f"Total bugs ({total})",
        )

    plt.xlabel("Timeout (s)")
    plt.ylabel("Bugs Caught")
    x_ticks = sorted(set(np.concatenate(all_x_arrays).tolist())) if all_x_arrays else []
    if x_ticks:
        plt.xticks(x_ticks)
    plt.grid(axis="y", alpha=0.25, linestyle=":")
    plt.legend(fontsize=8, loc="lower right", frameon=True)
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
    timeout_sweep_dir = os.path.join(args.output_dir, "timeout-sweep")
    plot_timeout_sweep_bug_catch(
        timeout_sweep_dir,
        os.path.join(args.output_dir, "timeout-sweep-bugs-caught.pdf"),
    )

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
    variant_total, variant_sash_detected, variant_shell_detected = _get_variant_detection_counts(all_results)
    print(f"% Variant bug instances: {variant_total}", file=sys.stderr)
    print(f"% {sysname} variants detected bugs: {variant_sash_detected}", file=sys.stderr)
    print(f"% ShellCheck variants detected bugs: {variant_shell_detected}", file=sys.stderr)
    print(f"% {sysname} variants missed bugs: {variant_total - variant_sash_detected}", file=sys.stderr)
    print(f"% ShellCheck variants missed bugs: {variant_total - variant_shell_detected}", file=sys.stderr)

if __name__ == "__main__":
    main()
