#!/bin/bash

TOP=$(realpath "$(dirname "$0")")

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

IN="$TOP/inputs/packages.$size"

KOALA_SHELL="${KOALA_SHELL:-bash}"
export BENCHMARK_CATEGORY="pkg"

SUITE_DIR="$(realpath "$(dirname "$0")")"
export SUITE_DIR

export TIMEFORMAT=%R
cd "$SUITE_DIR" || exit 1

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

if should_run "pacaur"; then
    script_file="$TOP/scripts/pacaur.sh"

    BENCHMARK_SCRIPT="$(realpath "$script_file")"
    export BENCHMARK_SCRIPT

    BENCHMARK_INPUT_FILE="$(realpath "$IN")"
    export BENCHMARK_INPUT_FILE

    echo "pacaur.sh"
    OUT="$TOP/outputs/aurpkg.$size"
    mkdir -p "${OUT}"
    if [ "$EUID" -eq 0 ]; then
      if ! id "user" &>/dev/null; then
        echo "Creating user 'user'..."
        useradd -m user
      fi

      echo "Running script as 'user'..."
      chown -R user:user "$OUT"
      $KOALA_SHELL "$script_file" "$IN" "$OUT"

    else
      echo "Not root, running script..."
      $KOALA_SHELL "$script_file" "$IN" "$OUT"
    fi

    echo "$?"
fi

if should_run "proginf"; then
    export INDEX="$TOP/inputs/index.$size.txt"

    script_file="$TOP/scripts/proginf.sh"
    export BENCHMARK_SCRIPT=$(realpath "$script_file")
    export BENCHMARK_INPUT_FILE="$TOP/inputs/node_modules"

    echo "proginf.sh"
    $KOALA_SHELL "$script_file"
    echo "$?"
fi