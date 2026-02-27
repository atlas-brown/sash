#!/usr/bin/env bash

top=$(git rev-parse --show-toplevel 2>/dev/null)
cd "${top}" || exit 1

if [[ ! -f "pyproject.toml" ]]; then
  echo "Run this script from the repository root (missing pyproject.toml)." >&2
  exit 1
fi

if [[ ! -f "/etc/os-release" ]] || ! grep -qi "ubuntu" /etc/os-release; then
  echo "This script is intended for Ubuntu." >&2
  exit 1
fi

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
elif [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  echo "This script requires sudo (or root)." >&2
  exit 1
fi

echo "==> Installing Ubuntu packages"
${SUDO} apt-get update
${SUDO} apt-get install -y \
  autoconf \
  automake \
  build-essential \
  ca-certificates \
  cloc \
  curl \
  git \
  jq \
  libtool \
  pkg-config \
  python3 \
  python3-dev \
  python3-pip \
  python3-venv \
  shellcheck \
  wget

if ! command -v shfmt >/dev/null 2>&1; then
  echo "==> Installing shfmt"
  if ${SUDO} apt-get install -y shfmt; then
    :
  else
    SHFMT_VERSION="v3.10.0"
    ARCH="$(uname -m)"
    case "${ARCH}" in
      x86_64) SHFMT_ARCH="amd64" ;;
      aarch64|arm64) SHFMT_ARCH="arm64" ;;
      *)
        echo "Unsupported architecture for shfmt fallback: ${ARCH}" >&2
        exit 1
        ;;
    esac
    tmp="$(mktemp)"
    curl -fsSL \
      "https://github.com/mvdan/sh/releases/download/${SHFMT_VERSION}/shfmt_${SHFMT_VERSION}_linux_${SHFMT_ARCH}" \
      -o "${tmp}"
    chmod +x "${tmp}"
    ${SUDO} mv "${tmp}" /usr/local/bin/shfmt
  fi
fi

echo "==> Creating virtual environment (.venv)"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing Python tooling and project dependencies"
python -m pip install --upgrade pip setuptools wheel
python -m pip install --upgrade uv
uv sync --dev

echo
echo "Setup complete."
echo "Activate with: source .venv/bin/activate"
