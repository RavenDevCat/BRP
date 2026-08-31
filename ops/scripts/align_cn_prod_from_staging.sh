#!/usr/bin/env bash
set -euo pipefail

STAGING_ROOT="${STAGING_ROOT:-/opt/brp/staging/app}"
PROD_ROOT="${PROD_ROOT:-/opt/brp/prod/app}"
PROD_BACKEND_SERVICE="${PROD_BACKEND_SERVICE:-brp-prod-backend.service}"
PROD_BACKEND_HEALTH_URL="${PROD_BACKEND_HEALTH_URL:-http://127.0.0.1:8000/health}"
PROD_BACKEND_HEALTH_ATTEMPTS="${PROD_BACKEND_HEALTH_ATTEMPTS:-15}"
PROD_BACKEND_HEALTH_DELAY_SECONDS="${PROD_BACKEND_HEALTH_DELAY_SECONDS:-2}"
TARGET_HEAD="${1:-}"

wait_for_backend_health() {
  local attempt

  case "$PROD_BACKEND_HEALTH_ATTEMPTS" in
    ''|*[!0-9]*|0)
      echo "PROD_BACKEND_HEALTH_ATTEMPTS must be a positive integer." >&2
      return 1
      ;;
  esac
  case "$PROD_BACKEND_HEALTH_DELAY_SECONDS" in
    ''|*[!0-9]*)
      echo "PROD_BACKEND_HEALTH_DELAY_SECONDS must be a non-negative integer." >&2
      return 1
      ;;
  esac

  for ((attempt = 1; attempt <= PROD_BACKEND_HEALTH_ATTEMPTS; attempt++)); do
    if curl -fsS "$PROD_BACKEND_HEALTH_URL" >/dev/null 2>&1; then
      echo "CN_PROD_BACKEND_READY_ATTEMPT=$attempt"
      return 0
    fi
    if [ "$attempt" -lt "$PROD_BACKEND_HEALTH_ATTEMPTS" ]; then
      sleep "$PROD_BACKEND_HEALTH_DELAY_SECONDS"
    fi
  done

  echo "CN production backend did not become healthy after $PROD_BACKEND_HEALTH_ATTEMPTS attempts." >&2
  sudo systemctl status "$PROD_BACKEND_SERVICE" --no-pager -l >&2 || true
  return 1
}

if [ -z "$TARGET_HEAD" ]; then
  TARGET_HEAD="$(git -C "$STAGING_ROOT" rev-parse --short HEAD)"
fi

staging_head="$(git -C "$STAGING_ROOT" rev-parse --short HEAD)"
if [ "$staging_head" != "$TARGET_HEAD" ]; then
  echo "Staging head $staging_head does not match target $TARGET_HEAD" >&2
  exit 1
fi

if ! grep -R "$TARGET_HEAD" -n "$STAGING_ROOT/apps/web/dist/assets"/index-*.js >/dev/null; then
  echo "Staging dist does not contain version marker $TARGET_HEAD." >&2
  echo "Build frontend on CN staging first, then rerun this script." >&2
  exit 1
fi

cd "$PROD_ROOT"
git fetch origin main
git checkout main
git pull --ff-only origin main

prod_head="$(git rev-parse --short HEAD)"
if [ "$prod_head" != "$TARGET_HEAD" ]; then
  echo "Prod head $prod_head does not match target $TARGET_HEAD" >&2
  exit 1
fi

web_dir="$PROD_ROOT/apps/web"
dist="$web_dir/dist"
new_dist="$web_dir/dist.new-$TARGET_HEAD"
backup="$web_dir/dist.prev-prod-$TARGET_HEAD-$(date +%Y%m%d%H%M%S)"

rm -rf "$new_dist"
mkdir -p "$web_dir"
cp -a "$STAGING_ROOT/apps/web/dist" "$new_dist"

if [ -d "$dist/assets" ] && [ -d "$new_dist/assets" ]; then
  cp -an "$dist/assets/." "$new_dist/assets/" || true
fi

if [ -d "$dist" ]; then
  mv "$dist" "$backup"
fi
mv "$new_dist" "$dist"

grep -R "$TARGET_HEAD" -n "$dist/assets"/index-*.js >/dev/null

sudo systemctl restart "$PROD_BACKEND_SERVICE"
wait_for_backend_health

echo "CN_PROD_HEAD=$prod_head"
echo "CN_PROD_DIST=ok"
echo "CN_PROD_BACKEND=ok"
