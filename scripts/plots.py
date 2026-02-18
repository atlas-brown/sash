import argparse
import pandas as pd
import numpy as np
import os
import re
import glob
from io import StringIO
import sys
import subprocess
import shlex
from pathlib import Path
from collections import Counter, defaultdict
import yaml
from benchmark_metadata import benchmark_key, benchmark_display_name, short_name
from bug_depth_stats import compute_script_metrics

import matplotlib.pyplot as plt
from matplotlib_set_diagrams import EulerDiagram
from matplotlib.lines import Line2D


def extract_issue_code(issue):
    """
    Issue IDs are typically 'L<line>:<code>'.
    Fall back to the raw string when no line prefix exists.
    """
    if ":" in issue:
        return issue.split(":", 1)[1]
    return issue


def parse_issue_list(value):
    if pd.isna(value):
        return []
    issues = [item.strip() for item in str(value).split(";") if item and item.strip()]
    return [i for i in issues if extract_issue_code(i) not in OOS_CODES]


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
    script_path = resolve_benchmark_path(path)
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
with (ROOT_DIR / "benchmarks" / "codes_out_of_scope.yaml").open("r", encoding="utf-8") as f:
    OOS_CODES = set(yaml.safe_load(f) or [])

_benchmark_dir_cache = {}
_shellcheck_map_cache = {}
_benchmark_info_cache = {}
_shellcheck_missing_variant_cache = {}


def resolve_benchmark_path(path):
    """
    Resolve benchmark/script paths robustly across machines.
    If a path includes a valid 'benchmarks/...' suffix but has a foreign prefix
    (e.g., cloud machine absolute paths), remap it to this repo's ROOT_DIR.
    """
    p = Path(str(path))

    # Direct hit.
    if p.exists():
        return p.resolve()

    # Repo-relative path.
    if not p.is_absolute():
        local = (ROOT_DIR / p)
        if local.exists():
            return local.resolve()

    # Foreign absolute path with reusable benchmarks suffix.
    parts = p.parts
    if "benchmarks" in parts:
        idx = parts.index("benchmarks")
        local = ROOT_DIR / Path(*parts[idx:])
        if local.exists():
            return local.resolve()

    # Best-effort fallback.
    return (ROOT_DIR / p).resolve() if not p.is_absolute() else p

def find_benchmark_dir(benchmark_path):
    if benchmark_path in _benchmark_dir_cache:
        return _benchmark_dir_cache[benchmark_path]

    p = resolve_benchmark_path(benchmark_path)
    candidates = [p]

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

    script_path = resolve_benchmark_path(benchmark_path)
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


def get_info_shellcheck_no_variant_counter(benchmark_path, kind):
    """
    For a given buggy/original ground truth row, return issue IDs (L<line>:<code>)
    whose bug IDs have ShellCheck support in the original, but no buggy_variant
    counterpart exists in info.yaml.
    """
    cache_key = (str(benchmark_path), str(kind))
    if cache_key in _shellcheck_missing_variant_cache:
        return _shellcheck_missing_variant_cache[cache_key]

    info = get_benchmark_info(benchmark_path)
    if not info:
        _shellcheck_missing_variant_cache[cache_key] = Counter()
        return Counter()

    benchmark_dir = find_benchmark_dir(benchmark_path)
    if benchmark_dir is None:
        _shellcheck_missing_variant_cache[cache_key] = Counter()
        return Counter()

    script_path = resolve_benchmark_path(benchmark_path)
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
        _shellcheck_missing_variant_cache[cache_key] = Counter()
        return Counter()

    variant_bug_ids = set()
    for gt_entry in (info.get("ground_truths") or []):
        if gt_entry.get("kind") == "buggy_variant":
            variant_bug_ids.update((gt_entry.get("bugs") or {}).keys())

    bugs = info.get("bugs") or {}
    counter = Counter()
    for bug_id, bug_gt in (gt.get("bugs") or {}).items():
        if bug_id in variant_bug_ids:
            continue

        bug_def = bugs.get(bug_id) or {}
        shellcheck_code = bug_def.get("shellcheck")
        code = bug_gt.get("code") or bug_def.get("code")
        if not shellcheck_code or not code:
            continue

        lines = bug_gt.get("lines")
        if lines is None:
            lines = bug_gt.get("regression_lines", [])
        for line in lines:
            counter[f"L{line}:{code}"] += 1

    _shellcheck_missing_variant_cache[cache_key] = counter
    return counter


