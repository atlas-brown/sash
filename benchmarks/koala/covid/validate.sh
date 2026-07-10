#!/bin/bash

TOP=$(git rev-parse --show-toplevel)
eval_dir="${TOP}/covid"
outputs_dir="${eval_dir}/outputs"
hashes_dir="${eval_dir}/hashes"

suffix=""
generate=false
selected_scripts=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --generate)
            generate=true
            shift
            ;;
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

# Function to check if a script should run
should_run() {
    script_num=$1
    # If no scripts specified, run all
    if [ -z "$selected_scripts" ]; then
        return 0
    fi
    # Check if script number is in selected list
    for selected in $selected_scripts; do
        if [ "$selected" = "$script_num" ]; then
            return 0
        fi
    done
    return 1
}

if $generate; then
    mkdir -p "$hashes_dir"
    # give relative paths to md5sum
    (
        cd "$outputs_dir" || exit 1
        
        temp_file=$(mktemp)
        for i in 1 2 3 4 5; do
            if should_run "$i"; then
                if [ -f "outputs$suffix/$i.out" ]; then
                    md5sum "outputs$suffix/$i.out" >> "$temp_file"
                fi
            fi
        done
        
        if [ -s "$temp_file" ]; then
            mv "$temp_file" "$hashes_dir/outputs$suffix.md5sum"
        else
            rm "$temp_file"
        fi
    )
    exit 0
fi

# give relative paths to md5sum
(
    cd "$outputs_dir" || exit 1
    
    if [ -z "$selected_scripts" ]; then
        # Validate all
        md5sum --check --quiet --status "$hashes_dir/outputs$suffix.md5sum"
        echo covid$suffix $?
    else
        # Validate only selected scripts
        all_passed=true
        for i in $selected_scripts; do
            if [ ! -f "outputs$suffix/$i.out" ]; then
                echo "Warning: outputs$suffix/$i.out not found" >&2
                all_passed=false
                continue
            fi
            
            # Extract the hash for this specific file
            expected_hash=$(grep "outputs$suffix/$i.out" "$hashes_dir/outputs$suffix.md5sum" 2>/dev/null | awk '{print $1}')
            if [ -z "$expected_hash" ]; then
                echo "Warning: no hash found for outputs$suffix/$i.out" >&2
                all_passed=false
                continue
            fi
            
            actual_hash=$(md5sum "outputs$suffix/$i.out" | awk '{print $1}')
            
            if [ "$expected_hash" != "$actual_hash" ]; then
                all_passed=false
            fi
        done
        
        if [ "$all_passed" = true ]; then
            echo covid$suffix 0
        else
            echo covid$suffix 1
        fi
    fi
)