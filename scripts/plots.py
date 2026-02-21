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
from matplotlib.colors import to_rgba
from matplotlib.patches import Rectangle


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
            lines = script_path.read_text(
                encoding="utf-8", errors="surrogateescape"
            ).splitlines()
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
with (ROOT_DIR / "benchmarks" / "codes_out_of_scope.yaml").open(
    "r", encoding="utf-8"
) as f:
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
        local = ROOT_DIR / p
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


def get_info_shellcheck_expected_counter(
    benchmark_path, kind, fallback_to_bug_default=True
):
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
    for gt_entry in info.get("ground_truths") or []:
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
    for gt_entry in info.get("ground_truths") or []:
        if gt_entry.get("kind") == kind and gt_entry.get("path") == rel_path:
            gt = gt_entry
            break
    if gt is None:
        _shellcheck_missing_variant_cache[cache_key] = Counter()
        return Counter()

    variant_bug_ids = set()
    for gt_entry in info.get("ground_truths") or []:
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


def has_coverage_values(data):
    if "ast_coverage_pct" in data.columns:
        cov = pd.to_numeric(data["ast_coverage_pct"], errors="coerce")
        if cov.notna().any():
            return True
    if {"ast_nodes_total", "ast_nodes_interpreted"}.issubset(set(data.columns)):
        total = pd.to_numeric(data["ast_nodes_total"], errors="coerce")
        interp = pd.to_numeric(data["ast_nodes_interpreted"], errors="coerce")
        if (total > 0).any() and interp.notna().any():
            return True
    return False


def inject_coverage_from_timeout_sweep(buggy_data, timeout_sweep_dir):
    """Fill missing coverage columns from the latest full-SaSh timeout-sweep CSV."""
    paths_dfs_on = glob.glob(os.path.join(timeout_sweep_dir, "results_t*_dfs_on.csv"))
    if paths_dfs_on:
        timeout_re = re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_on\.csv$")
        candidate_paths = paths_dfs_on
    else:
        timeout_re = re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)\.csv$")
        candidate_paths = glob.glob(os.path.join(timeout_sweep_dir, "results_t*.csv"))

    if not candidate_paths:
        return buggy_data

    best_path = None
    best_timeout = -1.0
    for path in candidate_paths:
        m = timeout_re.search(os.path.basename(path))
        if not m:
            continue
        t = float(m.group(1))
        if t > best_timeout:
            best_timeout = t
            best_path = path
    if best_path is None:
        return buggy_data

    sweep = load_csv(best_path)
    if "kind" in sweep.columns:
        sweep = sweep[sweep["kind"] == "buggy"].copy()
    cov_cols = ["ast_coverage_pct", "ast_nodes_total", "ast_nodes_interpreted"]
    if "benchmark" not in sweep.columns or not any(c in sweep.columns for c in cov_cols):
        return buggy_data

    enriched = buggy_data.copy()
    if "benchmark" not in enriched.columns:
        return enriched

    enriched["_bench_key"] = enriched["benchmark"].astype(str).apply(benchmark_key)
    sweep["_bench_key"] = sweep["benchmark"].astype(str).apply(benchmark_key)
    keep_cols = ["_bench_key"] + [c for c in cov_cols if c in sweep.columns]
    sweep_cov = sweep[keep_cols].drop_duplicates(subset=["_bench_key"], keep="last")

    merged = enriched.merge(sweep_cov, on="_bench_key", how="left", suffixes=("", "_sw"))
    for col in cov_cols:
        sw_col = f"{col}_sw"
        if sw_col not in merged.columns:
            continue
        if col not in merged.columns:
            merged[col] = merged[sw_col]
        else:
            merged[col] = merged[col].where(merged[col].notna(), merged[sw_col])
        merged = merged.drop(columns=[sw_col])
    merged = merged.drop(columns=["_bench_key"])
    return merged


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
# Optional manual prefix order for heatmap columns.
# Fill with short benchmark IDs (e.g., "njv") and/or benchmark keys
# (e.g., "high_profile/c02-n"). Remaining benchmarks keep auto-sort order.
HEATMAP_MANUAL_BENCHMARK_ORDER = []


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
        (1, 0): only_sash,  # Only SaSh
        (1, 1): both,  # Both
        (0, 1): only_shell,  # Only ShellCheck
        # (0, 0): neither             # Neither
    }

    plt.figure(figsize=figsize_small)
    dgm = EulerDiagram(
        combination_counts, set_labels=[sysname, "ShellCheck"], set_colors=color_scheme
    )
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
    variant_detected_to_missed, variant_missed_to_detected = (
        _get_variant_overlay_deltas(data)
    )

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
    ax.barh(
        fixed_rows, fixed_no_fp, height=bar_height, color=good_color, label="_nolegend_"
    )
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
        buggy_rows[0],
        buggy_rows[1],
        fixed_rows[0],
        fixed_rows[1],
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


