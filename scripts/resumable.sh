#!/usr/bin/env bash
set -euo pipefail

checkpoint_t=1
checkpoint_dir=".resumable/sash"
image_dir="/tmp/resumable-sash-criu"

die() {
    printf 'resumable.sh: %s\n' "$*" >&2
    exit 1
}

command -v criu >/dev/null 2>&1 || die "criu is not installed"
command -v sash >/dev/null 2>&1 || die "sash is not installed"

if (($# == 0)); then
    [[ -f "$checkpoint_dir/inventory.img" ]] || die "no checkpoint found in $checkpoint_dir"
    rm -rf "$image_dir"
    mkdir -p "$image_dir"
    cp -p "$checkpoint_dir"/*.img "$image_dir"/
    exec criu restore --images-dir "$image_dir" --shell-job
fi

rm -rf "$checkpoint_dir" "$image_dir"
mkdir -p "$checkpoint_dir" "$image_dir"

sash "$@" &
sash_pid="$!"

(
    sleep "$checkpoint_t"
    if kill -0 "$sash_pid" 2>/dev/null; then
        printf 'resumable.sh: checkpointing sash pid %s into %s\n' "$sash_pid" "$checkpoint_dir" >&2
        if criu dump \
            --tree "$sash_pid" \
            --images-dir "$image_dir" \
            --leave-running \
            --shell-job \
            --file-locks
        then
            cp -p "$image_dir"/*.img "$checkpoint_dir"/
            kill -TERM "$sash_pid" 2>/dev/null || true
        else
            printf 'resumable.sh: checkpoint failed; leaving sash running\n' >&2
        fi
    fi
) &
timer_pid="$!"

set +e
wait "$sash_pid"
status="$?"
set -e

kill "$timer_pid" 2>/dev/null || true
wait "$timer_pid" 2>/dev/null || true

if [[ -f "$checkpoint_dir/inventory.img" ]]; then
    printf 'resumable.sh: checkpoint saved in %s; run ./resumable.sh with no arguments to resume\n' "$checkpoint_dir" >&2
    exit 124
fi

exit "$status"
