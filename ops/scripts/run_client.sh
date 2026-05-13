#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR/apps/client"

CLIENT_PYTHON="${CLIENT_PYTHON:-/Users/developer/opt/anaconda3/envs/ortools-env/bin/python}"

"$CLIENT_PYTHON" -m streamlit run app.py