def _plot_system_good_bad_panel(
    ax, title, good_counts, bad_counts, max_total, show_yticklabels=False
):
    row_positions = [0.54, 0.46]
    bar_height = 0.07
    good_color = color_green
    bad_color = color_red

    ax.barh(
        row_positions,
        good_counts,
        height=bar_height,
        color=good_color,
    )
    ax.barh(
        row_positions,
        bad_counts,
        height=bar_height,
        left=good_counts,
        color=bad_color,
    )

    ax.set_title(title, fontsize=10)
    ax.set_xlim(0, max(max_total, 1))
    bar_half = bar_height / 2.0
    y_min = min(row_positions) - bar_half - 0.01
    y_max = max(row_positions) + bar_half + 0.01
    ax.set_ylim(y_min, y_max)
    interest_ticks = [0, max(max_total, 1)]
    for value in good_counts:
        if 0 < value < max_total:
            interest_ticks.append(value)
    ticks_sorted = sorted(set(interest_ticks))
    filtered_ticks = []
    for tick in ticks_sorted:
        if not filtered_ticks or (tick - filtered_ticks[-1]) >= 2:
            filtered_ticks.append(tick)
        elif tick == ticks_sorted[-1]:
            # Keep the right-most endpoint tick even if it is close to the previous one.
            filtered_ticks[-1] = tick
    ax.set_xticks(filtered_ticks)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="x", linestyle=":", linewidth=0.6, alpha=0.5)
    ax.set_axisbelow(True)

    if show_yticklabels:
        ax.set_yticks(row_positions)
        ax.set_yticklabels([sysname, "ShellCheck"], fontsize=8)
    else:
        ax.set_yticks(row_positions)
        ax.set_yticklabels([])

    if sum(good_counts) + sum(bad_counts) == 0:
        ax.text(
            0.5,
            0.5,
            "No data",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=8,
        )


def plot_bug_detection_bars_split_versions(data, output_path):
    buggy_sash, buggy_shell, buggy_expected = _get_bug_sets_for_kind(data, "buggy")
    variants_sash, variants_shell, variants_expected = _get_bug_sets_for_kind(
        data, "buggy_variant"
    )
    fixed_total, fixed_sash_no_fp, fixed_shell_no_fp = _get_fixed_fp_counts(data)

    buggy_total = len(buggy_expected)
    variants_total = len(variants_expected)

    buggy_good = [len(buggy_sash), len(buggy_shell)]
    buggy_bad = [buggy_total - buggy_good[0], buggy_total - buggy_good[1]]

    variants_good = [len(variants_sash), len(variants_shell)]
    variants_bad = [
        variants_total - variants_good[0],
        variants_total - variants_good[1],
    ]

    fixed_good = [fixed_sash_no_fp, fixed_shell_no_fp]
    fixed_bad = [fixed_total - fixed_sash_no_fp, fixed_total - fixed_shell_no_fp]

    max_total = max(
        buggy_total,
        variants_total,
        fixed_total,
        1,
    )

    fig, axes = plt.subplots(1, 3, figsize=(10.5, 1.8), sharey=True)
    _plot_system_good_bad_panel(
        axes[0],
        "Buggy",
        buggy_good,
        buggy_bad,
        max_total,
        show_yticklabels=True,
    )
    _plot_system_good_bad_panel(
        axes[1],
        "Variants",
        variants_good,
        variants_bad,
        max_total,
    )
    _plot_system_good_bad_panel(
        axes[2],
        "Fixed",
        fixed_good,
        fixed_bad,
        max_total,
    )

    fig.supxlabel("Bug Instances", fontsize=9, y=0.24)
    legend_handles = [
        Rectangle(
            (0, 0),
            1,
            1,
            facecolor=color_green,
            edgecolor=color_green,
            label="Good (Detected/No FP)",
        ),
        Rectangle(
            (0, 0),
            1,
            1,
            facecolor=color_red,
            edgecolor=color_red,
            label="Bad (Missed/FP)",
        ),
        Line2D([], [], linestyle="none", label="SaSh (Top)"),
        Line2D([], [], linestyle="none", label="ShellCheck (Bottom)"),
    ]
    fig.legend(
        handles=legend_handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=4,
        frameon=False,
        fontsize=8,
    )
    plt.tight_layout(rect=[0.0, 0.15, 1.0, 1.0])
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


