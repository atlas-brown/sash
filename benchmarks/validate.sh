#!/bin/sh

if ! yq --version > /dev/null 2>&1; then
    echo "yq is required to run this script" 2>&1
    echo "See https://mikefarah.gitbook.io/yq/" 2>&1
    exit 1
fi

red='\033[0;31m'
no_color='\033[0m'

not_integrated_dir="_not_integrated"

# Find repo root directory
root_dir=$(git rev-parse --show-toplevel)

# Ensure benchmarks directory exists
benchmarks_dir="$root_dir/benchmarks"
if [ ! -d "$benchmarks_dir" ]; then
    echo "Could not find '$benchmarks_dir'" 2>&1
    exit 1
fi

err=0

# Iterate over each benchmarks subdirectory, skipping the not integrated one
for dir in "$benchmarks_dir"/*; do
    if [ ! -d "$dir" ] || [ "$(basename "$dir")" = "$not_integrated_dir" ]; then
        continue
    fi

    # Loop over all individual benchmarks
    for bench_dir in "$dir"/*; do
        if [ ! -d "$bench_dir" ]; then
            echo "Unexpected file ${red}'$bench_dir'${no_color}" 2>&1
            err=1
            continue
        fi

        # Verify existence of required files
        for file in original.sh posix.sh fixed.sh info.yaml; do
            if [ ! -f "$bench_dir/$file" ]; then
                echo "Missing ${red}'$bench_dir/$file'${no_color}" 2>&1
                err=1
            fi
        done

        # Verify posix.sh and fixed.sh are parseable
        for file in posix.sh fixed.sh; do
            if [ -f "$bench_dir/$file" ]; then
                if ! uv run "$root_dir/scripts/try_parse.py" "$bench_dir/$file" 2>/dev/null; then
                    echo "Failed to parse ${red}'$bench_dir/$file'${no_color}" 2>&1
                    err=1
                fi
            fi
        done


        info_file="$bench_dir/info.yaml"
        if [ ! -f "$info_file" ]; then
            continue
        fi

        # Verify existence of required fields in info.yaml
        for field in bugs sources ground_truth; do
            if ! yq ".$field" "$info_file" > /dev/null 2>&1; then
                echo "Missing field ${red}'$field'${no_color} in ${red}'$info_file'${no_color}" 2>&1
                err=1
            fi
        done

        # Verify that bugs match ground truth
        sorted_bug_lines=$(yq '.bugs[].line' "$info_file" | sort)
        sorted_gt_lines=$(yq '.ground_truth.errors[].line' "$info_file" | sort)
        if [ "$sorted_bug_lines" != "$sorted_gt_lines" ]; then
            echo "Mismatch between bugs and ground truth in ${red}'$info_file'${no_color}" 2>&1
            err=1
        fi
    done
done

exit "$err"
