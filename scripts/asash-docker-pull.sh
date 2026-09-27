#!/usr/bin/env bash

set -eo pipefail

here=$(cd "$(dirname "$0")" && pwd)
image="${ASASH_IMAGE:-sash}"

runtime="${ASASH_RUNTIME:-}"
if [ -z "$runtime" ]; then
    if command -v docker >/dev/null 2>&1; then
        runtime=docker
    elif command -v podman >/dev/null 2>&1; then
        runtime=podman
    else
        echo "asash: no container runtime found; install docker or podman (or set ASASH_RUNTIME)" >&2
        exit 1
    fi
fi

if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
    if ! "$runtime" pull "$image"; then
        echo "asash: no local image named '$image' found." >&2
        echo "       Build it with: $runtime build --target sys -t sash ." >&2
        echo "       Or point at an existing image with: ASASH_IMAGE=<name> asash ..." >&2
        exit 1
    fi
fi

exec "$here/asash-docker.sh" "$@"
