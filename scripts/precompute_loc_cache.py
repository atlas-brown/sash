#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]


def resolve_benchmark_path(path: str) -> Path:
    p = Path(str(path))

    # Direct hit.
    if p.exists():
        return p

    # Repo-relative path.
    if not p.is_absolute():
        candidate = ROOT_DIR / p
        if candidate.exists():
            return candidate

    # Foreign absolute path: keep only benchmarks/... suffix.
    if "benchmarks" in p.parts:
        idx = p.parts.index("benchmarks")
        candidate = ROOT_DIR / Path(*p.parts[idx:])
        if candidate.exists():
            return candidate

    # Best effort fallback.
    return p if p.is_absolute() else ROOT_DIR / p


def get_loc(path: str) -> int:
    output = subprocess.check_output(["cloc", "--json", path], encoding="utf-8")
    data = json.loads(output)
    return int(data.get("SUM", {}).get("code", 0))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Precompute LoC cache for benchmarks used by table.py."
    )
    parser.add_argument(
        "--results-csv",
        type=Path,
        default=Path("results/results.csv"),
        help="Results CSV containing benchmark paths (default: results/results.csv).",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("results/benchmark_loc.csv"),
        help="Output cache CSV path (default: results/benchmark_loc.csv).",
    )
    args = parser.parse_args()

    if not args.results_csv.exists():
        print(f"Missing results CSV: {args.results_csv}", file=sys.stderr)
        sys.exit(1)

    data = pd.read_csv(args.results_csv)
    if "benchmark" not in data.columns:
        print("results-csv is missing 'benchmark' column", file=sys.stderr)
        sys.exit(1)

    benchmarks = sorted({str(p) for p in data["benchmark"].dropna().tolist()})
    rows = []
    for benchmark in benchmarks:
        resolved = resolve_benchmark_path(benchmark)
        try:
            loc = get_loc(str(resolved))
        except Exception as exc:
            print(
                f"[WARN] Failed cloc for {benchmark} (resolved: {resolved}): {exc}",
                file=sys.stderr,
            )
            continue
        rows.append({"benchmark": benchmark, "loc": loc})

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output_csv, index=False)
    print(f"Wrote {len(rows)} rows to {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
