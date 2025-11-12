#! /bin/sh

set -e # Exit on error

usage() {
    echo "Usage: $0 [-u <user>] [-h <host>] [-i <identity_file>] [-e env-file] [-f <eval_file>]"
    echo "  -u <user>            SSH username (default: from .env or EVAL_USER)"
    echo "  -h <host>            SSH host address (default: from .env or EVAL_HOST)"
    echo "  -i <identity_file>   SSH identity file (default: from .env or EVAL_IDENTITY_FILE)"
    echo "  -e <env-file>        Path to .env file (default: <repo-root>/.env)"
    echo "  -f <eval_file>       Output evaluation file, relative to the repo root (default: eval.out)"
    echo "  -t <timeout>         Per-script timeout in seconds (default: 60)"
    exit 1
}

# Handle --help
[ "$1" = "--help" ] && usage

# Read command-line arguments
while getopts "u:h:i:e:f:t:" opt; do
    case $opt in
        u) user="$OPTARG" ;;
        h) host="$OPTARG" ;;
        i) identity_file="$OPTARG" ;;
        e) env_file="$OPTARG" ;;
        f) eval_file="$OPTARG" ;;
        t) eval_timeout_sec="$OPTARG" ;;
        *) usage ;;
    esac
done

# Paths
local_root="$(git rev-parse --show-toplevel)"
remote_root="/home/kapetan/sash"

# Load .env file if any SSH parameter is missing
if [ -z "$user" ] || [ -z "$host" ] || [ -z "$identity_file" ]; then
    env_file="${env_file:-"$local_root/.env"}"
    # shellcheck disable=SC1090
    [ -f "$env_file" ] && . "$env_file"
    user="${user:-"$EVAL_USER"}"
    host="${host:-"$EVAL_HOST"}"
    identity_file="${identity_file:-"$EVAL_IDENTITY_FILE"}"
fi

# Validate SSH parameters
if [ -z "$user" ] || [ -z "$host" ] || [ -z "$identity_file" ]; then
    echo "One or more SSH configuration parameters are missing"
    echo "Use '$0 --help' for more information"
    exit 1
fi

# Evaluation parameters
eval_file="${eval_file:-"eval.out"}"
eval_timeout_sec="${eval_timeout_sec:-"60"}"

# shellcheck disable=SC2139
alias ssh="ssh -i \"$identity_file\" \"$user@$host\""
# shellcheck disable=SC2139
alias scp="scp -i \"$identity_file\""

echo "Connecting to $user@$host"

ssh "git clone sash-gh:atlas-brown/resash.git '$remote_root'" || true       # Clone the repo
ssh "git --git-dir '$remote_root/.git' pull origin master"                  # Pull latest changes
ssh "docker build -t sash -f '$remote_root/Dockerfile.new' '$remote_root'"  # Build Docker image
ssh "docker run --rm --entrypoint uv sash run pytest"                       # Run tests inside Docker
ssh "docker run --rm \
    --volume '$remote_root/results:/results' \
    --entrypoint uv \
    sash \
    run scripts/evaluation.py \
        --timeout '$eval_timeout_sec' \
        --output /results/eval.out" || true                                 # Run evaluation inside Docker
scp "$user@$host:$remote_root/results/eval.out" "$local_root/$eval_file"    # Copy results back

echo "Evaluation complete. Results saved to $local_root/$eval_file"
