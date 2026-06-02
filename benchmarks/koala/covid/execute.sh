#!/bin/bash

TOP=$(git rev-parse --show-toplevel)
eval_dir="${TOP}/covid"
input_dir="${eval_dir}/inputs"
outputs_dir="${eval_dir}/outputs"
scripts_dir="${eval_dir}/scripts"
export LC_ALL=C

suffix=""
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            suffix="_small"
            shift
            ;;
        --min)
            suffix="_min"
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

input_file="$input_dir/in$suffix.csv"
output_scoped="$outputs_dir/outputs$suffix"
mkdir -p "$output_scoped"

KOALA_SHELL="${KOALA_SHELL:-bash}"
export KOALA_SHELL

BENCHMARK_CATEGORY="covid"
export BENCHMARK_CATEGORY

BENCHMARK_INPUT_FILE="$(realpath "$input_file")"
export BENCHMARK_INPUT_FILE

should_run() {
    script_num=$1
    if [ -z "$selected_scripts" ]; then
        return 0
    fi
    for selected in $selected_scripts; do
        if [ "$selected" = "$script_num" ]; then
            return 0
        fi
    done
    return 1
}

for i in 1 2 3 4 5; do
    if should_run "$i"; then
        script="$scripts_dir/$i.sh"
        BENCHMARK_SCRIPT="$(realpath "$script")"
        export BENCHMARK_SCRIPT
        $KOALA_SHELL "$script" "$input_file" > "$output_scoped/$i.out"
    fi
done