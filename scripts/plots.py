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
import bugdepth

import matplotlib.pyplot as plt
from matplotlib_set_diagrams import EulerDiagram
from matplotlib.lines import Line2D
from matplotlib.colors import to_rgba
from matplotlib.patches import ConnectionPatch, Polygon, Rectangle


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
    return [i for i in issues if extract_issue_code(i) not in EXCLUDED_BUG_CODES]


BUG_LINE_RE = re.compile(r"^L([0-9]+):")
_depth_metrics_cache = {}
_lukas_program_cache = {}
_lukas_line_depth_cache = {}


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


def get_lukas_program(path):
    script_path = resolve_benchmark_path(path)
    script_key = str(script_path)
    if script_key not in _lukas_program_cache:
        try:
            _lukas_program_cache[script_key] = bugdepth.parser.parse_shell_script(
                script_key
            )
        except Exception:
            _lukas_program_cache[script_key] = []
    return _lukas_program_cache[script_key]


def lukas_depth_at_line(path, line_number):
    script_path = resolve_benchmark_path(path)
    cache_key = (str(script_path), int(line_number))
    if cache_key not in _lukas_line_depth_cache:
        depth_value = 0
        try:
            program = get_lukas_program(path)
            if program:
                depth_value = bugdepth.count_conds(program, int(line_number), verbose=False)
        except Exception:
            depth_value = 0
        _lukas_line_depth_cache[cache_key] = depth_value
    return _lukas_line_depth_cache[cache_key]


def deepest_bug_lukas_depth(path, expected_issues):
    bug_lines = parse_issue_lines(expected_issues)
    if not bug_lines:
        return 0
    return max((lukas_depth_at_line(path, line) for line in bug_lines), default=0)


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
EXCLUDED_BUG_CODES = OOS_CODES

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
        if code and shellcheck and code not in EXCLUDED_BUG_CODES:
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
        if not code or code in EXCLUDED_BUG_CODES:
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
        if not shellcheck_code or not code or code in EXCLUDED_BUG_CODES:
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
# Use a high-contrast Matplotlib palette across all plots.
color_scheme = plt.get_cmap("Set1").colors
# Semantic colors for good/bad outcomes.
color_green = "#2ca02c"
color_red = "#d62728"
# Optional manual prefix order for heatmap columns.
# Fill with short benchmark IDs (e.g., "njv") and/or benchmark keys
# (e.g., "high_profile/c02-n"). Remaining benchmarks keep auto-sort order.
HEATMAP_MANUAL_BENCHMARK_ORDER = []


def _format_coverage_tick(t: float) -> str:
    pct = 100.0 * float(t)
    if np.isclose(pct, round(pct)):
        return f"{int(round(pct))}%"
    return f"{pct:.1f}%"


