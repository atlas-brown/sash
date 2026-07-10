#!/bin/bash

cd "$(realpath "$(dirname "$0")")" || exit 1

[ ! -d "outputs" ] && echo "Directory 'outputs' does not exist" && exit 1

size="full"
generate=false
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --generate)
            generate=true
            shift
            ;;
        --small)
            size="small"
            shift
            ;;
        --min)
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

hash_folder="hashes/$size"
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
    output_folder="outputs"
    if should_run "proginf"; then
        find "$output_folder" -type f ! -path "$output_folder/aurpkg.$size/*" | sort | xargs md5sum > "$hash_folder/outputs.hashes"
    fi
    exit 0
fi

if should_run "pacaur"; then
    directory="outputs/aurpkg.$size"
    input="inputs/packages.$size"

    missing=0

    while IFS= read -r pkg || [ -n "$pkg" ]; do
        file="$directory/$pkg.txt"
        if [ ! -f "$file" ]; then
            missing=$((missing + 1))
            continue
        fi
        if ! grep -q "Finished making" "$file"; then
            missing=$((missing + 1))
        fi
    done < "$input"

    if [ "$missing" -eq 0 ]; then
        echo "aurpkg 0"
    else
        echo "aurpkg 1"
    fi
fi

if should_run "proginf"; then
    output_folder="outputs"

    mismatch=0
    tmpfile=$(mktemp)
    find "$output_folder" -type f ! -path "$output_folder/aurpkg.$size/*" | sort | xargs md5sum > "$tmpfile"
    if ! diff -q "$hash_folder/outputs.hashes" "$tmpfile" > /dev/null; then
        diff -u "$hash_folder/outputs.hashes" "$tmpfile"
        mismatch=1
    fi
    rm -f "$tmpfile"

    echo "prog-inf $mismatch"
fi