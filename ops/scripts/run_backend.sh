#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR/apps/backend"

BACKEND_PYTHON="${BACKEND_PYTHON:-/Users/alexus/opt/anaconda3/envs/ortools-env/bin/python}"

"$BACKEND_PYTHON" backend_service.py
