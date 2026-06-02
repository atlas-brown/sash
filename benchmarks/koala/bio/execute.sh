#!/bin/bash

# create bam files with regions
################### 1KG SAMPLES
IN="inputs/bio-full"
IN_NAME="inputs/bio-full/input.txt"
OUT="outputs"

for arg in "$@"; do
    case "$arg" in
        --small)
            IN_NAME="inputs/bio-small/input_small.txt" 
            IN="inputs/bio-small"
            ;;
        --min)   
            IN_NAME="inputs/bio-min/input_min.txt" 
            IN="inputs/bio-min"
            ;;
    esac
done

size=full
subset=false
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            size=full
            subset=true
            shift
            ;;
        --min)
            size=min
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

# export SIZE="$size" # for PARAMS.sh
export SIZE=full

KOALA_SHELL="${KOALA_SHELL:-bash}"
export BENCHMARK_CATEGORY="bio"
export KOALA_SHELL

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

if should_run "bio"; then
    script_file="./scripts/bio.sh"
    BENCHMARK_SCRIPT="$(realpath "$script_file")"
    export BENCHMARK_SCRIPT

    BENCHMARK_INPUT_FILE="$(realpath "$IN")"
    export BENCHMARK_INPUT_FILE

    $KOALA_SHELL "$script_file" "$IN" "$IN_NAME" "$OUT"
fi

# Note: The 'data.sh' script must be run first
teraseq_script_names="data
run_dRNASeq
run_5TERA"

if [ "$size" = "min" ]; then
    exit 0
fi

if [ "$subset" = true ]; then
teraseq_script_names="data
run_dRNASeq"
fi

BENCHMARK_INPUT_FILE="$(realpath "inputs/full")"
export BENCHMARK_INPUT_FILE
while IFS= read -r script; do
    if should_run "$script"; then
        script_file="./scripts/$script.sh"
        BENCHMARK_SCRIPT="$(realpath "$script_file")"
        export BENCHMARK_SCRIPT

        echo "$script"
        $KOALA_SHELL "$script_file"
        echo "$?"
    fi
done <<< "$teraseq_script_names"