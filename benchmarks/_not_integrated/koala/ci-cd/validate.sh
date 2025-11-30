#!/bin/bash

TOP="$(git rev-parse --show-toplevel)"
eval_dir="${TOP}/ci-cd"

min_benchmark=(
    "xz-clang"
)

run_min=false
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --min)
            run_min=true
            shift
            ;;
        -s|--scripts)
            shift
            while [ $# -gt 0 ] && [ "$(echo "$1" | cut -c1)" != "-" ]; do
                if [ -z "$selected_scripts" ]; then
                    selected_scripts="$1"
                else
                    selected_scripts="$selected_scripts $1"
                fi
                shift
            done
            ;;
        *)
            shift
            ;;
    esac
done

should_run() {
    script_name=$1
    if [ -z "$selected_scripts" ]; then
        return 0
    fi
    for selected in $selected_scripts; do
        if [ "$selected" = "$script_name" ]; then
            return 0
        fi
    done
    return 1
}

if [ "$run_min" = true ]; then
    for bench in "${min_benchmark[@]}"; do
        if should_run "$bench"; then
            script_path="$eval_dir/riker/$bench/validate.sh"
            if [ -x "$script_path" ]; then
                "$script_path"
            else
                echo "Error: $script_path not found or not executable."
                exit 1
            fi
        fi
    done
else
    for bench in "$eval_dir"/riker/*; do
        bench_name="$(basename "$bench")"
        if should_run "$bench_name"; then
            "$bench/validate.sh"
        fi
    done
fi

if should_run "makeself"; then
    status=0
    if grep -q "FAIL" "${eval_dir}/run_results.log" 2>/dev/null; then
        status=1
        echo makeself $status
        exit $status
    fi
    echo makeself $status
fi