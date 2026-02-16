#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


def get_loc(path: str) -> int:
    output = subprocess.check_output(["cloc", "--json", path], encoding="utf-8")
    data = json.loads(output)
    return int(data.get("SUM", {}).get("code", 0))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Precompute LoC cache for benchmarks used by table.py."
    )
    parser.add_argument(
        "--results_csv",
        type=Path,
        default=Path("results/results.csv"),
        help="Results CSV containing benchmark paths (default: results/results.csv).",
    )
    parser.add_argument(
        "--output_csv",
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
        print("results_csv is missing 'benchmark' column", file=sys.stderr)
        sys.exit(1)

    benchmarks = sorted({str(p) for p in data["benchmark"].dropna().tolist()})
    rows = []
    for benchmark in benchmarks:
        try:
            loc = get_loc(benchmark)
        except Exception as exc:
            print(f"[WARN] Failed cloc for {benchmark}: {exc}", file=sys.stderr)
            continue
        rows.append({"benchmark": benchmark, "loc": loc})

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output_csv, index=False)
    print(f"Wrote {len(rows)} rows to {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
