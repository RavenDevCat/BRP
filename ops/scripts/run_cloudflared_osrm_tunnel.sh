#!/bin/bash
set -euo pipefail

CONFIG_PATH="${CLOUDFLARED_CONFIG_PATH:-$HOME/.cloudflared/config.yml}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-}"

if [[ -z "$CLOUDFLARED_BIN" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    CLOUDFLARED_BIN="$(command -v cloudflared)"
  elif [[ -x "$HOME/bin/cloudflared" ]]; then
    CLOUDFLARED_BIN="$HOME/bin/cloudflared"
  fi
fi

if [[ -z "$CLOUDFLARED_BIN" || ! -x "$CLOUDFLARED_BIN" ]]; then
  echo "cloudflared is not installed or not on PATH." >&2
  echo "Set CLOUDFLARED_BIN to the cloudflared executable path if it is installed elsewhere." >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "cloudflared config not found: $CONFIG_PATH" >&2
  echo "Create it from ops/cloudflared/config.example.yml and fill tunnel credentials first." >&2
  exit 1
fi

exec "$CLOUDFLARED_BIN" tunnel --config "$CONFIG_PATH" run
