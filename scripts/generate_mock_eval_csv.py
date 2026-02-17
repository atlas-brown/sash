#!/usr/bin/env -S uv run python3
import argparse
import math
import random
import re
from collections import Counter

import pandas as pd

LINE_RE = re.compile(r"^L([0-9]+):")
EXTRA_CODES = [
    "unbound",
    "dead_code",
    "const_cond",
    "loop_once",
    "cmd_expected_path_state",
    "data_loss",
    "not_a_command",
    "word_split",
    "word_split_del_sys_file",
]


def parse_issue_list(value):
    if pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(";") if item and item.strip()]


def issue_line(issue, fallback=50):
    m = LINE_RE.match(str(issue))
    if m:
        return int(m.group(1))
    return fallback


def fmt_issues(issues):
    if not issues:
        return ""
    return ";".join(issues)


def line_numbers_field(issues):
    nums = [str(issue_line(i, fallback=0)) for i in issues]
    return ";".join(nums)


def choose_expected_hits(expected, probability, rng):
    hits = []
    for issue in expected:
        if rng.random() < probability:
            hits.append(issue)
    if expected and not hits and rng.random() < (0.55 * probability):
        hits.append(rng.choice(expected))
    return hits


def bench_complexity(name):
    # Stable pseudo-complexity in [0.75, 1.40]
    acc = 0
    for ch in str(name):
        acc = (acc * 131 + ord(ch)) % 1000003
    return 0.75 + ((acc % 650) / 1000.0)


def generate_mock(base_df, timeout, seed):
    rng = random.Random(seed + int(timeout * 1000))
    out = base_df.copy()

    for idx, row in out.iterrows():
        kind = str(row.get("kind", "buggy"))
        expected = parse_issue_list(row.get("expected_results", ""))
        expected_counter = Counter(expected)
        complexity = bench_complexity(row.get("benchmark", idx))
        budget = timeout / (timeout + 25.0)

        if kind in ("buggy", "buggy_variant"):
            base_detect = 0.72 + 0.24 * budget
            if kind == "buggy_variant":
                base_detect -= 0.10
            base_detect = min(max(base_detect, 0.20), 0.98)
            matched = choose_expected_hits(expected, base_detect, rng)
            extras_mean = 1.0 + 2.0 * budget + (0.6 if kind == "buggy_variant" else 0.0)
        else:
            # For fixed scripts, these expected IDs correspond to false positives.
            fp_prob = 0.02 + 0.05 * budget
            if kind == "fixed_variant":
                fp_prob += 0.01
            matched = choose_expected_hits(expected, fp_prob, rng)
            extras_mean = 0.2 + 0.9 * budget

        extras = []
        expected_lines = [issue_line(i, fallback=60) for i in expected]
        center = int(sum(expected_lines) / len(expected_lines)) if expected_lines else 80
        spread = max(10, int(35 * complexity))
        extra_count = max(0, int(round(rng.gauss(extras_mean, 0.9))))
        for _ in range(extra_count):
            code = rng.choice(EXTRA_CODES)
            ln = max(1, int(round(rng.gauss(center, spread))))
            extras.append(f"L{ln}:{code}")

        actual = matched + extras
        actual.sort(key=lambda s: (issue_line(s, fallback=10**9), str(s)))
        actual_counter = Counter(actual)

        detected_all = True
        for issue, count in expected_counter.items():
            if actual_counter[issue] < count:
                detected_all = False
                break

        timeout_prob = 0.34 * math.exp(-timeout / 42.0) * complexity
        if kind.endswith("_variant"):
            timeout_prob += 0.04
        if kind.startswith("fixed"):
            timeout_prob -= 0.03
        timeout_prob = min(max(timeout_prob, 0.01), 0.88)
        timed_out = rng.random() < timeout_prob

        if timed_out:
            solver_time = timeout * rng.uniform(0.95, 1.08)
            exec_time = timeout * rng.uniform(0.95, 2.10) * complexity
        else:
            solver_time = timeout * rng.uniform(0.05, 0.75) * complexity
            exec_time = timeout * rng.uniform(0.12, 1.35) * complexity

        total_time = exec_time + solver_time

        out.at[idx, "missing_gt"] = False
        out.at[idx, "crashed"] = False
        out.at[idx, "timed_out"] = bool(timed_out)
        out.at[idx, "solver_time"] = round(float(solver_time), 4)
        out.at[idx, "exec_time"] = round(float(exec_time), 4)
        out.at[idx, "time"] = round(float(total_time), 4)
        out.at[idx, "detected_all"] = bool(detected_all)
        out.at[idx, "actual_results"] = fmt_issues(actual)
        out.at[idx, "line_numbers"] = line_numbers_field(actual)

    return out


def main():
    parser = argparse.ArgumentParser(
        description="Generate a synthetic-but-plausible evaluation CSV for plotting experiments."
    )
    parser.add_argument(
        "--base-csv",
        type=str,
        default="results/results.csv",
        help="Template CSV to copy benchmark/kind/expected fields from.",
    )
    parser.add_argument("--output-csv", type=str, required=True, help="Path to write mock CSV.")
    parser.add_argument("--timeout", type=float, required=True, help="Synthetic timeout value (seconds).")
    parser.add_argument("--seed", type=int, default=2026, help="Random seed for reproducibility.")
    args = parser.parse_args()

    base_df = pd.read_csv(args.base_csv)
    mock_df = generate_mock(base_df, args.timeout, args.seed)
    mock_df.to_csv(args.output_csv, index=False)
    print(f"Wrote {args.output_csv} with {len(mock_df)} rows (timeout={args.timeout}s, seed={args.seed})")


if __name__ == "__main__":
    main()
