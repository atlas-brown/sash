#! /bin/sh

# Run all benchmarks named posix.sh in the benchmarks directory and subdirectories
# The benchmarks directory is located by recursively searching outwards from the current directory

# Recursively search outwards for a directory called "benchmarks"
BENCHMARKS_DIR="$PWD"
while [ ! -d "$BENCHMARKS_DIR/benchmarks" ]; do
    if [ "$BENCHMARKS_DIR" = "/" ]; then
        echo "Could not find benchmarks directory"
        exit 1
    fi

    BENCHMARKS_DIR=$(dirname "$BENCHMARKS_DIR")
done

BENCHMARKS_DIR="$BENCHMARKS_DIR/benchmarks"

# Find all files or symlinks named posix.sh in the benchmarks directory and subdirectories
find "$BENCHMARKS_DIR" -type f -name 'posix.sh' -o -type l -name 'posix.sh' | while read -r benchmark; do
    echo "Running benchmark: $benchmark"
    output=$(uv run sash "$benchmark")

    # Check if the output is valid JSON
    if ! echo "$output" | jq empty; then
        echo "$output"
        continue
    fi

    # The output is a JSON object, extract and print the "error_messages" field, one element at a time
    echo "$output" | jq -r '.error_messages[]' || true
done
