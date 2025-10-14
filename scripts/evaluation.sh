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

    # Input: { "errors": [ { "code": ... }, ... ] }
    # Output: [ { "code": ... }, ... ]
    expected=$(jq --sort-keys '.errors' "$(dirname "$benchmark")/ground_truth.json")

    # Input: { ..., "errors": [ { "code": ..., message: ... }, ... ] }
    # Output: [ { "code": ... }, ... ]
    actual=$(echo "$output" | jq --sort-keys '[.errors[] | {code}]' 2>/dev/null)

    if [ "$expected" != "$actual" ]; then
        echo "Unexpected output:"
        echo "$output" | jq '.errors'
        failure=$((failure + 1))
    fi
done < <(find "$bench_dir" -type f -name 'posix.sh' -o -type l -name 'posix.sh')

if [ "$failure" -ne 0 ]; then
    echo "$failure benchmarks failed"
    exit 1
fi
