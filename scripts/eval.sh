#!/usr/bin/env bash
set -euo pipefail

top="$(git rev-parse --show-toplevel)"
cd "${top}"

mkdir -p results
t=60

# Run full benchmark evaluation on regular (buggy) + fixed scripts.
python scripts/evaluation.py -j 4 -t $t -T $t -f -v -c results/results.csv -H results/results.html
