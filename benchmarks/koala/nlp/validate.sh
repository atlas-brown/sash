#!/bin/bash

# Exit immediately if a command exits with a non-zero status
# set -e

cd "$(realpath "$(dirname "$0")")" || exit 1

hash_folder="hashes"

generate=false
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --generate)
            generate=true
            shift
            ;;
        --min)
            hash_folder="hashes/min"
            shift
            ;;
        --small)
            hash_folder="hashes/small"
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

mkdir -p "$hash_folder"

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

if $generate; then
    for dir in outputs/*/; do
        script=$(basename "$dir")
        if should_run "$script"; then
            out="$hash_folder/$script.hashes"
            : > "$out"
            find "$dir" -type f | grep -v '\.hash$' | sort | while read -r f; do
                rel=${f#outputs/}
                printf '%s  %s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$rel" >> "$out"
            done
        fi
    done
    exit
fi

mismatch=0
for dir in outputs/*/; do
    script=$(basename "$dir")
    if should_run "$script"; then
        ref="$hash_folder/$script.hashes"
        if [ ! -f "$ref" ]; then
            echo "$script missing reference"
            mismatch=1
            continue
        fi
        tmp=$(mktemp)
        find "$dir" -type f | grep -v '\.hash$' | sort | while read -r f; do
            rel=${f#outputs/}
            printf '%s  %s\n' "$(shasum -a 256 "$f" | awk '{print $1}')" "$rel" >> "$tmp"
        done
        if ! diff -q "$ref" "$tmp" > /dev/null; then
            echo "Mismatch in $script:"
            diff -u "$ref" "$tmp"
            mismatch=1
        fi
        rm -f "$tmp"
    fi
done

echo "nlp $mismatch"