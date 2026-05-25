#!/bin/bash
set -euo pipefail

CONFIG_PATH="${CLOUDFLARED_CONFIG_PATH:-$HOME/.cloudflared/config.yml}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed or not on PATH." >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "cloudflared config not found: $CONFIG_PATH" >&2
  echo "Create it from ops/cloudflared/config.example.yml and fill tunnel credentials first." >&2
  exit 1
fi

exec cloudflared tunnel --config "$CONFIG_PATH" run
