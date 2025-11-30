#!/bin/bash

# Exit immediately if a command exits with a non-zero status
# set -e

cd "$(realpath "$(dirname "$0")")" || exit 1
mkdir -p hashes/small

if [ ! -d "outputs" ]; then
    echo "Directory 'outputs' does not exist"
    exit 1
fi

hash_folder="hashes/full"
generate=false
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --generate)
            generate=true
            shift
            ;;
        --small)
            hash_folder="hashes/small"
            size="small"
            shift
            ;;
        --min)
            hash_folder="hashes/min"
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
    # Directory to iterate over
    directory="outputs/$size"

    # Loop through all .out files in the directory
    for file in "$directory"/*.out; do
        # Extract the filename without the directory path and extension
        filename=$(basename "$file" .out)
        
        if should_run "$filename"; then
            # Generate SHA-256 hash
            hash=$(shasum -a 256 "$file" | awk '{ print $1 }')

            # Save the hash to a file
            echo "$hash" >"$hash_folder/$filename.hash"

            # Print the filename and hash
            echo "$hash_folder/$filename.hash $hash"
        fi
    done

    exit 0
fi

# Loop through all directories in the parent directory
for file in outputs/$size/*.out; do
    # Extract the filename without the directory path and extension
    filename=$(basename "$file" .out)
    
    if should_run "$filename"; then
        # Generate SHA-256 hash
        hash=$(shasum -a 256 "$file" | awk '{ print $1 }')

        # Save the hash to a file
        echo "$hash" > "outputs/$size/$filename.hash"

        diff "$hash_folder/$filename.hash" "outputs/$size/$filename.hash" > /dev/null
        match="$?"
        # Print the filename and hash
        echo "outputs/$size/$filename $match"
    fi
done