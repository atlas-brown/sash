#! /bin/bash

# Run all benchmarks named posix.sh in the benchmarks directory and subdirectories
# The benchmarks directory is located by recursively searching outwards from the current directory

top="$(git rev-parse --show-toplevel)"
cd "$top" || exit 1
bench_dir="$top/benchmarks"

# Get the first argument as the benchmark set name
if [ -n "$1" ]; then
    bench_dir="$bench_dir/$1"
fi

failure=0

# Find all files or symlinks named posix.sh in the benchmarks directory and subdirectories
while read -r benchmark; do
    echo "Running benchmark: $benchmark"
    output=$(uv run sash "$benchmark")

    if [ ! -f "$(dirname "$benchmark")/ground_truth.json" ]; then
        # No ground truth, just print the output
        echo "$output"
        continue
    fi

    # Input: { ..., "errors": [ { "code": <code>, ... }, ... ] }
    # Output: <code> ...
    expected=$(jq '.errors[].code' "$(dirname "$benchmark")/ground_truth.json" | sort)

    # Input: { ..., "errors": [ { "code": <code>, ... }, ... ] }
    # Output: <code> ...
    actual=$(echo "$output" | jq '.errors[].code' | sort)


    if [ "$actual" != "$expected" ]; then
        echo "Unexpected output:"
        echo "$output" | jq '.errors'
        failure=$((failure + 1))
    fi
done < <(find "$bench_dir" -type f -name 'posix.sh' -o -type l -name 'posix.sh')

if [ "$failure" -ne 0 ]; then
    echo "$failure benchmarks failed"
    exit 1
fi
