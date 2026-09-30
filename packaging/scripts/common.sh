# Shared helpers for the packaging scripts. Source this file, do not run it.
pkg=asash
root=$(cd "$(dirname "$0")/../.." && pwd)

upstream_version() {
    sed -n 's/^version = "\(.*\)"/\1/p' "$root/pyproject.toml" | head -n 1
}

stage_tree() {
    [ -d "$root/vendor" ] || "$root/packaging/scripts/vendor.sh"
    mkdir -p "$1"
    git -C "$root" archive HEAD | tar -x -C "$1"
    rm -rf "$1/benchmarks" "$1/results" "$1/tests" "$1/scripts" \
        "$1/packaging" "$1/.github" "$1/.devcontainer"
    cp -R "$root/vendor" "$1/vendor"
}
