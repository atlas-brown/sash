#! /bin/sh

# Run all benchmarks named posix.sh in the benchmarks directory and subdirectories
# The benchmarks directory is located by recursively searching outwards from the current directory

top="$(git rev-parse --show-toplevel)"
cd "$top" || exit 1
bench_dir="$top/benchmarks"

# Get the first argument as the benchmark set name
if [ -n "$1" ]; then
    bench_dir="$bench_dir/$1"
fi

# Find all files or symlinks named posix.sh in the benchmarks directory and subdirectories
find "$bench_dir" -type f -name 'posix.sh' -o -type l -name 'posix.sh' | while read -r benchmark; do
    echo "Running benchmark: $benchmark"
    output=$(uv run sash "$benchmark")

    # Check if the output is valid JSON
    if ! echo "$output" | jq empty; then
        echo "$output"
        continue
    fi
    echo "$output" | jq -r '.error_messages[]' || true
done