def _benchmark_kind_rows(data, kind):
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
        data_view = data_view.iloc[0:0]
    return data_view


def _collect_benchmark_success(data, kind):
    """
    Aggregate benchmark-family success counts for one kind.
    For kind='buggy', success means bug detected.
    For kind='fixed', success means no false positive on the corresponding bug.
    """
    rows = _benchmark_kind_rows(data, kind)
    by_family = {}
    order = []

    for _, row in rows.iterrows():
        family = benchmark_group_key(row["benchmark"])
        if family not in by_family:
            by_family[family] = {
                "label": short_name(row["benchmark"]),
                "total": 0,
                "sash_good": 0,
                "shell_good": 0,
            }
            order.append(family)

        expected_counter = Counter(parse_issue_list(row["expected_results"]))
        actual_counter = Counter(parse_issue_list(row["actual_results"]))
        expected_shell_counter = get_info_shellcheck_expected_counter(
            row["benchmark"], kind, fallback_to_bug_default=(kind in {"buggy", "fixed"})
        )

        total = sum(expected_counter.values())
        sash_match = sum(
            min(expected_count, actual_counter[issue])
            for issue, expected_count in expected_counter.items()
        )
        shell_match = sum(
            min(expected_count, expected_shell_counter[issue])
            for issue, expected_count in expected_counter.items()
        )

        by_family[family]["total"] += total
        if kind == "buggy":
            by_family[family]["sash_good"] += sash_match
            by_family[family]["shell_good"] += shell_match
        else:
            by_family[family]["sash_good"] += max(total - sash_match, 0)
            by_family[family]["shell_good"] += max(total - shell_match, 0)

    return by_family, order


def _get_variant_misses_by_family(data):
    """
    Per benchmark-family misses on buggy variants for SaSh and ShellCheck.
    """
    data_view = data
    if "kind" in data_view.columns:
        data_view = data_view[data_view["kind"] == "buggy_variant"]
    elif "benchmark" in data_view.columns:
        data_view = data_view[
            data_view["benchmark"].astype(str).str.contains("/variants/bug-")
        ]
    else:
        data_view = data_view.iloc[0:0]

    by_family = defaultdict(lambda: {"sash_missed": 0, "shell_missed": 0})

    for _, row in data_view.iterrows():
        family = benchmark_group_key(row["benchmark"])
        expected_counter = Counter(parse_issue_list(row["expected_results"]))
        actual_counter = Counter(parse_issue_list(row["actual_results"]))
        expected_shell_counter = get_info_shellcheck_expected_counter(
            row["benchmark"],
            row.get("kind", "buggy_variant"),
            fallback_to_bug_default=False,
        )

        for issue, expected_count in expected_counter.items():
            sash_hits = min(expected_count, actual_counter[issue])
            shell_hits = min(expected_count, expected_shell_counter[issue])
            by_family[family]["sash_missed"] += max(expected_count - sash_hits, 0)
            by_family[family]["shell_missed"] += max(expected_count - shell_hits, 0)

    return by_family


