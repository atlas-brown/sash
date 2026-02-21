#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run full evaluation multiple times with different timeouts.

Usage:
  scripts/timeout_sweep_eval.sh [options]

Options:
  --timeouts LIST      Comma-separated timeout values in seconds.
                       Example: --timeouts 1,5,10,20,30,60
                       Default: 1,5,10,20,30,60
  --mock               Generate synthetic (plausible) CSVs instead of running evaluation.
  --base-csv PATH      Template CSV used by --mock mode.
                       Default: results/results.csv
  --output-dir DIR     Parent directory for sweep outputs.
                       CSVs will be written to: <DIR>/timeout-sweep
                       Default: results
  --only REGEX         Benchmark regex passed to evaluation.py --only.
                       Default: .*
  --jobs N             Number of evaluation workers (-j).
                       Default: 8
  --dry-run            Print commands without executing.
  -h, --help           Show this help and exit.

Behavior:
  For each timeout T, runs:
    1) All optimizations disabled + no DFS
       (-D --fork-everywhere --disable-solver-optimizations)
    2) Smart forking enabled + solver optimizations disabled + no DFS
       (-D --disable-solver-optimizations)
    3) Solver optimizations enabled + no DFS
       (-D)
    4) Full SaSh (all DFS passes enabled; default config)
       [compatibility key: dfs_on]
  and writes:
    <output-dir>/timeout-sweep/results_t<T>_no_opts.csv
    <output-dir>/timeout-sweep/results_t<T>_smart_forking.csv
    <output-dir>/timeout-sweep/results_t<T>_solver_opts.csv
    <output-dir>/timeout-sweep/results_t<T>_dfs_on.csv
  Plus compatibility copy for DFS-on:
    <output-dir>/timeout-sweep/results_t<T>.csv
EOF
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

TIMEOUTS_CSV="1,5,10,20,30,60"
OUTPUT_DIR="results"
SWEEP_SUBDIR="timeout-sweep"
ONLY_REGEX=".*"
JOBS="8"
DRY_RUN=0
MOCK=0
BASE_CSV="results/results.csv"
MOCK_SEED=42

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeouts)
      TIMEOUTS_CSV="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --mock)
      MOCK=1
      shift
      ;;
    --base-csv)
      BASE_CSV="${2:-}"
      shift 2
      ;;
    --only)
      ONLY_REGEX="${2:-}"
      shift 2
      ;;
    --jobs)
      JOBS="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

IFS=',' read -r -a TIMEOUTS <<< "${TIMEOUTS_CSV}"
if [[ ${#TIMEOUTS[@]} -eq 0 ]]; then
  echo "No timeouts specified" >&2
  exit 2
fi

TARGET_DIR="${OUTPUT_DIR%/}/${SWEEP_SUBDIR}"
mkdir -p "${REPO_ROOT}/${TARGET_DIR}"

echo "Repository: ${REPO_ROOT}"
echo "Output dir: ${TARGET_DIR}"
echo "Timeouts:   ${TIMEOUTS_CSV}"
if [[ ${MOCK} -eq 1 ]]; then
  echo "Mode:       mock"
  echo "Base CSV:   ${BASE_CSV}"
fi
echo "Sweep:      no_opts -> smart_forking -> solver_opts -> dfs_on"

for raw_t in "${TIMEOUTS[@]}"; do
  t="$(echo "${raw_t}" | xargs)"
  if [[ -z "${t}" ]]; then
    continue
  fi
  if ! [[ "${t}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid timeout value: '${t}'" >&2
    exit 2
  fi

  DFS_MODES=("no_opts" "smart_forking" "solver_opts" "dfs_on")

  for dfs_mode in "${DFS_MODES[@]}"; do
    csv_path="${TARGET_DIR}/results_t${t}_${dfs_mode}.csv"

    if [[ ${MOCK} -eq 1 ]]; then
      seed_offset=0
      case "${dfs_mode}" in
        no_opts) seed_offset=0 ;;
        smart_forking) seed_offset=2741 ;;
        solver_opts) seed_offset=5471 ;;
        dfs_on) seed_offset=7919 ;;
        *) seed_offset=0 ;;
      esac
      cmd=(
        python scripts/generate_mock_eval_csv.py
        --base-csv "${BASE_CSV}"
        --output-csv "${csv_path}"
        --timeout "${t}"
        --seed "$((MOCK_SEED + seed_offset))"
      )
    else
      cmd=(python scripts/evaluation.py -f -v --only "${ONLY_REGEX}" -t "${t}" -T "${t}" -c "${csv_path}")
      if [[ -n "${JOBS}" ]]; then
        cmd+=(-j "${JOBS}")
      fi
      case "${dfs_mode}" in
        no_opts)
          cmd+=(-D --fork-everywhere --disable-solver-optimizations)
          ;;
        smart_forking)
          cmd+=(-D --disable-solver-optimizations)
          ;;
        solver_opts)
          cmd+=(-D)
          ;;
      esac
    fi

    echo
    echo "=== timeout ${t}s | ${dfs_mode} ==="
    echo "${cmd[*]}"
    if [[ ${DRY_RUN} -eq 0 ]]; then
      (
        cd "${REPO_ROOT}"
        "${cmd[@]}"
      )
    fi

    # Keep legacy DFS-on filename for downstream scripts expecting results_t<T>.csv.
    if [[ "${dfs_mode}" == "dfs_on" && ${DRY_RUN} -eq 0 ]]; then
      cp -f "${REPO_ROOT}/${csv_path}" "${REPO_ROOT}/${TARGET_DIR}/results_t${t}.csv"
    fi
  done
done

echo
echo "Done. CSV files are in: ${TARGET_DIR}"
