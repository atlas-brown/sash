#!/usr/bin/env bash
# Run the containerized SaSh (the `sys` image) on a host shell script,
# mounting *only* the file(s) you want to analyze into the container.
#
# The plain `docker run ... sash file.sh` invocation can only see files under a
# directory that you remember to mount. This wrapper instead mounts each
# argument that names an existing host file (read-only) into the container at
# its own absolute path, and passes everything else through to SaSh unchanged.
# That means flag ordering does not matter and the paths in SaSh's output match
# the paths you typed.
#
# Usage:
#   scripts/sash-docker.sh [SASH_OPTIONS] FILE
#
# It works with either Docker or Podman, auto-detecting whichever is installed
# (preferring Docker if both are present).
#
# Environment:
#   SASH_IMAGE        Image (tag) to run (default: sash)
#   SASH_RUNTIME      Container runtime to use (default: auto-detect docker/podman)
#   SASH_DOCKER_ARGS  Extra args for `<runtime> run`, e.g. "--privileged" for CRIU
#
# Tip: alias it for convenience, e.g.
#   alias sash='/path/to/scripts/sash-docker.sh'

set -eo pipefail

image="${SASH_IMAGE:-sash}"

# Pick a container runtime: an explicit SASH_RUNTIME wins, otherwise prefer
# docker and fall back to podman.
runtime="${SASH_RUNTIME:-}"
if [ -z "$runtime" ]; then
    if command -v docker >/dev/null 2>&1; then
        runtime=docker
    elif command -v podman >/dev/null 2>&1; then
        runtime=podman
    else
        echo "sash: no container runtime found; install docker or podman (or set SASH_RUNTIME)" >&2
        exit 1
    fi
fi

# Fail early with a helpful message if the image isn't built locally, rather
# than letting the runtime try (and fail) to pull it from a registry.
if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
    echo "sash: no local image named '$image' found." >&2
    echo "      Build it with: $runtime build --target sys -t sash ." >&2
    echo "      Or point at an existing image with: SASH_IMAGE=<name> sash ..." >&2
    exit 1
fi

docker_args=()
sash_args=()

for arg in "$@"; do
    if [ -f "$arg" ]; then
        # Resolve to an absolute path without relying on `realpath`/coreutils,
        # which are not present by default on macOS.
        dir=$(cd "$(dirname "$arg")" && pwd)
        abs="${dir}/$(basename "$arg")"
        docker_args+=(-v "${abs}:${abs}:ro")
        sash_args+=("$abs")
    else
        sash_args+=("$arg")
    fi
done

# shellcheck disable=SC2206 # intentional word-splitting of SASH_DOCKER_ARGS
extra_args=(${SASH_DOCKER_ARGS:-})

exec "$runtime" run --rm "${extra_args[@]}" "${docker_args[@]}" "$image" "${sash_args[@]}"