def plot_bug_detection_heatmap(data, output_path):
    buggy_stats, buggy_order = _collect_benchmark_success(data, "buggy")
    fixed_stats, fixed_order = _collect_benchmark_success(data, "fixed")
    variant_misses = _get_variant_misses_by_family(data)

    # Preserve buggy benchmark order for columns; append fixed-only families, if any.
    families = list(buggy_order)
    for family in fixed_order:
        if family not in families:
            families.append(family)

    if not families:
        return

    # Group benchmarks where ShellCheck fails on buggy benchmarks together.
    original_pos = {family: idx for idx, family in enumerate(families)}

    def shellcheck_failure_key(family):
        buggy = buggy_stats.get(family, {"total": 0, "shell_good": 0})
        buggy_miss = max(buggy["total"] - buggy["shell_good"], 0)
        any_failure = buggy_miss > 0
        # Buggy-failing families first; then by buggy failure magnitude;
        # keep prior order as tiebreaker.
        return (0 if any_failure else 1, -buggy_miss, original_pos[family])

    families = sorted(families, key=shellcheck_failure_key)

    def family_key(family):
        p = Path(str(family))
        parts = p.parts
        if "benchmarks" in parts:
            idx = parts.index("benchmarks")
            return "/".join(parts[idx + 1 :])
        return str(family)

    if HEATMAP_MANUAL_BENCHMARK_ORDER:
        family_keys = {family: family_key(family) for family in families}
        family_labels = {
            family: (
                buggy_stats.get(family, fixed_stats.get(family, {"label": "?"}))[
                    "label"
                ]
            )
            for family in families
        }

        manual_families = []
        used = set()
        for token in HEATMAP_MANUAL_BENCHMARK_ORDER:
            tok = str(token).strip().lower()
            if not tok:
                continue
            for family in families:
                if family in used:
                    continue
                if (
                    family_keys[family].lower() == tok
                    or family_labels[family].lower() == tok
                ):
                    manual_families.append(family)
                    used.add(family)
                    break

        families = manual_families + [
            family for family in families if family not in used
        ]

    labels = []
    for family in families:
        if family in buggy_stats:
            labels.append(buggy_stats[family]["label"])
        elif family in fixed_stats:
            labels.append(fixed_stats[family]["label"])
        else:
            labels.append("?")

    values = np.full((4, len(families)), np.nan)
    totals = np.zeros((4, len(families)), dtype=float)

    for j, family in enumerate(families):
        buggy = buggy_stats.get(family, {"total": 0, "sash_good": 0, "shell_good": 0})
        fixed = fixed_stats.get(family, {"total": 0, "sash_good": 0, "shell_good": 0})

        if buggy["total"] > 0:
            values[0, j] = buggy["sash_good"] / buggy["total"]
            values[1, j] = buggy["shell_good"] / buggy["total"]
            totals[0, j] = buggy["total"]
            totals[1, j] = buggy["total"]

        if fixed["total"] > 0:
            values[2, j] = fixed["sash_good"] / fixed["total"]
            values[3, j] = fixed["shell_good"] / fixed["total"]
            totals[2, j] = fixed["total"]
            totals[3, j] = fixed["total"]

    # Compact layout: narrow cells and low plot height.
    fig_w = max(9.0, 0.22 * len(families) + 1.6)
    fig, ax = plt.subplots(figsize=(fig_w, 1.5))

    max_total = float(np.nanmax(totals)) if np.any(totals > 0) else 1.0
    rgba = np.ones((4, len(families), 4), dtype=float)
    rgba[:, :, :] = to_rgba("#efefef")
    good_rgb = np.array(to_rgba(color_green))
    bad_rgb = np.array(to_rgba(color_red))

    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            total = totals[i, j]
            if total <= 0:
                continue
            success_ratio = values[i, j]
            base = good_rgb if success_ratio >= 0.999 else bad_rgb
            intensity = 0.25 + 0.75 * (total / max_total)
            rgba[i, j, :3] = base[:3]
            rgba[i, j, 3] = intensity

    ax.imshow(rgba, aspect="auto", interpolation="nearest")
    ax.hlines(
        1.5,
        -0.5,
        len(families) - 0.5,
        colors="black",
        linewidth=1.2,
        zorder=6,
    )

    # Texture buggy rows only for variant regressions:
    # highlight when original had no misses but variants do.
    for j, family in enumerate(families):
        miss = variant_misses.get(family, {"sash_missed": 0, "shell_missed": 0})
        buggy = buggy_stats.get(family, {"total": 0, "sash_good": 0, "shell_good": 0})
        orig_sash_missed = max(buggy["total"] - buggy["sash_good"], 0)
        orig_shell_missed = max(buggy["total"] - buggy["shell_good"], 0)

        if miss["sash_missed"] > 0 and orig_sash_missed == 0:
            ax.add_patch(
                Rectangle(
                    (j - 0.5, -0.5),
                    1.0,
                    1.0,
                    facecolor="none",
                    hatch="////",
                    edgecolor=color_red,
                    linewidth=0.0,
                )
            )
        if miss["shell_missed"] > 0 and orig_shell_missed == 0:
            ax.add_patch(
                Rectangle(
                    (j - 0.5, 0.5),
                    1.0,
                    1.0,
                    facecolor="none",
                    hatch="////",
                    edgecolor=color_red,
                    linewidth=0.0,
                )
            )

    ax.set_xticks(np.arange(len(families)))
    ax.set_xticklabels(labels, rotation=45, ha="center", fontsize=8)
    ax.set_yticks([0, 1, 2, 3])
    ax.set_yticklabels(["", "", "", ""], fontsize=7)
    ax.set_xlabel("Benchmark", fontsize=8)
    ax.tick_params(axis="x", length=0, pad=1)
    ax.tick_params(axis="y", length=0, pad=2)

    # Left group labels (centered on top/bottom row pairs), right row labels.
    y_axis_transform = ax.get_yaxis_transform()
    ax.text(
        -0.015,
        0.5,
        "Buggy",
        transform=y_axis_transform,
        ha="right",
        va="center",
        fontsize=8,
        clip_on=False,
    )
    ax.text(
        -0.015,
        2.5,
        "Fixed",
        transform=y_axis_transform,
        ha="right",
        va="center",
        fontsize=8,
        clip_on=False,
    )
    ax.text(
        1.01,
        0,
        sysname,
        transform=y_axis_transform,
        ha="left",
        va="center",
        fontsize=7,
        clip_on=False,
    )
    ax.text(
        1.01,
        1,
        "ShellCheck",
        transform=y_axis_transform,
        ha="left",
        va="center",
        fontsize=7,
        clip_on=False,
    )
    ax.text(
        1.01,
        2,
        sysname,
        transform=y_axis_transform,
        ha="left",
        va="center",
        fontsize=7,
        clip_on=False,
    )
    ax.text(
        1.01,
        3,
        "ShellCheck",
        transform=y_axis_transform,
        ha="left",
        va="center",
        fontsize=7,
        clip_on=False,
    )

    legend_handles = [
        Line2D(
            [],
            [],
            marker="s",
            linestyle="none",
            markersize=7,
            markerfacecolor=color_green,
            markeredgecolor=color_green,
            label="Good",
        ),
        Line2D(
            [],
            [],
            marker="s",
            linestyle="none",
            markersize=7,
            markerfacecolor=color_red,
            markeredgecolor=color_red,
            label="Bad",
        ),
        Rectangle(
            (0, 0),
            1,
            1,
            facecolor="none",
            hatch="////",
            edgecolor=color_red,
            linewidth=0.0,
            label="Missed on variant",
        ),
    ]
    ax.legend(
        handles=legend_handles, fontsize=7, loc="lower left", frameon=True, ncol=3
    )

    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


