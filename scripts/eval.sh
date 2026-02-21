#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${repo_root}"

mkdir -p results
t=60

# Run full benchmark evaluation on regular (buggy) + fixed scripts.
uv run scripts/evaluation.py -j 8 -t $t -T $t -f -v -c results/results.csv -H results/results.html
