#!/bin/bash

TOP=$(git rev-parse --show-toplevel)
EVAL_DIR="${TOP}/repl"
REPO_PATH="${EVAL_DIR}/inputs/chromium"
COMMITS_DIR="${EVAL_DIR}/inputs/commits"

cd "$REPO_PATH" || { echo "Cannot cd into $REPO_PATH"; exit 1; }
NUM_COMMITS=21
GENERATE=false
selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --min)
            NUM_COMMITS=2
            shift
            ;;
        --small)
            NUM_COMMITS=6
            shift
            ;;
        --generate)
            GENERATE=true
            shift
            ;;
        -s|--scripts)
            shift
            while [ $# -gt 0 ] && [ "$(echo "$1" | cut -c1)" != "-" ]; do
                if [ -z "$selected_scripts" ]; then
                    selected_scripts="$1"
                else
                    selected_scripts="$selected_scripts $1"
                fi
                shift
            done
            ;;
        *)
            shift
            ;;
    esac
done

should_run() {
    script_name=$1
    if [ -z "$selected_scripts" ]; then
        return 0
    fi
    for selected in $selected_scripts; do
        if [ "$selected" = "$script_name" ]; then
            return 0
        fi
    done
    return 1
}

if should_run "git-workflow"; then
    check_commits=$((NUM_COMMITS-1))

    branch=$(git rev-parse --abbrev-ref HEAD)
    if [ "$branch" != "bench_branch" ]; then
        echo "Expected to be on 'bench_branch', but found '$branch'"
        exit 1
    fi

    base_commit_file="$COMMITS_DIR/base_commit.txt"
    if [ ! -f "$base_commit_file" ]; then
        echo "Missing base commit file at $base_commit_file"
        exit 1
    fi

    base_commit=$(cat "$base_commit_file")

    commit_count=$(git rev-list --count "$base_commit"..HEAD)
    if [ "$commit_count" -lt $check_commits ]; then
        echo "Expected at least $check_commits new commits after base commit, found $commit_count"
        exit 1
    fi
    echo git-workflow 0
fi

if should_run "vps-audit" || should_run "vps-audit-negate"; then
    if [ "$GENERATE" = true ]; then
        python3 $EVAL_DIR/utils/validate.py --generate
        exit 0
    else
        python3 $EVAL_DIR/utils/validate.py
        echo "vps-audit $?"
    fi
fi