def _get_variant_detection_counts(data):
    data_view = data
    if "kind" in data_view.columns:
        data_view = data_view[data_view["kind"] == "buggy_variant"]
    elif "benchmark" in data_view.columns:
        data_view = data_view[
            data_view["benchmark"].astype(str).str.contains("/variants/bug-")
        ]
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
    symexec_timeout, solver_timeout = estimate_runtime_timeouts(data)

    tol = 0.25  # seconds tolerance for numeric jitter
    timeout_line_color = "#C62828"
    for sym_t, sol_t, bar_sym, bar_solver in zip(
        symexec_times, solver_times, bars_sym, bars_solver
    ):
        sym_timed_out = symexec_timeout is not None and sym_t >= (symexec_timeout - tol)
        solver_timed_out = solver_timeout is not None and sol_t >= (
            solver_timeout - tol
        )
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

    plt.xticks(
        x, benchmarks, rotation=45, ha="right", rotation_mode="anchor", fontsize=7
    )
    plt.ylabel("Time (s)")
    plt.yscale("log")
    for xi, depth, sym_t, sol_t in zip(x, depth_labels, symexec_times, solver_times):
        top_h = max(sym_t, sol_t)
        y = top_h * 1.06 if top_h > 0 else 1e-3
        plt.text(xi, y, f"{int(depth)}", ha="center", va="bottom", fontsize=7)

    handles, labels = plt.gca().get_legend_handles_labels()
    handles.append(
        Line2D([], [], color=timeout_line_color, linewidth=1.8, label="Timeout")
    )
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


