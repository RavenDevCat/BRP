#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOCAL_ENV_FILE="$ROOT_DIR/ops/env/local.env"
if [ -f "$LOCAL_ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$LOCAL_ENV_FILE"
  set +a
fi

cd "$ROOT_DIR/apps/client"

DEFAULT_BRP_PYTHON="/opt/anaconda3/envs/brp/bin/python"
if [ -z "${CLIENT_PYTHON:-}" ] && [ -x "$DEFAULT_BRP_PYTHON" ]; then
  CLIENT_PYTHON="$DEFAULT_BRP_PYTHON"
else
  CLIENT_PYTHON="${CLIENT_PYTHON:-python3}"
fi
STREAMLIT_SERVER_ADDRESS="${STREAMLIT_SERVER_ADDRESS:-127.0.0.1}"
STREAMLIT_SERVER_PORT="${STREAMLIT_SERVER_PORT:-8501}"
BRP_CLIENT_CACHE_DIR="${BRP_CLIENT_CACHE_DIR:-$ROOT_DIR/apps/client/cache}"
BRP_BACKEND_CACHE_DIR="${BRP_BACKEND_CACHE_DIR:-$ROOT_DIR/apps/backend/cache}"
mkdir -p "$BRP_CLIENT_CACHE_DIR" "$BRP_BACKEND_CACHE_DIR"

BRP_CLIENT_CACHE_DIR="$BRP_CLIENT_CACHE_DIR" BRP_BACKEND_CACHE_DIR="$BRP_BACKEND_CACHE_DIR" "$CLIENT_PYTHON" -m streamlit run app.py --server.address "$STREAMLIT_SERVER_ADDRESS" --server.port "$STREAMLIT_SERVER_PORT"
