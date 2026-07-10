#!/bin/bash

TOP=$(git rev-parse --show-toplevel)

eval_dir="${TOP}/weather"
outputs_dir="${eval_dir}/outputs"
scripts_dir="${eval_dir}/scripts"
input_dir="${eval_dir}/inputs"

export LC_ALL=C

size=full
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            size="small"
            shift
            ;;
        --min)
            size="min"
            shift
            ;;
        --full)
            size="full"
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

KOALA_SHELL=${KOALA_SHELL:-bash}

export BENCHMARK_CATEGORY="weather"

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

if should_run "max-temp"; then
    echo "max-temp"
    export input_file="${input_dir}/temperatures.$size.txt"
    export statistics_dir="$outputs_dir/statistics.$size"

    mkdir -p "$statistics_dir"

    BENCHMARK_INPUT_FILE="$(realpath "$input_file")"
    export BENCHMARK_INPUT_FILE

    BENCHMARK_SCRIPT="$(realpath "${scripts_dir}/temp-analytics.sh")"
    export BENCHMARK_SCRIPT

    $KOALA_SHELL "$scripts_dir/temp-analytics.sh"

    echo "$?"
fi

if should_run "tuft-weather"; then
    echo "tuft-weather"
    export BENCHMARK_SCRIPT="$scripts_dir/tuft-weather.sh"
    export BENCHMARK_INPUT_FILE="$input_dir/tuft_weather.${size}.txt"

    mkdir -p "$outputs_dir/$size"

    $KOALA_SHELL "$BENCHMARK_SCRIPT" "$BENCHMARK_INPUT_FILE" "$size" > "$outputs_dir/$size/turf_weather.log"
    echo "$?"

    rm -rf "$outputs_dir/$size/plots" || true
    mkdir -p "$outputs_dir/$size/plots"

    if [ -d "$eval_dir/plots" ]; then
        mv "$eval_dir/plots"/* "$outputs_dir/$size/plots/"
    fi
fi