def benchmark_group_key(benchmark_path):
    benchmark_dir = find_benchmark_dir(benchmark_path)
    if benchmark_dir is not None:
        return str(benchmark_dir)
    p = resolve_benchmark_path(benchmark_path)
    return str(p.parent)


def get_benchmark_segments(data, kind):
    """
    Return ordered (label, expected_bug_count) segments for benchmark families.
    Order follows first appearance in the input CSV.
    """
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
        else:
            data_view = data_view.iloc[0:0]
    else:
        return []

    ordered = []
    by_family = {}
    for _, row in data_view.iterrows():
        family = benchmark_group_key(row["benchmark"])
        expected_count = len(parse_issue_list(row["expected_results"]))
        if family not in by_family:
            by_family[family] = {
                "label": short_name(row["benchmark"]),
                "count": 0,
            }
            ordered.append(family)
        by_family[family]["count"] += expected_count

    return [
        (by_family[f]["label"], by_family[f]["count"])
        for f in ordered
        if by_family[f]["count"] > 0
    ]

def load_csv(file_path):
    try:
        data = pd.read_csv(file_path)
        return data
    except Exception as e:
        print(f"Error loading CSV file: {e}")
        exit(1)

def get_loc(path):
    resolved = resolve_benchmark_path(path)
    proc = os.popen(f"cloc --json {shlex.quote(str(resolved))}")
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
color_scheme = plt.get_cmap("Pastel1").colors
color_red = color_scheme[0]
color_green = color_scheme[2]

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

    # Also texture ShellCheck-detected original bugs that have no buggy_variant
    # counterpart in metadata. These are meaningful original-only detections.
    shell_missing_variant_detected = 0
    for _, row in buggy_rows.iterrows():
        kind = str(row.get("kind", "buggy"))
        shell_counter = get_info_shellcheck_expected_counter(
            row["benchmark"], kind, fallback_to_bug_default=True
        )
        missing_variant_counter = get_info_shellcheck_no_variant_counter(
            row["benchmark"], kind
        )
        for issue, count in missing_variant_counter.items():
            shell_missing_variant_detected += min(count, shell_counter[issue])

    detected_to_missed[1] += shell_missing_variant_detected
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
    fig, ax = plt.subplots(1, 1, figsize=(9, 2))

    sash_buggy_detected = both_detected + only_sash
    shell_buggy_detected = both_detected + only_shell
    buggy_detected = [sash_buggy_detected, shell_buggy_detected]
    buggy_missed = [all_expected - buggy_detected[0], all_expected - buggy_detected[1]]
    fixed_no_fp = [sash_success, shell_success]
    fixed_fp = [sash_fp, shell_fp]

    # Group by benchmark kind on y-axis; inside each group:
    # upper row = SaSh, lower row = ShellCheck.
    group_buggy = 0.59
    group_fixed = 0.41
    row_gap = 0.09
    bar_height = 0.06
    buggy_rows = [group_buggy + (row_gap / 2), group_buggy - (row_gap / 2)]
    fixed_rows = [group_fixed + (row_gap / 2), group_fixed - (row_gap / 2)]

    # Use consistent semantics across Original/Fixed:
    # good = detected (buggy) / no false positive (fixed)
    # bad = missed (buggy) / false positive (fixed)
    good_color = color_green
    bad_color = color_red

    # Buggy bars
    ax.barh(
        buggy_rows,
        buggy_detected,
        height=bar_height,
        color=good_color,
        label="Good (Detected/No FP)",
    )
    ax.barh(
        buggy_rows,
        buggy_missed,
        height=bar_height,
        left=buggy_detected,
        color=bad_color,
        label="Bad (Missed/FP)",
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
                edgecolor=color_red,
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
    ax.barh(fixed_rows, fixed_no_fp, height=bar_height, color=good_color, label="_nolegend_")
    ax.barh(
        fixed_rows,
        fixed_fp,
        height=bar_height,
        left=fixed_no_fp,
        color=bad_color,
        label="_nolegend_",
    )

    tick_size = 8

    max_total = max(all_expected, fixed_total, 1)
    ax.set_xlim(0, max_total)
    ax.set_yticks([group_buggy, group_fixed], ["Buggy", "Fixed"], fontsize=tick_size)
    ax.set_ylim(0.28, 0.72)
    ax.set_xlabel("Benchmark", loc="right", fontsize=tick_size)

    # Annotate row identity on the right side as axis tick labels.
    row_ticks = [
        buggy_rows[0], buggy_rows[1], fixed_rows[0], fixed_rows[1],
    ]
    row_labels = [sysname, "ShellCheck", sysname, "ShellCheck"]
    ax_right = ax.twinx()
    ax_right.set_ylim(ax.get_ylim())
    ax_right.set_yticks(row_ticks)
    ax_right.set_yticklabels(row_labels, fontsize=tick_size)
    ax_right.tick_params(axis="y", labelsize=tick_size, length=0, pad=4)
    ax_right.spines["top"].set_visible(False)
    ax_right.spines["left"].set_visible(False)
    ax_right.spines["bottom"].set_visible(False)

    def segment_ticks(segments):
        tick_pos = []
        tick_labels = []
        current = 0.0
        for label, count in segments:
            tick_pos.append(current + (count / 2.0))
            tick_labels.append(label)
            current += count
        return tick_pos, tick_labels

    fixed_segments = get_benchmark_segments(data, "fixed")
    buggy_segments = get_benchmark_segments(data, "buggy")

    if fixed_segments:
        bottom_pos, bottom_labels = segment_ticks(fixed_segments)
        ax.set_xticks(bottom_pos)
        ax.set_xticklabels(bottom_labels, rotation=90, ha="right")
        ax.tick_params(axis="x", labelsize=8, length=2.5, width=0.6, pad=1)
    else:
        ax.set_xticks([0, max_total])
        ax.set_xticklabels(["0", str(int(max_total))])
        ax.tick_params(axis="x", labelsize=8, length=2.5, width=0.6)

    # if buggy_segments:
    #     top_pos, top_labels = segment_ticks(buggy_segments)
    #     ax_top = ax.twiny()
    #     ax_top.set_xlim(ax.get_xlim())
    #     ax_top.set_xticks(top_pos)
    #     ax_top.set_xticklabels(top_labels, rotation=45, ha="left")
    #     ax_top.tick_params(axis="x", labelsize=8, length=0, pad=1)
    #     ax_top.spines["left"].set_visible(False)
    #     ax_top.spines["right"].set_visible(False)
    #     ax_top.spines["bottom"].set_visible(False)

    # Label stack transition points (where bar color changes).
    transition_points = [
        (buggy_detected[0], buggy_rows[0], buggy_missed[0]),
        (buggy_detected[1], buggy_rows[1], buggy_missed[1]),
        (fixed_no_fp[0], fixed_rows[0], fixed_fp[0]),
        (fixed_no_fp[1], fixed_rows[1], fixed_fp[1]),
    ]
    for x, y, right_segment in transition_points:
        if x <= 0 or right_segment <= 0:
            continue
        if x >= max_total:
            continue
        ha = "left"
        x_text = x + 0.4
        if x > max_total - 2.0:
            ha = "right"
            x_text = x - 0.4
        ax.text(
            x_text,
            y,
            str(int(x)),
            fontsize=6,
            va="center",
            ha=ha,
            color="black",
        )
    # Deduplicate legend labels because multiple grouped bars reuse the same names.
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    by_label["Missed on variant"] = plt.Rectangle(
        (0, 0),
        1,
        1,
        facecolor="none",
        hatch="////",
        edgecolor=color_red,
        linewidth=0.0,
    )
    ax.legend(
        by_label.values(),
        by_label.keys(),
        fontsize=8,
        loc="upper left",
        ncol=1,
        frameon=True,
    )

    plt.tight_layout(rect=[0.0, 0.08, 1.0, 0.92])
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
    plt.figure(figsize=(9, 3))
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
    timeout_line_color = "#C62828"
    for sym_t, sol_t, bar_sym, bar_solver in zip(symexec_times, solver_times, bars_sym, bars_solver):
        sym_timed_out = symexec_timeout is not None and sym_t >= (symexec_timeout - tol)
        solver_timed_out = solver_timeout is not None and sol_t >= (solver_timeout - tol)
        if sym_timed_out:
            x0 = bar_sym.get_x()
            x1 = x0 + bar_sym.get_width()
            y = bar_sym.get_height()
            plt.hlines(y, x0, x1, colors=timeout_line_color, linewidth=1.8, zorder=5)
        if solver_timed_out:
            x0 = bar_solver.get_x()
            x1 = x0 + bar_solver.get_width()
            y = bar_solver.get_height()
            plt.hlines(y, x0, x1, colors=timeout_line_color, linewidth=1.8, zorder=5)
    plt.margins(x=0.02)  # keep a slight gap at plot borders
    plt.margins(y=0.10)  # keep a slight gap at top for bar labels

    plt.xticks(x, benchmarks, rotation=45, ha="right", rotation_mode="anchor", fontsize=7)
    plt.ylabel("Time (s)")
    plt.yscale("log")
    for xi, depth, sym_t, sol_t in zip(x, depth_labels, symexec_times, solver_times):
        top_h = max(sym_t, sol_t)
        y = top_h * 1.06 if top_h > 0 else 1e-3
        plt.text(xi, y, f"{int(depth)}", ha='center', va='bottom', fontsize=7)

    handles, labels = plt.gca().get_legend_handles_labels()
    handles.append(Line2D([], [], color=timeout_line_color, linewidth=1.8, label="Timeout"))
    labels.append("Timeout")
    handles.append(
        Line2D(
            [],
            [],
            linestyle="none",
            marker="$n$",
            markersize=5,
            color="0.35",
            label="Bug depth",
        )
    )
    labels.append("Bug depth")
    plt.legend(handles, labels, fontsize=8, loc="lower right", frameon=True)
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
            timeout_value = 2.0 * float(match.group(1))
            data = load_csv(path)
            buggy_data = data[data["kind"] == "buggy"].copy() if "kind" in data.columns else data
            sash_detected, _, all_expected = _get_bug_sets(buggy_data)
            timeout_points.append(timeout_value)
            bugs_caught.append(len(sash_detected))
            bug_totals.append(len(all_expected))
        if not timeout_points:
            return np.array([]), np.array([]), [], np.array([])
        order = np.argsort(timeout_points)
        x = np.array(timeout_points)[order]
        y = np.array(bugs_caught)[order]
        t = np.array(timeout_points)[order]
        return x, y, bug_totals, t

    plt.figure(figsize=(figsize_small[0], 2.6))
    all_x_arrays = []
    all_y_arrays = []
    all_totals = []
    all_timeout_values = []
    for key, label, color, regex in series_specs:
        x_vals, y_vals, totals, timeout_vals = collect_series(series_paths.get(key, []), regex)
        all_totals.extend(totals)
        all_timeout_values.extend(timeout_vals.tolist() if len(timeout_vals) > 0 else [])
        if len(x_vals) == 0:
            continue
        all_x_arrays.append(x_vals)
        all_y_arrays.append(y_vals)
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
            label="_nolegend_",
        )
        ax = plt.gca()
        y_ticks = set(ax.get_yticks().tolist())
        y_ticks.add(float(total))
        ax.set_yticks(sorted(y_ticks))
        if all_y_arrays:
            min_seen = float(np.min(np.concatenate(all_y_arrays)))
            y_lower = max(0.0, min_seen - 2.0)
        else:
            y_lower = 0.0
        ax.set_ylim(y_lower, float(total) + 1.0)

    plt.xlabel("Timeout (s)")
    plt.ylabel("Bugs Caught")
    if all_x_arrays:
        timeout_ticks = sorted({int(round(v)) for v in all_timeout_values})
        rightmost_tick = round(float(np.max(np.concatenate(all_x_arrays))), 2)
        x_ticks = sorted(set(timeout_ticks + [rightmost_tick]))
        timeout_tick_set = set(timeout_ticks)
        x_tick_labels = [
            str(int(x)) if x in timeout_tick_set else f"{x:.2f}"
            for x in x_ticks
        ]
        plt.xticks(x_ticks, x_tick_labels)
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
