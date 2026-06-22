#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}" || exit 1

# Select which portions of the evaluation to run
RUN_ALL=1
RUN_MAIN=0
RUN_SWEEP=0
RUN_KOALA=0

# Parameters
MAIN_TIMEOUT="${MAIN_TIMEOUT:-60}"
MAIN_JOBS=${MAIN_JOBS:-1}
KOALA_TIMEOUT="${KOALA_TIMEOUT:-$((15 * 60))}" # 15 minutes
SWEEP_TIMEOUTS_CSV="${SWEEP_TIMEOUTS_CSV:-1,10,20,30,40,50,60,70,80,90,100}"
SWEEP_JOBS="${SWEEP_JOBS:-4}"
FORCE=0  # Ignore cached results when nonzero
DRY_RUN=0

# Input and output paths
RESULTS_DIR="${RESULTS_DIR:-"results"}"
MAIN_RESULTS_DIR="${RESULTS_DIR}/main-eval"
SWEEP_RESULTS_DIR="${RESULTS_DIR}/timeout-sweep"
KOALA_RESULTS_DIR="${RESULTS_DIR}/koala-eval"
FIGURES_DIR="${RESULTS_DIR}/figures"
KOALA_DIR="${REPO_ROOT}/benchmarks/koala"

# Options
NO_COLOR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
    --main)
        RUN_MAIN=1
        RUN_ALL=0
        shift
        ;;
    --sweep)
        RUN_SWEEP=1
        RUN_ALL=0
        shift
        ;;
    --koala)
        RUN_KOALA=1
        RUN_ALL=0
        shift
        ;;
    --main-timeout)
        MAIN_TIMEOUT="${2:-}"
        shift 2
        ;;
    --koala-timeout)
        KOALA_TIMEOUT="${2:-}"
        shift 2
        ;;
    --sweep-timeouts)
        SWEEP_TIMEOUTS_CSV="${2:-}"
        shift 2
        ;;
    --main-jobs)
        MAIN_JOBS="${2:-}"
        shift 2
        ;;
    --sweep-jobs)
        SWEEP_JOBS="${2:-}"
        shift 2
        ;;
    --force)
        FORCE=1
        shift
        ;;
    --dry-run)
        DRY_RUN=1
        shift
        ;;
    --no-color)
        NO_COLOR="--no-color"
        shift
        ;;
    *)
        echo "Unknown argument: $1" >&2
        exit 2
        ;;
    esac
done

if [[ ${RUN_ALL} -eq 1 ]]; then
    RUN_MAIN=1
    RUN_SWEEP=1
    RUN_KOALA=1
fi

# Verify cloc is installed
if ! command -v cloc >/dev/null 2>&1; then
    echo "Error: 'cloc' is not installed. Install it (e.g., apt install cloc) and retry." >&2
    exit 1
fi

# Parse the sweep timeouts
IFS=',' read -r -a SWEEP_TIMEOUTS <<<"${SWEEP_TIMEOUTS_CSV}"
if [[ ${#SWEEP_TIMEOUTS[@]} -eq 0 ]]; then
    echo "No sweep timeouts specified" >&2
    exit 2
fi
unset SWEEP_TIMEOUTS_CSV

mkdir -p "${MAIN_RESULTS_DIR}" "${SWEEP_RESULTS_DIR}" "${KOALA_RESULTS_DIR}" "${FIGURES_DIR}"

run_cmd() {
    echo "$*"
    if [[ ${DRY_RUN} -eq 0 ]]; then
        "$@"
    fi
}

run_main() {
    echo "> Running main evaluation"
    main_eval_csv="${MAIN_RESULTS_DIR}/results_t${MAIN_TIMEOUT}.csv"
    if [[ ${FORCE} -eq 0 && -f "${main_eval_csv}" ]]; then
        echo "Skipping existing: ${main_eval_csv}"
    else
        run_cmd uv run scripts/evaluation.py ${NO_COLOR} -f -v -j "${MAIN_JOBS}" -t "${MAIN_TIMEOUT}" -c "${main_eval_csv}"
    fi
}

run_sweep() {
    echo "> Running evaluation timeout sweep"
    for t in "${SWEEP_TIMEOUTS[@]}"; do
        opts=""
        # Plotting script relies on output names to end with "_no_opts", "_smart_forking" or "_dfs_on"
        for mode in "no_opts" "smart_forking" "dfs_on"; do
            case "${mode}" in
            "no_opts")
                opts="--disable-dfs --disable-optimistic-forking"
                ;;
            "smart_forking")
                opts="--disable-dfs"
                ;;
            "dfs_on")
                opts=""
                ;;
            *)
                exit 1  # Unreachable
                ;;
            esac
            sweep_csv="${SWEEP_RESULTS_DIR}/results_t${t}_${mode}.csv"
            if [[ ${FORCE} -eq 0 && -f "${sweep_csv}" ]]; then
                echo "Skipping existing: ${sweep_csv}"
            else
                run_cmd uv run scripts/evaluation.py ${NO_COLOR} -j "${SWEEP_JOBS}" -t "${t}" ${opts} -c "${sweep_csv}"
            fi
        done
    done
}

