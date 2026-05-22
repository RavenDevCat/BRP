#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR/apps/backend"

BACKEND_PYTHON="${BACKEND_PYTHON:-/Users/developer/opt/anaconda3/envs/ortools-env/bin/python}"
BRP_BACKEND_HOST="${BRP_BACKEND_HOST:-127.0.0.1}"
BRP_BACKEND_PORT="${BRP_BACKEND_PORT:-8001}"

BRP_BACKEND_HOST="$BRP_BACKEND_HOST" BRP_BACKEND_PORT="$BRP_BACKEND_PORT" "$BACKEND_PYTHON" backend_service.py
