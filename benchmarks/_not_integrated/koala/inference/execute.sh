#!/bin/bash

KOALA_SHELL=${KOALA_SHELL:-bash}
TOP=$(git rev-parse --show-toplevel)
eval_dir="$TOP/inference"
scripts_dir="$eval_dir/scripts"
input_dir="$eval_dir/inputs"
outputs_dir="$eval_dir/outputs"

suffix=".full"
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            suffix=".small"
            shift
            ;;
        --min)
            suffix=".min"
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

export LC_ALL=C
export BENCHMARK_CATEGORY="inference"

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

if should_run "dpt"; then
    echo "dpt"
    outputs_dir="${eval_dir}/outputs"
    mkdir -p "$outputs_dir"
    img_input="${input_dir}/dpt$suffix"

    export BENCHMARK_INPUT_FILE="$img_input"
    export BENCHMARK_SCRIPT="$(realpath "$scripts_dir/dpt_seq.sh")"
    $KOALA_SHELL "$scripts_dir/dpt_seq.sh" "$img_input" "$outputs_dir/dpt_output$suffix.txt"
    echo "$?"
fi

if should_run "image-annotation"; then
    echo "image-annotation"
    img_input_dir="$input_dir/jpg$suffix"
    img_outputs_dir="$outputs_dir/jpg$suffix"
    mkdir -p "$img_outputs_dir"

    BENCHMARK_INPUT_FILE="$(realpath "$img_input_dir")"
    export BENCHMARK_INPUT_FILE

    export BENCHMARK_SCRIPT="$(realpath "$scripts_dir/image-annotation.sh")"
    $KOALA_SHELL "$scripts_dir/image-annotation.sh" "$img_input_dir" "$img_outputs_dir"
    echo $?
fi

if should_run "playlist-creation"; then
    echo "playlist-creation"
    songs_input_dir="$input_dir/songs$suffix"
    songs_outputs_dir="$outputs_dir/songs$suffix"
    mkdir -p "$songs_outputs_dir"

    BENCHMARK_INPUT_FILE="$(realpath "$songs_input_dir")"
    export BENCHMARK_INPUT_FILE

    export BENCHMARK_SCRIPT="$(realpath "$scripts_dir/playlist-creation.sh")"
    $KOALA_SHELL "$scripts_dir/playlist-creation.sh" "$songs_input_dir" "$songs_outputs_dir"
    echo $?
fi