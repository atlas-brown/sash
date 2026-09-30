#!/usr/bin/env bash

set -eo pipefail

here=$(cd "$(dirname "$0")" && pwd)
image="${SASH_IMAGE:-sash}"

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

if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
    if ! "$runtime" pull "$image"; then
        echo "sash: no local image named '$image' found." >&2
        echo "       Build it with: $runtime build --target sys -t sash ." >&2
        echo "       Or point at an existing image with: SASH_IMAGE=<name> sash ..." >&2
        exit 1
    fi
fi

exec "$here/sash-docker.sh" "$@"