def _lighten_color(color, amount):
    """
    Blend a color toward white.
    amount=0 keeps the original color, amount=1 yields white.
    """
    r, g, b, a = to_rgba(color)
    amount = min(max(float(amount), 0.0), 1.0)
    return (
        r + (1.0 - r) * amount,
        g + (1.0 - g) * amount,
        b + (1.0 - b) * amount,
        a,
    )


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
    variants_good = [len(variants_sash), len(variants_shell)]

    fixed_good = [fixed_sash_no_fp, fixed_shell_no_fp]
    (
        fixed_shell_no_fp_from_orig_caught,
        fixed_shell_no_fp_from_orig_missed,
    ) = _get_fixed_shell_no_fp_split_by_buggy_detection(data)
    split_total = (
        fixed_shell_no_fp_from_orig_caught + fixed_shell_no_fp_from_orig_missed
    )
    # Keep bar height exactly aligned with the fixed ShellCheck total even if
    # metadata mismatches create tiny accounting differences.
    if split_total < fixed_good[1]:
        fixed_shell_no_fp_from_orig_missed += fixed_good[1] - split_total
    elif split_total > fixed_good[1]:
        overflow = split_total - fixed_good[1]
        trim_missed = min(overflow, fixed_shell_no_fp_from_orig_missed)
        fixed_shell_no_fp_from_orig_missed -= trim_missed
        overflow -= trim_missed
        if overflow > 0:
            fixed_shell_no_fp_from_orig_caught = max(
                fixed_shell_no_fp_from_orig_caught - overflow, 0
            )

    # Two-panel layout: Buggy/Variants (left) and Fixed (right).
    fig, (ax_left, ax_right) = plt.subplots(
        1,
        2,
        figsize=(6.4, 2.8),
        sharey=True,
        gridspec_kw={"width_ratios": [1.3, 1.0]},
    )
    width = 0.16
    # Keep a small gap between SaSh/ShellCheck bars to avoid visual overlap.
    bar_offset = width * 0.58

    # Use neutral pastel system colors (avoid good/bad red/green semantics).
    sash_color = color_green
    shellcheck_color = color_scheme[3]
    shellcheck_lighter = to_rgba(shellcheck_color, alpha=0.45)

    left_categories = ["Real Bugs", "Variants"]
    # Keep original/variant groups visually separated.
    left_x = np.array([0.0, 1.22], dtype=float)
    left_sash = np.array([buggy_good[0], variants_good[0]], dtype=float)
    left_shell = np.array([buggy_good[1], variants_good[1]], dtype=float)

    ax_left.bar(
        left_x - bar_offset,
        left_sash,
        width=width,
        color=sash_color,
        edgecolor=sash_color,
        linewidth=0.0,
    )
    ax_left.bar(
        left_x + bar_offset,
        left_shell,
        width=width,
        color=shellcheck_color,
        edgecolor=shellcheck_color,
        linewidth=0.0,
    )

    # Visual reference: carry buggy ShellCheck catches over to the variants slot.
    ax_left.hlines(
        y=left_shell[0] + 0.5,
        xmin=left_x[0] + bar_offset - width / 2,
        xmax=left_x[1] + bar_offset - 3 * width / 5,
        colors="black",
        linestyles=":",
        linewidth=0.7,
        alpha=0.9,
    )

    right_categories = ["Real Fixes"]
    right_x = np.array([0.0], dtype=float)
    right_sash = np.array([fixed_good[0]], dtype=float)
    right_shell = np.array([fixed_good[1]], dtype=float)

    ax_right.bar(
        right_x - bar_offset,
        right_sash,
        width=width,
        color=sash_color,
        edgecolor=sash_color,
        linewidth=0.0,
    )
    ax_right.bar(
        right_x + bar_offset,
        np.array([fixed_shell_no_fp_from_orig_caught], dtype=float),
        width=width,
        color=shellcheck_color,
        edgecolor="none",
        linewidth=0.0,
    )
    ax_right.bar(
        right_x + bar_offset,
        np.array([fixed_shell_no_fp_from_orig_missed], dtype=float),
        width=width,
        bottom=np.array([fixed_shell_no_fp_from_orig_caught], dtype=float),
        color=shellcheck_lighter,
        edgecolor="none",
        linewidth=0.0,
    )

    max_good = max(
        float(np.max(left_sash)),
        float(np.max(left_shell)),
        float(np.max(right_sash)),
        float(np.max(right_shell)),
        1.0,
    )
    top_pad = max(3, int(np.ceil(max_good * 0.10)))
    y_max = max(max_good + top_pad, 125 + top_pad)
    y_ticks = list(range(0, int(y_max) + 1, 25))
    for ax in (ax_left, ax_right):
        ax.set_ylim(0, y_max)
        ax.set_yticks(y_ticks)
        ax.axhline(
            y=float(buggy_total),
            color="black",
            linewidth=0.5,
            linestyle="--",
            alpha=0.18,
            zorder=0,
        )
        ax.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.35)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", labelsize=14)

    ax_left.set_xticks(left_x)
    ax_left.set_xticklabels(left_categories, fontsize=14)
    ax_right.set_xticks(right_x)
    ax_right.set_xticklabels(right_categories, fontsize=14)
    # Match visual bar thickness across panels by keeping data-unit scales
    # proportional to the subplot width ratio (left:right = 1.3:1.0).
    left_xlim = (-0.35, 1.52)
    left_span = left_xlim[1] - left_xlim[0]
    width_ratio_left = 1.3
    width_ratio_right = 1.0
    right_span = left_span * (width_ratio_right / width_ratio_left)
    ax_left.set_xlim(*left_xlim)
    ax_right.set_xlim(-right_span / 2.0, right_span / 2.0)

    ax_left.set_ylabel("Bugs identified", fontsize=14, labelpad=8)
    ax_left.tick_params(
        axis="y",
        labelsize=14,
        left=True,
        labelleft=True,
        right=False,
        labelright=False,
        pad=1,
    )
    ax_right.tick_params(
        axis="y",
        left=True,
        labelleft=True,
        right=False,
        labelright=False,
        labelsize=14,
        pad=1,
    )
    # Keep same label-to-tick gap as the left subplot.
    ax_right.set_ylabel("True negatives", fontsize=14, labelpad=8)
    ax_right.yaxis.set_label_position("left")

    label_offset = max(0.4, y_max * 0.012)
    for xi, total in zip(left_x - bar_offset, left_sash):
        ax_left.text(
            xi,
            total + label_offset,
            f"{int(total)}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    for xi, total in zip(left_x + bar_offset, left_shell):
        ax_left.text(
            xi,
            total + label_offset,
            f"{int(total)}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    for xi, total in zip(right_x - bar_offset, right_sash):
        ax_right.text(
            xi,
            total + label_offset,
            f"{int(total)}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    for xi, total in zip(right_x + bar_offset, right_shell):
        ax_right.text(
            xi,
            total + label_offset,
            f"{int(total)}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    # Extra fixed ShellCheck label: true negatives on issues ShellCheck
    # actually flags on original buggy scripts (exclude "says nothing" cases).
    if len(right_x) == 1:
        ax_right.text(
            right_x[0] + bar_offset,
            fixed_shell_no_fp_from_orig_caught + label_offset,
            f"{int(fixed_shell_no_fp_from_orig_caught)}",
            ha="center",
            va="bottom",
            fontsize=7,
            color="black",
        )
    # Bars encode good-instance counts for each system.
    side_handles = [
        Rectangle((0, 0), 1, 1, facecolor=sash_color, edgecolor=sash_color, label="SaSh"),
        Rectangle(
            (0, 0),
            1,
            1,
            facecolor=shellcheck_color,
            edgecolor=shellcheck_color,
            label="ShellCheck",
        ),
        Line2D(
            [],
            [],
            color="black",
            linewidth=0.5,
            linestyle="--",
            alpha=0.18,
            label=f"Total bugs ({buggy_total})"
        ),
    ]
    fig.legend(
        handles=side_handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.08),
        ncol=3,
        frameon=False,
        fontsize=14,
        handlelength=1.2,
        handletextpad=0.6,
        borderaxespad=0.0,
        columnspacing=2.0,
    )
    fig.subplots_adjust(left=0.18, right=0.90, bottom=0.36, top=0.92, wspace=0.84)
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


def _get_fixed_shell_no_fp_split_by_buggy_detection(data):
    """
    Split fixed ShellCheck true negatives into two groups:
    1) corresponding buggy issue was detected by ShellCheck
    2) corresponding buggy issue was not detected by ShellCheck
    """
    buggy_rows = _benchmark_kind_rows(data, "buggy")
    fixed_rows = _benchmark_kind_rows(data, "fixed")

    buggy_shell_detected = set()
    for _, row in buggy_rows.iterrows():
        family = benchmark_group_key(row["benchmark"])
        kind = str(row.get("kind", "buggy"))
        expected_counter = Counter(parse_issue_list(row["expected_results"]))
        shell_counter = get_info_shellcheck_expected_counter(
            row["benchmark"], kind, fallback_to_bug_default=True
        )
        for issue, expected_count in expected_counter.items():
            matched = min(expected_count, shell_counter[issue])
            for idx in range(matched):
                buggy_shell_detected.add((family, issue, idx))

    no_fp_from_orig_caught = 0
    no_fp_from_orig_missed = 0
    for _, row in fixed_rows.iterrows():
        family = benchmark_group_key(row["benchmark"])
        kind = str(row.get("kind", "fixed"))
        expected_counter = Counter(parse_issue_list(row["expected_results"]))
        shell_counter = get_info_shellcheck_expected_counter(
            row["benchmark"], kind, fallback_to_bug_default=True
        )
        for issue, expected_count in expected_counter.items():
            fp_matched = min(expected_count, shell_counter[issue])
            for idx in range(expected_count):
                # fixed no-FP for this issue instance
                if idx >= fp_matched:
                    if (family, issue, idx) in buggy_shell_detected:
                        no_fp_from_orig_caught += 1
                    else:
                        no_fp_from_orig_missed += 1

    return no_fp_from_orig_caught, no_fp_from_orig_missed


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
    plt.legend(
        handles,
        labels,
        fontsize=8,
        loc="lower left",
        frameon=True,
        ncol=1,
    )
    plt.subplots_adjust(bottom=0.30)
    plt.tight_layout()
    plt.savefig(output_path, format="pdf")
    plt.close()


def plot_coverage(data, output_path):
    plt.figure(figsize=(14, 1.5))
    data = data.copy()
    data["depth_lukas"] = data.apply(
        lambda row: deepest_bug_lukas_depth(
            row["benchmark"], parse_issue_list(row["expected_results"])
        ),
        axis=1,
    )
    data = data.sort_values(by=["depth_lukas", "time"], ascending=[True, True])

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
    coverage = (coverage.fillna(0.0).clip(lower=0, upper=100)) / 100.0

    benchmarks = data["benchmark"].apply(short_name)
    depth_labels = data["depth_lukas"]
    x = np.arange(len(data))

    bars = plt.bar(
        x,
        coverage.to_numpy(),
        color=color_scheme[0],
        width=0.65,
        label="AST coverage",
    )
    plt.margins(x=0.005)
    plt.ylim(0, 1.40)
    y_ticks = [0.0, 0.25, 0.5, 0.75, 1.0]
    y_tick_labels = [_format_coverage_tick(t) for t in y_ticks]
    plt.yticks(y_ticks, y_tick_labels, fontsize=8)

    plt.xticks(
        x, benchmarks, rotation=45, ha="right", rotation_mode="anchor", fontsize=8
    )
    ax = plt.gca()
    ax.set_xlabel("Benchmarks")
    ax.xaxis.set_label_coords(0.5, -0.54)
    plt.ylabel("Coverage")
    plt.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    for xi, depth in zip(x, depth_labels):
        ax.text(
            xi,
            -0.37,
            f"{int(depth)}",
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="top",
            fontsize=7,
            clip_on=False,
        )
    ax.text(
        0.0,
        -0.37,
        r"Bug depth:",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=8,
        clip_on=False,
    )

    handles, labels = plt.gca().get_legend_handles_labels()
    handles = handles[::-1]
    labels = labels[::-1]
    fig = plt.gcf()
    fig.legend(
        handles,
        labels,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.00),
        frameon=False,
        ncol=1,
    )
    plt.subplots_adjust(left=0.11, right=0.98, bottom=0.43)
    plt.tight_layout(rect=(0, 0.08, 1, 1))
    plt.savefig(output_path, format="pdf")
    plt.close()


def _coverage_map_from_results(data):
    data_view = data.copy()
    if "kind" in data_view.columns:
        data_view = data_view[data_view["kind"] == "buggy"].copy()

    if "benchmark" not in data_view.columns:
        return {}

    if "ast_coverage_pct" in data_view.columns:
        coverage = pd.to_numeric(data_view["ast_coverage_pct"], errors="coerce")
    else:
        coverage = pd.Series(np.nan, index=data_view.index, dtype=float)

    if coverage.isna().all():
        if "ast_nodes_interpreted" in data_view.columns:
            interp = pd.to_numeric(data_view["ast_nodes_interpreted"], errors="coerce")
        else:
            interp = pd.Series(np.nan, index=data_view.index, dtype=float)
        if "ast_nodes_total" in data_view.columns:
            total = pd.to_numeric(data_view["ast_nodes_total"], errors="coerce")
        else:
            total = pd.Series(np.nan, index=data_view.index, dtype=float)
        coverage = pd.Series(
            np.where(total > 0, (100.0 * interp / total), np.nan), index=data_view.index
        )

    coverage = coverage.clip(lower=0, upper=100)
    keys = data_view["benchmark"].astype(str).apply(benchmark_key)
    out = {}
    for k, v in zip(keys, coverage):
        if pd.notna(v):
            out[k] = float(v)
    return out


def coverage_full_across_timeout_sweep(timeout_sweep_dir):
    """
    Return (full_count, comparable_count, csv_count) for buggy benchmarks that
    have 100% AST coverage in every timeout/configuration CSV.
    """
    paths = sorted(glob.glob(os.path.join(timeout_sweep_dir, "results_t*.csv")))
    if not paths:
        return None

    coverage_maps = []
    for path in paths:
        data = load_csv(path)
        if "kind" in data.columns:
            data = data[data["kind"] == "buggy"].copy()
        cov_map = _coverage_map_from_results(data)
        if cov_map:
            coverage_maps.append(cov_map)

    if not coverage_maps:
        return None

    comparable_keys = set(coverage_maps[0].keys())
    for cov_map in coverage_maps[1:]:
        comparable_keys &= set(cov_map.keys())

    if not comparable_keys:
        return 0, 0, len(coverage_maps)

    full_count = 0
    for bench_key in comparable_keys:
        if all(
            np.isclose(cov_map[bench_key], 100.0, atol=1e-9)
            for cov_map in coverage_maps
        ):
            full_count += 1

    return full_count, len(comparable_keys), len(coverage_maps)


def bugs_caught_across_timeout_sweep(timeout_sweep_dir):
    """
    Return (caught_all_count, comparable_bug_count, csv_count) for buggy bug
    instances that are detected in every timeout/configuration CSV.
    """
    paths = sorted(glob.glob(os.path.join(timeout_sweep_dir, "results_t*.csv")))
    if not paths:
        return None

    expected_sets = []
    detected_sets = []
    for path in paths:
        data = load_csv(path)
        if "kind" in data.columns:
            data = data[data["kind"] == "buggy"].copy()

        expected_set = set()
        detected_set = set()
        for _, row in data.iterrows():
            bench_key = benchmark_key(row["benchmark"])
            expected_counter = Counter(parse_issue_list(row["expected_results"]))
            actual_counter = Counter(parse_issue_list(row["actual_results"]))
            for issue, count in expected_counter.items():
                for idx in range(count):
                    key = f"{bench_key}_{issue}_{idx}"
                    expected_set.add(key)
                    if idx < min(count, actual_counter[issue]):
                        detected_set.add(key)

        if expected_set:
            expected_sets.append(expected_set)
            detected_sets.append(detected_set)

    if not expected_sets:
        return None

    comparable_expected = set(expected_sets[0])
    for s in expected_sets[1:]:
        comparable_expected &= s

    if not comparable_expected:
        return 0, 0, len(expected_sets)

    caught_all = set(detected_sets[0])
    for s in detected_sets[1:]:
        caught_all &= s
    caught_all &= comparable_expected

    return len(caught_all), len(comparable_expected), len(expected_sets)


def plot_coverage_by_config(timeout_sweep_dir, base_buggy_data, output_path):
    dfs_on_color = color_green
    smart_forking_color = _lighten_color(dfs_on_color, 0.22)
    no_opts_color = _lighten_color(dfs_on_color, 0.45)
    series_specs = [
        (
            "no_opts",
            f"{sysname} w/o optimisations",
            no_opts_color,
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_no_opts\.csv$"),
        ),
        (
            "smart_forking",
            f"{sysname} w/o effect-aware exploration",
            smart_forking_color,
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_smart_forking\.csv$"),
        ),
        (
            "dfs_on",
            f"{sysname}",
            dfs_on_color,
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_on\.csv$"),
        ),
    ]

    timeout_maps = {}
    for key, _, _, regex in series_specs:
        matches = {}
        for path in glob.glob(os.path.join(timeout_sweep_dir, f"results_t*_{key}.csv")):
            m = regex.search(os.path.basename(path))
            if not m:
                continue
            matches[float(m.group(1))] = path
        if key == "dfs_on" and not matches:
            legacy_re = re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)\.csv$")
            for path in glob.glob(os.path.join(timeout_sweep_dir, "results_t*.csv")):
                m = legacy_re.search(os.path.basename(path))
                if not m:
                    continue
                matches[float(m.group(1))] = path
        timeout_maps[key] = matches

    available_specs = [spec for spec in series_specs if timeout_maps.get(spec[0])]
    if not available_specs:
        return False

    timeout_sets = [set(timeout_maps[key].keys()) for key, _, _, _ in available_specs]
    common_timeouts = set.intersection(*timeout_sets) if timeout_sets else set()
    if common_timeouts:
        preferred_timeout = 60.0
        chosen_timeout = (
            preferred_timeout if preferred_timeout in common_timeouts else max(common_timeouts)
        )
        chosen_specs = [
            spec
            for spec in available_specs
            if chosen_timeout in timeout_maps.get(spec[0], {})
        ]
        chosen_paths = {
            key: timeout_maps[key][chosen_timeout] for key, _, _, _ in chosen_specs
        }
    else:
        chosen_specs = available_specs
        chosen_paths = {
            key: timeout_maps[key][max(timeout_maps[key].keys())]
            for key, _, _, _ in chosen_specs
        }

    data = base_buggy_data.copy()
    data["depth_lukas"] = data.apply(
        lambda row: deepest_bug_lukas_depth(
            row["benchmark"], parse_issue_list(row["expected_results"])
        ),
        axis=1,
    )
    data = data.sort_values(by=["depth_lukas", "time"], ascending=[True, True])
    bench_keys = data["benchmark"].astype(str).apply(benchmark_key).to_numpy()
    bench_labels = data["benchmark"].apply(short_name).to_numpy()
    depth_labels = data["depth_lukas"].to_numpy()

    x = np.arange(len(data))
    n_series = max(1, len(chosen_specs))
    group_width = 0.84
    bar_width = group_width / n_series

    fig = plt.figure(figsize=(10, 2))
    gs = fig.add_gridspec(nrows=2, ncols=1, height_ratios=[0.22, 2.68], hspace=0.42)
    ax_top = fig.add_subplot(gs[0, 0])
    ax_main = fig.add_subplot(gs[1, 0])
    plt.sca(ax_main)
    series_y = {}
    series_present = {}
    for key, _, _, _ in chosen_specs:
        series_df = load_csv(chosen_paths[key])
        cov_map = _coverage_map_from_results(series_df)
        y_raw = np.array([cov_map.get(k, np.nan) for k in bench_keys], dtype=float)
        series_present[key] = ~np.isnan(y_raw)
        y_vals = np.clip(y_raw / 100.0, 0.0, 1.0)
        series_y[key] = y_vals

    # Keep only benchmarks available in all selected timeout-sweep configurations.
    present_all = np.ones(len(bench_keys), dtype=bool)
    for mask in series_present.values():
        present_all &= mask
    if not present_all.any():
        return False

    bench_keys = bench_keys[present_all]
    bench_labels = bench_labels[present_all]
    depth_labels = depth_labels[present_all]
    for key in list(series_y.keys()):
        series_y[key] = series_y[key][present_all]

    bundle_mask = np.ones(len(bench_keys), dtype=bool)
    for y_vals in series_y.values():
        bundle_mask &= np.isclose(y_vals, 1.0, atol=1e-9)
    keep_idx = np.where(~bundle_mask)[0]

    plot_labels = [bench_labels[i] for i in keep_idx]
    depth_texts = [f"{int(depth_labels[i])}" for i in keep_idx]

    # Summary bar: fully covered in all selected configurations.
    fully_covered_count = int(np.sum(bundle_mask))
    comparable_total = int(len(bundle_mask))
    top_bar_height = 0.03
    avg_cfg_color = "#c8e6c9"  # light green fill for the covered segment
    axis_linewidth = float(plt.rcParams.get("axes.linewidth", 1.0))
    empty_bar_color = "#eceff1"
    total_bar_width = max(comparable_total, 1)
    empty_width = max(total_bar_width - fully_covered_count, 0)
    if fully_covered_count > 0:
        ax_top.barh(
            [0],
            [fully_covered_count],
            left=[0],
            color=avg_cfg_color,
            height=top_bar_height,
            edgecolor="none",
            linewidth=0.0,
        )
    if empty_width > 0:
        ax_top.barh(
            [0],
            [empty_width],
            left=[fully_covered_count],
            color=empty_bar_color,
            height=top_bar_height,
            edgecolor="none",
            linewidth=0.0,
        )
    ax_top.add_patch(
        Rectangle(
            (0.0, -top_bar_height / 2.0),
            total_bar_width,
            top_bar_height,
            facecolor="none",
            edgecolor="black",
            linewidth=axis_linewidth,
            antialiased=False,
            joinstyle="miter",
            zorder=3,
        )
    )
    current_top_bar_pad = max(total_bar_width * 0.06, 0.6)
    current_span = total_bar_width + (2.0 * current_top_bar_pad)
    # Make the bar appear at 3/4 of its previous visual width.
    target_span = current_span / 0.75
    top_bar_pad = (target_span - total_bar_width) / 2.0
    ax_top.set_xlim(-top_bar_pad, total_bar_width + top_bar_pad)
    ax_top.set_xticks([])
    ax_top.set_yticks([])
    for spine in ax_top.spines.values():
        spine.set_visible(False)
    ax_top.text(
        0.5,
        1.22,
        f"100% coverage under all configurations ({fully_covered_count}/{comparable_total})",
        transform=ax_top.transAxes,
        ha="center",
        va="bottom",
        fontsize=8,
    )
    connector_left = ConnectionPatch(
        xyA=(fully_covered_count, -top_bar_height / 2.0),
        coordsA=ax_top.transData,
        xyB=(0.0, 1.0),
        coordsB=ax_main.transAxes,
        axesA=ax_top,
        axesB=ax_main,
        color="black",
        linewidth=axis_linewidth,
        linestyle="--",
    )
    connector_right = ConnectionPatch(
        xyA=(max(comparable_total, 1), -top_bar_height / 2.0),
        coordsA=ax_top.transData,
        xyB=(1.0, 1.0),
        coordsB=ax_main.transAxes,
        axesA=ax_top,
        axesB=ax_main,
        color="black",
        linewidth=axis_linewidth,
        linestyle="--",
    )

    x = np.arange(len(plot_labels))
    all_y = []
    for i, (key, label, color, _) in enumerate(chosen_specs):
        y_vals = series_y[key]
        y_plot = y_vals[keep_idx]
        all_y.append(y_plot)
        offset = (i - (n_series - 1) / 2.0) * bar_width
        if len(y_plot) > 0:
            plt.bar(
                x + offset,
                y_plot,
                width=bar_width * 0.95,
                color=color,
                label=label,
            )

    plt.margins(x=0.005)
    plt.ylim(0, 1.06)
    y_ticks = [0.0, 0.25, 0.5, 0.75, 1.0]
    y_tick_labels = [_format_coverage_tick(t) for t in y_ticks]
    plt.yticks(y_ticks, y_tick_labels, fontsize=7)
    plt.xticks(
        x, plot_labels, rotation=45, ha="right", rotation_mode="anchor", fontsize=7
    )
    ax = plt.gca()
    ax.set_xlabel("Benchmarks")
    ax.xaxis.set_label_coords(0.5, -0.54)
    plt.ylabel("Coverage")
    plt.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.25)

    if len(plot_labels) == 0:
        ax.text(
            0.5,
            0.5,
            "All comparable benchmarks are fully explored\nin all configurations",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=8,
        )
    else:
        for xi, depth_text in zip(x, depth_texts):
            ax.text(
                xi,
                -0.37,
                depth_text,
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=7,
                clip_on=False,
            )
        ax.text(
            0.0,
            -0.37,
            r"Bug depth:",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8,
            clip_on=False,
        )

    handles, labels = plt.gca().get_legend_handles_labels()
    handles = handles[::-1]
    labels = labels[::-1]
    fig.legend(
        handles,
        labels,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.00),
        frameon=False,
        ncol=max(1, min(3, len(labels))),
    )
    fig.subplots_adjust(left=0.11, right=0.98, bottom=0.43, top=0.92, hspace=0.42)

    # Build connector fill after final subplot layout so coordinates are exact.
    top_left_disp = ax_top.transData.transform(
        (fully_covered_count, -top_bar_height / 2.0)
    )
    top_right_disp = ax_top.transData.transform(
        (max(comparable_total, 1), -top_bar_height / 2.0)
    )
    top_left_fig = fig.transFigure.inverted().transform(top_left_disp)
    top_right_fig = fig.transFigure.inverted().transform(top_right_disp)
    bottom_left_fig = fig.transFigure.inverted().transform(
        ax_main.transAxes.transform((0.0, 1.0))
    )
    bottom_right_fig = fig.transFigure.inverted().transform(
        ax_main.transAxes.transform((1.0, 1.0))
    )
    connector_fill_color = "#f5f7f9"  # keep connector patch subtle/light
    connector_fill = Polygon(
        [top_left_fig, top_right_fig, bottom_right_fig, bottom_left_fig],
        closed=True,
        transform=fig.transFigure,
        facecolor=connector_fill_color,
        edgecolor="none",
        alpha=1.0,
        zorder=0.2,
    )
    fig.add_artist(connector_fill)
    fig.add_artist(connector_left)
    fig.add_artist(connector_right)
    plt.savefig(output_path, format="pdf")
    plt.close()
    return True


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
    dfs_on_color = color_green
    smart_forking_color = _lighten_color(dfs_on_color, 0.22)
    no_opts_color = _lighten_color(dfs_on_color, 0.45)
    series_specs = [
        (
            "no_opts",
            f"{sysname} w/o optimisations",
            no_opts_color,
            "o",
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_no_opts\.csv$"),
        ),
        (
            "smart_forking",
            f"{sysname} w/o effect-aware exploration",
            smart_forking_color,
            "^",
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_smart_forking\.csv$"),
        ),
        (
            "dfs_on",
            f"{sysname}",
            dfs_on_color,
            "s",
            re.compile(r"results_t([0-9]+(?:\.[0-9]+)?)_dfs_on\.csv$"),
        ),
    ]
    series_paths = {
        key: glob.glob(os.path.join(timeout_sweep_dir, f"results_t*_{key}.csv"))
        for key, _, _, _, _ in series_specs
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
                dfs_on_color,
                "o",
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
            timeout_value = float(match.group(1))
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

    plt.figure(figsize=(5.8, 2.0))
    all_x_arrays = []
    all_y_arrays = []
    all_totals = []
    all_timeout_values = []
    for key, label, color, marker, regex in series_specs:
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
            marker=marker,
            color=color,
            linewidth=1.8,
            markersize=4,
            markerfacecolor=color,
            markeredgecolor=color,
            markeredgewidth=0.8,
            label=label,
        )

    if all_totals:
        total = int(round(float(np.median(all_totals))))
        ax = plt.gca()
        ax.axhline(
            y=float(total),
            color="black",
            linewidth=0.5,
            linestyle="--",
            alpha=0.18,
            zorder=0,
        )
        if all_y_arrays:
            min_seen = float(np.min(np.concatenate(all_y_arrays)))
            y_lower = max(0.0, min_seen - 2.0)
        else:
            y_lower = 0.0
        y_lower_int = int(np.floor(y_lower))
        # Use a fixed truncated baseline for readability.
        y_bottom = 70.0
        y_top = 120.0
        ax.set_ylim(y_bottom, y_top)
        y_tick_start = int(np.floor(y_bottom / 10.0) * 10 + 10)
        y_ticks = [t for t in range(y_tick_start, int(y_top) + 1, 10)]
        if not y_ticks:
            y_ticks = [int(total)]
        ax.set_yticks(y_ticks)
        # Standard broken-axis directive: // marker on the left y-axis,
        # positioned between the axis bottom and the first visible tick.
        y_break = 75.0
        x_center = 0.0  # left axis spine in y-axis transform coordinates
        dx = 0.010      # x in axes fraction
        dy = 0.07       # y in data units
        gap = 0.70      # spacing between the two slashes (data units)
        break_kwargs = dict(
            transform=ax.get_yaxis_transform(),
            color="black",
            clip_on=False,
            linewidth=0.6,
            zorder=6,
        )
        ax.plot(
            (x_center - dx, x_center + dx),
            (y_break - dy, y_break + dy),
            **break_kwargs,
        )
        ax.plot(
            (x_center - dx, x_center + dx),
            (y_break + gap - dy, y_break + gap + dy),
            **break_kwargs,
        )
        # Mask the axis segment between slashes to emphasize the break.
        ax.plot(
            (x_center, x_center),
            (y_break + dy + 0.03, y_break + gap - dy - 0.03),
            transform=ax.get_yaxis_transform(),
            color="white",
            linewidth=2.2,
            solid_capstyle="butt",
            clip_on=False,
            zorder=5,
        )

    plt.xlabel("Timeout (s)")
    plt.ylabel("Bugs caught")
    ax = plt.gca()
    axis_label_size = max(ax.xaxis.label.get_size(), ax.yaxis.label.get_size())
    if all_x_arrays:
        timeout_ticks = sorted({int(round(v)) for v in all_timeout_values})
        x_ticks = [t for t in timeout_ticks if t == 1 or t % 10 == 0]
        if not x_ticks:
            x_ticks = timeout_ticks
        plt.xticks(x_ticks, [str(t) for t in x_ticks], fontsize=axis_label_size)
    plt.yticks(fontsize=axis_label_size)
    plt.grid(axis="y", alpha=0.25, linestyle=":")
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) > 1:
        handles = handles[::-1]
        labels = labels[::-1]
    if all_totals:
        handles.append(
            Line2D(
                [],
                [],
                color="black",
                linewidth=0.5,
                linestyle="--",
                alpha=0.18,
                label=f"Total bugs ({total})",
            )
        )
        labels.append(f"Total bugs ({total})")
    legend_size = max(8, axis_label_size - 2)
    plt.legend(
        handles,
        labels,
        fontsize=legend_size,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.34),
        frameon=False,
        ncol=2,
    )
    plt.subplots_adjust(bottom=0.42)
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
    excluded_script_names = {
        "clean.sh",
        "execute.sh",
        "fetch.sh",
        "install.sh",
        "validate.sh",
    }
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

    if not selected_by_timeout:
        print(
            f"% Koala timeout-sweep files in {koala_sweep_dir} did not match expected naming; skipping koala CDF plot",
            file=sys.stderr,
        )
        return

    max_timeout = max(selected_by_timeout.keys())
    effective_timeout = float(max_timeout)
    path = selected_by_timeout[max_timeout]
    data = load_csv(path)

    # Koala sweep files should contain full SaSh results only, but filter defensively.
    if "tool" in data.columns:
        data = data[data["tool"].astype(str).str.lower() == "sash"]
    if "benchmark" in data.columns:
        benchmark_names = data["benchmark"].astype(str).map(lambda p: Path(p).name)
        data = data[~benchmark_names.isin(excluded_script_names)].copy()

    if data.empty:
        print(
            f"% Koala timeout CDF input {path} has no SaSh rows; skipping koala CDF plot",
            file=sys.stderr,
        )
        return

    csv_timed_out = (
        data["timed_out"].map(_as_bool)
        if "timed_out" in data.columns
        else pd.Series([False] * len(data), index=data.index)
    )
    crashed = (
        data["crashed"].map(_as_bool)
        if "crashed" in data.columns
        else pd.Series([False] * len(data), index=data.index)
    )
    if "time" in data.columns:
        total_time = pd.to_numeric(data["time"], errors="coerce")
    else:
        exec_time = pd.to_numeric(data.get("exec_time"), errors="coerce")
        solver_time = pd.to_numeric(data.get("solver_time"), errors="coerce")
        total_time = exec_time.fillna(0.0) + solver_time.fillna(0.0)

    effective_timed_out = csv_timed_out | (total_time > effective_timeout)
    complete = (~effective_timed_out) & (~crashed)
    complete_data = data[complete].copy()

    if complete_data.empty:
        print(
            f"% Koala timeout CDF input {path} has no completed rows; skipping koala CDF plot",
            file=sys.stderr,
        )
        return

    completion_times = total_time.loc[complete_data.index].dropna().to_numpy(dtype=float)
    completion_times.sort()

    if completion_times.size == 0:
        print(
            f"% Koala timeout CDF input {path} has no numeric completion times; skipping koala CDF plot",
            file=sys.stderr,
        )
        return

    def koala_group_label(benchmark_path):
        parts = Path(str(benchmark_path)).parts
        if "koala" in parts:
            idx = parts.index("koala")
            if idx + 1 < len(parts):
                label = parts[idx + 1]
                if label.startswith("."):
                    return None
                return label
        return "unknown"

    total_scripts = int(len(data))
    completed_count = int(len(completion_times))
    timeout_count = int((effective_timed_out & (~crashed)).sum())

    positive_start = max(1e-2, float(np.min(completion_times)) * 0.5)
    complete_x = np.concatenate(([positive_start], completion_times))
    complete_y_counts = np.concatenate(([0], np.arange(1, completed_count + 1)))
    complete_y = complete_y_counts.astype(float)
    timeout_x = None
    timeout_y = None
    if timeout_count > 0:
        timeout_x = np.full(timeout_count, float(effective_timeout))
        timeout_y = np.full(timeout_count, float(completed_count))

    runtime_data = data.copy()
    runtime_data["group"] = runtime_data["benchmark"].map(koala_group_label)
    runtime_data["time_value"] = pd.to_numeric(runtime_data["time"], errors="coerce").fillna(0.0)
    runtime_data = runtime_data[runtime_data["group"].notna()].copy()
    sash_runtime_by_group = (
        runtime_data.groupby("group", sort=False)["time_value"].sum().sort_values(ascending=False)
    )
    runtime_groups = sash_runtime_by_group.index.tolist()

    wall_runtime_by_group = pd.Series(dtype=float)
    wall_csv_path = Path(koala_sweep_dir).resolve().parents[0] / "koala_wall_time.csv"
    if wall_csv_path.exists():
        wall_df = pd.read_csv(wall_csv_path)
        if {"benchmark", "total_wall_time_sec"}.issubset(wall_df.columns):
            wall_runtime_by_group = (
                wall_df.assign(
                    benchmark=wall_df["benchmark"].astype(str),
                    total_wall_time_sec=pd.to_numeric(
                        wall_df["total_wall_time_sec"], errors="coerce"
                    ).fillna(0.0),
                )
                .groupby("benchmark", sort=False)["total_wall_time_sec"]
                .sum()
            )

    sash_runtime_values = np.array(
        [float(sash_runtime_by_group.get(group, 0.0)) for group in runtime_groups],
        dtype=float,
    )
    wall_runtime_values = np.array(
        [float(wall_runtime_by_group.get(group, 0.0)) for group in runtime_groups],
        dtype=float,
    )

    fig, ax = plt.subplots(figsize=(5.2, 1.72))

    # Top runtime subplot disabled for now; keep the code commented for easy restore.
    # gs = fig.add_gridspec(2, 1, height_ratios=[0.72, 0.65], hspace=0.76)
    # ax_top = fig.add_subplot(gs[0, 0])
    # ax = fig.add_subplot(gs[1, 0])
    #
    # bar_x = np.arange(len(runtime_groups))
    # bar_width = 0.34
    # ax_top.bar(
    #     bar_x - bar_width / 2,
    #     sash_runtime_values,
    #     width=bar_width,
    #     color=color_green,
    #     edgecolor=color_green,
    #     linewidth=0.6,
    #     label="SaSh",
    # )
    # ax_top.bar(
    #     bar_x + bar_width / 2,
    #     wall_runtime_values,
    #     width=bar_width,
    #     color="#9E9E9E",
    #     edgecolor="#9E9E9E",
    #     linewidth=0.6,
    #     label="Execution",
    # )
    # ax_top.set_ylabel("Runtime (s)")
    # ax_top.set_yscale("log")
    # ax_top.set_xticks(bar_x)
    # ax_top.set_xticklabels(runtime_groups, rotation=35, ha="right")
    # ax_top.grid(axis="y", alpha=0.25, linestyle=":")
    # ax_top.set_axisbelow(True)
    # ax_top.legend(
    #     loc="upper right",
    #     frameon=True,
    #     facecolor="white",
    #     edgecolor="0.8",
    #     framealpha=0.5,
    #     fontsize=9,
    # )

    completion_color = color_green
    completion_timeout_color = _lighten_color(completion_color, 0.45)
    ax.scatter(
        complete_x,
        complete_y,
        color=completion_color,
        s=5,
        label=sysname,
        zorder=4,
    )
    if timeout_x is not None and timeout_y is not None:
        ax.scatter(
            timeout_x,
            timeout_y,
            color=completion_timeout_color,
            s=5,
            zorder=4,
        )

    ax.set_ylim(bottom=0.0, top=float(max(total_scripts, 120)))
    ax.set_xscale("log")
    ax.set_xlim(left=positive_start, right=float(effective_timeout))

    ax.set_xlabel("Runtime")
    ax.set_ylabel("Completed")
    x_tick_candidates = [0.1, 1, 60, 600, 3600]
    x_ticks = [tick for tick in x_tick_candidates if tick <= ax.get_xlim()[1] + 1e-9]
    if x_ticks:
        ax.set_xticks(
            x_ticks,
            [
                "0.1s" if abs(tick - 0.1) < 1e-9 else
                "1s" if abs(tick - 1) < 1e-9 else
                "1m" if abs(tick - 60) < 1e-9 else
                "10m" if abs(tick - 600) < 1e-9 else
                "1h" if abs(tick - 3600) < 1e-9 else
                f"{tick:g}"
                for tick in x_ticks
            ],
        )
    ax.set_yticks(list(range(0, max(int(total_scripts), 120) + 1, 20)))
    ax.grid(axis="y", alpha=0.25, linestyle=":", zorder=0)
    ax.text(
        0.98,
        0.06,
        f"{completed_count}/{total_scripts} completed\n{timeout_count} timed out",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="none", alpha=0.9),
    )
    bottom_handles = [
        Line2D(
            [],
            [],
            linestyle="None",
            marker="s",
            markersize=6,
            markerfacecolor=completion_color,
            markeredgecolor=completion_color,
            label=sysname,
        )
    ]
    ax.legend(
        bottom_handles,
        [sysname],
        loc="lower right",
        bbox_to_anchor=(0.98, 0.26),
        ncol=1,
        frameon=False,
        fontsize=12,
        handlelength=1.2,
        handletextpad=0.6,
        borderaxespad=0.0,
        columnspacing=2.0,
    )
    fig.subplots_adjust(left=0.16, right=0.97, bottom=0.38, top=0.94)
    fig.savefig(output_path, format="pdf")
    plt.close(fig)


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
    coverage_output = os.path.join(args.output_dir, "coverage.pdf")
    plotted_multi_config_coverage = plot_coverage_by_config(
        timeout_sweep_dir, buggy_results, coverage_output
    )
    if not plotted_multi_config_coverage:
        coverage_results = buggy_results
        if not has_coverage_values(coverage_results):
            coverage_results = inject_coverage_from_timeout_sweep(
                coverage_results, timeout_sweep_dir
            )
        plot_coverage(coverage_results, coverage_output)
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
    full_coverage_stats = coverage_full_across_timeout_sweep(timeout_sweep_dir)
    bug_sweep_stats = bugs_caught_across_timeout_sweep(timeout_sweep_dir)

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
    if full_coverage_stats is None:
        print(
            "% Benchmarks with 100% coverage across all configurations/timeouts: N/A",
            file=sys.stderr,
        )
    else:
        full_count, comparable_count, csv_count = full_coverage_stats
        print(
            "% Benchmarks with 100% coverage across all configurations/timeouts: "
            f"{full_count}/{comparable_count} (from {csv_count} CSVs)",
            file=sys.stderr,
        )
    if bug_sweep_stats is None:
        print(
            "% Bug instances caught across all configurations/timeouts: N/A",
            file=sys.stderr,
        )
    else:
        caught_all_count, comparable_bug_count, csv_count = bug_sweep_stats
        print(
            "% Bug instances caught across all configurations/timeouts: "
            f"{caught_all_count}/{comparable_bug_count} (from {csv_count} CSVs)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
