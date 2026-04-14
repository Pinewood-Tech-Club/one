#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOMEBREW_INSTALL_COMMAND='/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

log() {
  printf '%s\n' "$1" >&2
}

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
    return 0
  fi
  if [[ -x /opt/homebrew/bin/brew ]]; then
    printf '%s\n' /opt/homebrew/bin/brew
    return 0
  fi
  if [[ -x /usr/local/bin/brew ]]; then
    printf '%s\n' /usr/local/bin/brew
    return 0
  fi
  return 1
}

apply_homebrew_env() {
  local brew_bin brew_prefix
  if ! brew_bin="$(find_brew)"; then
    return 1
  fi
  brew_prefix="$("$brew_bin" --prefix)"
  export PATH="$brew_prefix/bin:$brew_prefix/sbin:$brew_prefix/opt/python@3.12/libexec/bin:$PATH"
}

ensure_python_runner() {
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi

  apply_homebrew_env >/dev/null 2>&1 || true
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  if [[ "$(uname -s)" != "Darwin" ]]; then
    log "idk bro i only use macos"
    return 1
  fi

  local brew_bin
  if ! brew_bin="$(find_brew)"; then
    log "Homebrew is required to bootstrap local tooling on macOS."
    log "- Install command: $HOMEBREW_INSTALL_COMMAND"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew_bin="$(find_brew)"
  fi

  apply_homebrew_env

  if ! command -v python3.12 >/dev/null 2>&1; then
    log "Installing python@3.12 with Homebrew..."
    "$brew_bin" install python@3.12
    apply_homebrew_env
  fi

  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  log "Unable to find a usable Python interpreter after bootstrapping."
  return 1
}

PYTHON_BIN="$(ensure_python_runner)"

exec "$PYTHON_BIN" "$ROOT_DIR/scripts/devtools.py" "$@"