run_koala() {
    echo "> Running Koala"
    if [[ -d "${KOALA_DIR}" ]]; then
        koala_csv="${KOALA_RESULTS_DIR}/results_t${KOALA_TIMEOUT}.csv"
        if [[ ${FORCE} -eq 0 && -f "${koala_csv}" ]]; then
            echo "Skipping existing: ${koala_csv}"
        else
            run_cmd uv run scripts/run_on_dir.py "${KOALA_DIR}" -t "${KOALA_TIMEOUT}" -c "${koala_csv}"
        fi
    else
        echo "Koala directory missing; skipping Koala: ${KOALA_DIR}"
    fi
}

compute_loc() {
    echo "> Computing LoC"
    if [[ ${FORCE} -eq 0 && -f ${RESULTS_DIR}/benchmark_loc.csv ]]; then
        echo "Skipping existing: ${RESULTS_DIR}/benchmark_loc.csv"
    else
        run_cmd uv run scripts/precompute_loc_cache.py --results-csv "${main_eval_csv}" --output-csv "${RESULTS_DIR}/benchmark_loc.csv"
    fi
}

generate_plots() {
    echo "> Generating plots"
    run_cmd uv run python - <<PY
from pathlib import Path
from scripts import plots

figures_dir = Path("${FIGURES_DIR}")
timeout_sweep_dir = Path("${SWEEP_RESULTS_DIR}")
koala_sweep_dir = Path("${KOALA_RESULTS_DIR}")

if "${RUN_MAIN}" == "1":
    results_csv = Path("${main_eval_csv:-}")
    all_results = plots.load_csv(str(results_csv))
    plots.plot_bug_detection_bars_split_versions(
        all_results,
        str(figures_dir / "main-eval.pdf"),
    )
if "${RUN_SWEEP}" == "1":
    plots.plot_timeout_sweep_bug_catch(
        str(timeout_sweep_dir),
        str(figures_dir / "timeout-sweep.pdf"),
    )
if "${RUN_KOALA}" == "1":
    plots.plot_koala_timeout_cdf(
        str(koala_sweep_dir),
        str(figures_dir / "koala.pdf"),
    )
PY
}

generate_appendix() {
    echo "> Generating LaTeX table"
    if [[ ${FORCE} -eq 0 && -f ${RESULTS_DIR}/table.tex ]]; then
        echo "Skipping existing: ${RESULTS_DIR}/table.tex"
    else
        run_cmd uv run scripts/table.py --appendix --loc-cache-path "${RESULTS_DIR}/benchmark_loc.csv" --results-csv "${main_eval_csv}" >"${RESULTS_DIR}/table.tex"
    fi
}

if [[ ${RUN_MAIN} -eq 1 ]]; then
    run_main
    echo
    compute_loc
    echo
    generate_appendix
    echo
fi

if [[ ${RUN_SWEEP} -eq 1 ]]; then
    run_sweep
    echo
fi

if [[ ${RUN_KOALA} -eq 1 ]]; then
    run_koala
    echo
fi

if [[ ${RUN_MAIN} -eq 1 ]] || [[ ${RUN_SWEEP} -eq 1 ]] || [[ ${RUN_KOALA} -eq 1 ]]; then
    generate_plots
    echo
fi

echo "Done"

if [[ ${RUN_MAIN} -eq 1 ]] || [[ ${RUN_SWEEP} -eq 1 ]] || [[ ${RUN_KOALA} -eq 1 ]]; then
    echo "Outputs:"
fi

if [[ ${RUN_MAIN} -eq 1 ]]; then
    echo
    echo "  ${main_eval_csv} (evaluation of buggy programs, fixed programs, and variants)"
    echo "  ${RESULTS_DIR}/benchmark_loc.csv (LoC information for all benchmarks)"
    echo "  ${RESULTS_DIR}/table.tex (appendix)"
    echo "  ${RESULTS_DIR}/figures/main-eval.pdf (bar plot)"
fi

if [[ ${RUN_SWEEP} -eq 1 ]]; then
    echo
    echo "  ${SWEEP_RESULTS_DIR}/results_t*.csv (evaluation of buggy programs under different timeouts and SaSh features)"
    echo "  ${RESULTS_DIR}/figures/timeout-sweep.pdf (line plot)"
fi

if [[ ${RUN_KOALA} -eq 1 ]]; then
    echo
    echo "  ${KOALA_RESULTS_DIR}/results_t*.csv (evaluation of SaSh on the Koala benchmarks)"
    echo "  ${RESULTS_DIR}/figures/koala.pdf (CDF plot)"
fi
