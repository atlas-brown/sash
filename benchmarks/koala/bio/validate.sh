#!/bin/bash

# Exit immediately if a command exits with a non-zero status
# set -e

cd "$(realpath "$(dirname "$0")")" || exit 1

hash_folder="hashes/full"
directory="outputs"
tseq_output="outputs/teraseq"

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
            size=min
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
    if should_run "bio"; then
        for file in "$directory"/*.bam; do
            filename=$(basename "$file" .bam)
            hash=$(shasum -a 256 "$file" | awk '{ print $1 }')
            echo "$hash" > "$hash_folder/$filename.hash"
            echo "$hash_folder/$filename.hash $hash"
        done
    fi

    if [ "$size" != "min" ]; then
        teraseq_scripts="data run_dRNASeq run_5TERA"
        should_run_any=false
        for script in $teraseq_scripts; do
            if should_run "$script"; then
                should_run_any=true
                break
            fi
        done

        if [ "$should_run_any" = true ]; then
            find "$tseq_output" -type f | sort | xargs md5sum > "$hash_folder/tseq_output.hashes"
            cat "$hash_folder/tseq_output.hashes"
        fi
    fi

    exit 0
fi

# Loop through all .bam files in the current directory
if should_run "bio"; then
    for file in "$directory"/*.bam; do
        # Extract the filename without the directory path and extension
        filename=$(basename "$file" .bam)

        if [ ! -f "$hash_folder/$filename.hash" ]; then
            echo "Error: Hash file for $filename does not exist in $hash_folder."
            echo "Please generate the hash files first using --generate option."
        fi

        # Compare the hash with the hash in the hashes directory
        current_hash=$(shasum -a 256 "$file" | awk '{ print $1 }')
        stored_hash=$(cat "$hash_folder/$filename.hash")

        if [ "$current_hash" = "$stored_hash" ]; then
            match=0
        else
            match=1
        fi

        # Print the filename and match
        echo "$hash_folder/$filename $match"
    done
fi

if [ "$size" = "min" ]; then
    exit 0
fi

teraseq_scripts="data run_dRNASeq run_5TERA"
should_run_any=false
for script in $teraseq_scripts; do
    if should_run "$script"; then
        should_run_any=true
        break
    fi
done

if [ "$should_run_any" = true ]; then
    mismatch=0
    tmpfile=$(mktemp)
    find "$tseq_output" -type f | sort | xargs md5sum > "$tmpfile"
    if ! diff -q "$hash_folder/tseq_output.hashes" "$tmpfile" > /dev/null; then
        diff -u "$hash_folder/tseq_output.hashes" "$tmpfile"
        mismatch=1
    fi
    rm -f "$tmpfile"

    echo "teraseq $mismatch"
fi