def plot_coverage(data, output_path):
    plt.figure(figsize=(9, 3))
    data = data.copy()
    data["depth_bfs"] = data.apply(
        lambda row: deepest_bug_depth(
            row["benchmark"], parse_issue_list(row["expected_results"])
        ),
        axis=1,
    )
    data = data.sort_values(by=["depth_bfs", "time"], ascending=[True, True])

    if "ast_coverage_pct" in data.columns:
        coverage = pd.to_numeric(data["ast_coverage_pct"], errors="coerce")
    else:
        coverage = pd.Series(np.nan, index=data.index, dtype=float)
    if coverage.isna().all():
        if "ast_nodes_interpreted" in data.columns:
            interp = pd.to_numeric(data["ast_nodes_interpreted"], errors="coerce")
        else:
            interp = pd.Series(np.nan, index=data.index, dtype=float)
        if "ast_nodes_total" in data.columns:
            total = pd.to_numeric(data["ast_nodes_total"], errors="coerce")
        else:
            total = pd.Series(np.nan, index=data.index, dtype=float)
        coverage = pd.Series(
            np.where(total > 0, (100.0 * interp / total), np.nan), index=data.index
        )
    coverage = coverage.fillna(0.0).clip(lower=0, upper=100)

    benchmarks = data["benchmark"].apply(get_runtime_label)
    depth_labels = data["depth_bfs"]
    x = np.arange(len(data))

    bars = plt.bar(
        x,
        coverage.to_numpy(),
        color=color_scheme[0],
        width=0.65,
        label="AST coverage",
    )
    plt.margins(x=0.02)
    plt.ylim(0, 104)
    plt.yticks([0, 20, 40, 60, 80, 100])

    plt.xticks(
        x, benchmarks, rotation=45, ha="right", rotation_mode="anchor", fontsize=7
    )
    plt.ylabel("Coverage (%)")

    for xi, depth, bar in zip(x, depth_labels, bars):
        y = min(102.0, bar.get_height() + 1.0)
        plt.text(xi, y, f"{int(depth)}", ha="center", va="bottom", fontsize=7)

    handles, labels = plt.gca().get_legend_handles_labels()
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


def estimate_runtime_timeouts(data):
    timeout_rows = (
        data[data["timed_out"] == True]
        if "timed_out" in data.columns
        else data.iloc[0:0]
    )
    if timeout_rows.empty:
        return None, None

    solver_timeout = float(round(timeout_rows["solver_time"].median()))
    long_exec = timeout_rows[timeout_rows["exec_time"] > (solver_timeout * 1.5)][
        "exec_time"
    ]
    if long_exec.empty:
        symexec_timeout = float(round(timeout_rows["exec_time"].median()))
    else:
        symexec_timeout = float(round(long_exec.median() / 10.0) * 10.0)
    return symexec_timeout, solver_timeout


def plot_timeout_sweep_bug_catch(timeout_sweep_dir, output_path):
    series_specs = [
        (
            "no_opts",
            "No opts",
            color_scheme[4],
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_no_opts\.csv$"),
        ),
        (
            "smart_forking",
            "Smart forking",
            color_scheme[3],
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_smart_forking\.csv$"),
        ),
        (
            "solver_opts",
            "Solver opts",
            color_scheme[2],
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_solver_opts\.csv$"),
        ),
        (
            "dfs_on",
            f"Full {sysname}",
            color_scheme[0],
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_on\.csv$"),
        ),
    ]
    series_paths = {
        key: glob.glob(os.path.join(timeout_sweep_dir, f"results_t*_{key}.csv"))
        for key, _, _, _ in series_specs
    }

    if not any(series_paths.values()):
        # Backward-compatible fallback: legacy single-series files.
        series_paths["dfs_on"] = glob.glob(
            os.path.join(timeout_sweep_dir, "results_t*.csv")
        )
        series_specs = [
            (
                "dfs_on",
                f"Full {sysname}",
                color_scheme[0],
                re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)\.csv$"),
            ),
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
            buggy_data = (
                data[data["kind"] == "buggy"].copy() if "kind" in data.columns else data
            )
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

    plt.figure(figsize=(5, 2.15))
    all_x_arrays = []
    all_y_arrays = []
    all_totals = []
    all_timeout_values = []
    for key, label, color, regex in series_specs:
        x_vals, y_vals, totals, timeout_vals = collect_series(
            series_paths.get(key, []), regex
        )
        all_totals.extend(totals)
        all_timeout_values.extend(
            timeout_vals.tolist() if len(timeout_vals) > 0 else []
        )
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
        if all_y_arrays:
            min_seen = float(np.min(np.concatenate(all_y_arrays)))
            y_lower = max(0.0, min_seen - 2.0)
        else:
            y_lower = 0.0
        y_lower_int = int(np.floor(y_lower))
        span = total - y_lower_int
        if span <= 4:
            y_ticks = np.arange(y_lower_int, total + 1, 1, dtype=int)
        else:
            y_ticks = sorted(
                {
                    y_lower_int,
                    *[int(round(v)) for v in np.linspace(y_lower_int, total, 5)],
                    total,
                }
            )
        ax.set_yticks(y_ticks)
        ax.set_ylim(float(y_lower_int), float(total))
        # Keep the truncated y-axis via limits/ticks only.

    plt.xlabel("Timeout (s)")
    plt.ylabel("Bugs caught")
    if all_x_arrays:
        timeout_ticks = sorted({int(round(v)) for v in all_timeout_values})
        rightmost_tick = round(float(np.max(np.concatenate(all_x_arrays))), 2)
        x_ticks = sorted(set(timeout_ticks + [rightmost_tick]))
        timeout_tick_set = set(timeout_ticks)
        x_tick_labels = [
            str(int(x)) if x in timeout_tick_set else f"{x:.2f}" for x in x_ticks
        ]
        plt.xticks(x_ticks, x_tick_labels)
    plt.grid(axis="y", alpha=0.25, linestyle=":")
    plt.legend(fontsize=8, loc="lower right", frameon=True)
    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"1", "true", "t", "yes", "y"}


