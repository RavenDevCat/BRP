#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR/apps/client"

CLIENT_PYTHON="${CLIENT_PYTHON:-python3}"
STREAMLIT_SERVER_ADDRESS="${STREAMLIT_SERVER_ADDRESS:-127.0.0.1}"
STREAMLIT_SERVER_PORT="${STREAMLIT_SERVER_PORT:-8501}"

"$CLIENT_PYTHON" -m streamlit run app.py --server.address "$STREAMLIT_SERVER_ADDRESS" --server.port "$STREAMLIT_SERVER_PORT"
