#!/bin/bash

SUITE_DIR="$(realpath "$(dirname "$0")")"
export SUITE_DIR
export TIMEFORMAT=%R
export LC_ALL=C

cd "$SUITE_DIR" || exit 1

scripts_inputs=(
"1;1"
"2;1"
"3;1"
"4;1"
"5;2"
"6;3"
"7;4"
"8;4"
"9;4"
"10;4"
"11;4"
"12;4"
"13;5"
"14;6"
"15;7"
"16;7"
"17;7"
"18;8"
"19;8"
"20;8"
"21;8"
# "22;8"
"23;9.1"
"24;9.2"
"25;9.3"
"26;9.4"
# "27;9.5"
"28;9.6"
"29;9.7"
"30;9.8"
"31;9.9"
"32;10"
"33;10"
"34;10"
"35;11"
"36;11"
)

suffix=""
selected_scripts=""
size="full"
while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            suffix="_30M"
            size="small"
            shift
            ;;
        --min)
            suffix="_6M"
            size="min"
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

# Set default suffix if not set
if [ -z "$suffix" ]; then
    suffix="_3G"
    size="full"
fi

echo "executing unixfun $(date)"

mkdir -p "outputs/$size"
KOALA_SHELL=${KOALA_SHELL:-bash}
export BENCHMARK_CATEGORY="unixfun"

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

for script_input in "${scripts_inputs[@]}"; do
    IFS=";" read -r -a parsed <<< "${script_input}"

    script=${parsed[0]}
    input=${parsed[1]}

    if should_run "$script"; then
        script_file="./scripts/$script.sh"
        input_file="./inputs/${input}${suffix}.txt"
        output_file="./outputs/$size/$script.out"


        BENCHMARK_SCRIPT="$(realpath "$script_file")"
        export BENCHMARK_SCRIPT

        BENCHMARK_INPUT_FILE="$(realpath "$input_file")"
        export BENCHMARK_INPUT_FILE
        
        echo "$script"
        $KOALA_SHELL "$script_file" "$input_file" > "$output_file"
        echo $?
    fi
done
