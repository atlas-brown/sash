#!/bin/bash

SUITE_DIR="$(realpath "$(dirname "$0")")"
export SUITE_DIR
cd "$SUITE_DIR" || exit 1

TOP=$(git rev-parse --show-toplevel)
eval_dir="${TOP}/oneliners"
scripts_dir="${eval_dir}/scripts"
input_dir="${eval_dir}/inputs"
export TIMEFORMAT=%R

KOALA_SHELL=${KOALA_SHELL:-bash}
export BENCHMARK_CATEGORY="oneliners"
size=full
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            size=small
            shift
            ;;
        --min)
            size=min
            shift
            ;;
        --full)
            size=full
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
outputs_dir="${eval_dir}/outputs/$size"
mkdir -p "$outputs_dir"
export LC_ALL=C

if [ "$size" = "small" ]; then
    scripts_inputs=(
        "nfa-regex;10M"
        "sort;30M"
        "top-n;30M"
        "wf;30M"
        "spell;30M"
        "diff;30M"
        "bi-grams;30M"
        "set-diff;30M"
        "sort-sort;30M"
        "uniq-ips;logs-popcount-org_$size"
        "comm;comm_$size"
        "opt-parallel;chessdata_small"
    )
    chess_input="$input_dir/chessdata_small"
    comm_input="$input_dir/comm_small"

elif [ "$size" = "min" ]; then
    scripts_inputs=(
        "nfa-regex;1M"
        "sort;1M"
        "top-n;1M"
        "wf;1M"
        "spell;1M"
        "diff;1M"
        "bi-grams;1M"
        "set-diff;1M"
        "sort-sort;1M"
        "uniq-ips;logs-popcount-org_$size"
        "comm;comm_$size"
        "opt-parallel;chessdata_min"
    )
    chess_input="$input_dir/chessdata_min"
    comm_input="$input_dir/comm_min"

else
    scripts_inputs=(
        "nfa-regex;1G"
        "sort;3G"
        "top-n;3G"
        "wf;3G"
        "spell;3G"
        "diff;3G"
        "bi-grams;3G"
        "set-diff;3G"
        "sort-sort;3G"
        "uniq-ips;logs-popcount-org_$size"
        "comm;comm_$size"
        "opt-parallel;chessdata"
    )
    chess_input="$input_dir/chessdata"
    comm_input="$input_dir/comm_full"
fi

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

for script_input in "${scripts_inputs[@]}"
do
    case "$script_input" in
        opt-parallel*)
            if should_run "opt-parallel"; then
                IFS=";" read -r -a parsed <<< "${script_input}"
                script_file="$scripts_dir/${parsed[0]}.sh"
                echo "$script_file"
                export BENCHMARK_INPUT_FILE="${chess_input}"
                BENCHMARK_SCRIPT="$(realpath "$script_file")"
                export BENCHMARK_SCRIPT
                $KOALA_SHELL "$script_file" "$chess_input" > "${outputs_dir}/opt-parallel.out"
                echo "$?"
            fi
            ;;
        comm*)
            if should_run "comm"; then
                IFS=";" read -r -a parsed <<< "${script_input}"
                script_file="$scripts_dir/${parsed[0]}.sh"
                echo "$script_file"
                export BENCHMARK_INPUT_FILE="${comm_input}"
                BENCHMARK_SCRIPT="$(realpath "$script_file")"
                export BENCHMARK_SCRIPT
                $KOALA_SHELL "$script_file" "${comm_input}" > "${outputs_dir}/comm.out"
                echo "$?"
            fi
            ;;
        *)
            IFS=";" read -r -a parsed <<< "${script_input}"
            script_name="${parsed[0]}"
            
            if should_run "$script_name"; then
                script_file="$scripts_dir/${parsed[0]}.sh"
                input_file="$input_dir/${parsed[1]}.txt"
                output_file="$outputs_dir/${parsed[0]}.out"

                echo "$script_file"
                BENCHMARK_INPUT_FILE="$(realpath "$input_file")"
                export BENCHMARK_INPUT_FILE

                BENCHMARK_SCRIPT="$(realpath "$script_file")"
                export BENCHMARK_SCRIPT
                
                $KOALA_SHELL "$script_file" "$input_file" > "$output_file"
                echo "$?"
            fi
            ;;
    esac
done