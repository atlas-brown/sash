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
                       Default: all available cores (evaluation default)
  --disable-dfs        Run only the DFS-disabled sweep mode (backward compatible behavior).
  --dry-run            Print commands without executing.
  -h, --help           Show this help and exit.

Behavior:
  For each timeout T, runs:
    1) DFS enabled (full)
    2) DFS enabled without targeted DFS
    3) DFS enabled without unbound-empty DFS
    4) DFS disabled (-D)
  and writes:
    <output-dir>/timeout-sweep/results_t<T>_dfs_on.csv
    <output-dir>/timeout-sweep/results_t<T>_dfs_no_targeted.csv
    <output-dir>/timeout-sweep/results_t<T>_dfs_no_unbound_empty.csv
    <output-dir>/timeout-sweep/results_t<T>_dfs_off.csv
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
JOBS=""
DISABLE_DFS=0
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
    --disable-dfs)
      DISABLE_DFS=1
      shift
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
if [[ ${DISABLE_DFS} -eq 1 ]]; then
  echo "DFS modes:  disabled only"
else
  echo "DFS modes:  full + no_targeted + no_unbound_empty + disabled"
fi

for raw_t in "${TIMEOUTS[@]}"; do
  t="$(echo "${raw_t}" | xargs)"
  if [[ -z "${t}" ]]; then
    continue
  fi
  if ! [[ "${t}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid timeout value: '${t}'" >&2
    exit 2
  fi

  if [[ ${DISABLE_DFS} -eq 1 ]]; then
    DFS_MODES=("dfs_off")
  else
    DFS_MODES=("dfs_on" "dfs_no_targeted" "dfs_no_unbound_empty" "dfs_off")
  fi

  for dfs_mode in "${DFS_MODES[@]}"; do
    csv_path="${TARGET_DIR}/results_t${t}_${dfs_mode}.csv"

    if [[ ${MOCK} -eq 1 ]]; then
      seed_offset=0
      case "${dfs_mode}" in
        dfs_on) seed_offset=0 ;;
        dfs_no_targeted) seed_offset=2741 ;;
        dfs_no_unbound_empty) seed_offset=5471 ;;
        dfs_off) seed_offset=7919 ;;
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
        dfs_off)
          cmd+=(-D)
          ;;
        dfs_no_targeted)
          cmd+=(--disable-targeted-dfs)
          ;;
        dfs_no_unbound_empty)
          cmd+=(--disable-unbound-empty-dfs)
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