def plot_koala_timeout_cdf(koala_sweep_dir, output_path):
    all_paths = glob.glob(os.path.join(koala_sweep_dir, "results_t*.csv"))
    timeout_re_dfs_on = re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_on\.csv$")
    timeout_re_plain = re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)\.csv$")

    if not all_paths:
        print(
            f"% No Koala timeout-sweep CSV files found in {koala_sweep_dir}; skipping koala CDF plot",
            file=sys.stderr,
        )
        return

    # Prefer explicit full-SaSh files (_dfs_on) when both naming styles exist.
    selected_by_timeout = {}
    for path in all_paths:
        base = os.path.basename(path)
        m_dfs = timeout_re_dfs_on.search(base)
        if m_dfs:
            timeout_value = float(m_dfs.group(1))
            selected_by_timeout[timeout_value] = path
            continue
        m_plain = timeout_re_plain.search(base)
        if not m_plain:
            continue
        timeout_value = float(m_plain.group(1))
        selected_by_timeout.setdefault(timeout_value, path)

    paths = list(selected_by_timeout.values())

    timeout_vals = []
    complete_counts = []
    total_counts = []

    for path in paths:
        base = os.path.basename(path)
        m_dfs = timeout_re_dfs_on.search(base)
        m_plain = timeout_re_plain.search(base)
        if m_dfs:
            timeout_value = float(m_dfs.group(1))
        elif m_plain:
            timeout_value = float(m_plain.group(1))
        else:
            continue
        data = load_csv(path)

        # Koala sweep files should contain full SaSh results only, but filter defensively.
        if "tool" in data.columns:
            data = data[data["tool"].astype(str).str.lower() == "sash"]

        if data.empty:
            continue

        timed_out = (
            data["timed_out"].map(_as_bool)
            if "timed_out" in data.columns
            else pd.Series([False] * len(data))
        )
        crashed = (
            data["crashed"].map(_as_bool)
            if "crashed" in data.columns
            else pd.Series([False] * len(data))
        )
        complete = (~timed_out) & (~crashed)

        timeout_vals.append(timeout_value)
        complete_counts.append(int(complete.sum()))
        total_counts.append(int(len(data)))

    if not timeout_vals:
        print(
            f"% Koala timeout-sweep files in {koala_sweep_dir} did not match expected naming; skipping koala CDF plot",
            file=sys.stderr,
        )
        return

    order = np.argsort(timeout_vals)
    x = np.array(timeout_vals)[order]
    y = np.array(complete_counts)[order]
    totals_sorted = np.array(total_counts)[order]

    plt.figure(figsize=(5.2, 2.4))
    plt.step(
        x,
        y,
        where="post",
        color=color_scheme[0],
        linewidth=1.8,
        label=f"Full {sysname}",
    )
    plt.plot(x, y, "o", color=color_scheme[0], markersize=4)

    if len(totals_sorted) > 0:
        total_scripts = int(np.median(totals_sorted))
        plt.axhline(
            y=total_scripts,
            linestyle="--",
            linewidth=1.0,
            color="gray",
            label="_nolegend_",
        )
        ax = plt.gca()
        y_ticks = set(ax.get_yticks().tolist())
        y_ticks.add(float(total_scripts))
        ax.set_yticks(sorted(y_ticks))
        ax.set_ylim(
            bottom=max(0.0, float(np.min(y)) - 1.0), top=float(total_scripts) + 1.0
        )

    plt.xlabel("Timeout (s)")
    plt.ylabel("Scripts completely analyzed")
    timeout_ticks = sorted({int(round(v)) for v in x})
    plt.xticks(timeout_ticks, [str(t) for t in timeout_ticks])
    plt.grid(axis="y", alpha=0.25, linestyle=":")
    plt.legend(fontsize=8, loc="lower right", frameon=True)
    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "results_csv", type=str, help="Path to the input CSV file (e.g., results.csv)."
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=".",
        help="Path to the output directory (default: current directory).",
    )
    args = parser.parse_args()
    # Ensure the output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    plt.rcParams.update(
        {
            # "text.usetex": True, # doesnt work in container
            "font.family": "serif",
            # "font.serif": ["Times New Roman"], # doesnt work in container
            "font.size": 12,
        }
    )

    all_results = load_csv(args.results_csv)
    buggy_results = all_results[all_results["kind"] == "buggy"].copy()
    buggy_results["loc"] = buggy_results["benchmark"].apply(get_loc)
    plot_bug_detection_euler(
        buggy_results, os.path.join(args.output_dir, "bug-detection-euler.pdf")
    )
    plot_bug_detection_bars(
        all_results, os.path.join(args.output_dir, "bug-detection-bars.pdf")
    )
    plot_bug_detection_bars_split_versions(
        all_results,
        os.path.join(args.output_dir, "bug-detection-bars-split-versions.pdf"),
    )
    plot_bug_detection_heatmap(
        all_results, os.path.join(args.output_dir, "bug-detection-heatmap.pdf")
    )
    plot_runtime(buggy_results, os.path.join(args.output_dir, "runtime.pdf"))
    # Sweep inputs live next to the main results CSV, not in the figure output dir.
    timeout_sweep_dir = os.path.join(
        os.path.dirname(os.path.abspath(args.results_csv)),
        "timeout-sweep",
    )
    coverage_results = buggy_results
    if not has_coverage_values(coverage_results):
        coverage_results = inject_coverage_from_timeout_sweep(
            coverage_results, timeout_sweep_dir
        )
    plot_coverage(coverage_results, os.path.join(args.output_dir, "coverage.pdf"))
    plot_timeout_sweep_bug_catch(
        timeout_sweep_dir,
        os.path.join(args.output_dir, "timeout-sweep-bugs-caught.pdf"),
    )
    koala_timeout_sweep_dir = os.path.join(
        os.path.dirname(os.path.abspath(args.results_csv)),
        "koala-timeout-sweep",
    )
    plot_koala_timeout_cdf(
        koala_timeout_sweep_dir,
        os.path.join(args.output_dir, "koala-timeout-sweep-cdf.pdf"),
    )

    # Print bug stats
    total_benchmarks = len(buggy_results)
    sash_detected, shellcheck_detected, all_bugs_expected = _get_bug_sets(buggy_results)
    total_bugs = len(all_bugs_expected)
    print(f"% Total benchmarks: {total_benchmarks}", file=sys.stderr)
    print(f"% Total bugs: {total_bugs}", file=sys.stderr)
    print(f"% {sysname} detected bugs: {len(sash_detected)}", file=sys.stderr)
    print(f"% ShellCheck detected bugs: {len(shellcheck_detected)}", file=sys.stderr)
    print(
        f"% Both detected bugs: {len(sash_detected & shellcheck_detected)}",
        file=sys.stderr,
    )
    print(
        f"% Missed bugs: {len(all_bugs_expected - (sash_detected | shellcheck_detected))}",
        file=sys.stderr,
    )
    fixed_total, sash_success, shell_success = _get_fixed_fp_counts(all_results)
    print(f"% Fixed bug instances: {fixed_total}", file=sys.stderr)
    print(f"% {sysname} fixed no-FP (bug-level): {sash_success}", file=sys.stderr)
    print(f"% ShellCheck fixed no-FP (bug-level): {shell_success}", file=sys.stderr)
    variant_total, variant_sash_detected, variant_shell_detected = (
        _get_variant_detection_counts(all_results)
    )
    print(f"% Variant bug instances: {variant_total}", file=sys.stderr)
    print(
        f"% {sysname} variants detected bugs: {variant_sash_detected}", file=sys.stderr
    )
    print(
        f"% ShellCheck variants detected bugs: {variant_shell_detected}",
        file=sys.stderr,
    )
    print(
        f"% {sysname} variants missed bugs: {variant_total - variant_sash_detected}",
        file=sys.stderr,
    )
    print(
        f"% ShellCheck variants missed bugs: {variant_total - variant_shell_detected}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
