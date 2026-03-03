#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 0 ]]; then
  echo "Usage: ./full_eval.sh" >&2
  echo "This script takes no arguments." >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

TIMEOUTS_CSV="1,10,20,30,40,50,60,70,80,90,100"
CONFIGS_CSV="no_opts,smart_forking,dfs_on"

echo "==> Running benchmark timeout sweep"
bash scripts/timeout_sweep_eval.sh --timeouts "${TIMEOUTS_CSV}" --configs "${CONFIGS_CSV}"

echo
echo "==> Running Koala timeout sweep"
bash scripts/on_koala --timeouts "300" --configs "dfs_on"

echo
echo "==> Running regular evaluation"
bash eval.sh

echo
echo "Full evaluation completed."
