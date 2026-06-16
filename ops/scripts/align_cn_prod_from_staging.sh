#!/usr/bin/env bash
set -euo pipefail

STAGING_ROOT="${STAGING_ROOT:-/opt/brp/staging/app}"
PROD_ROOT="${PROD_ROOT:-/opt/brp/prod/app}"
PROD_BACKEND_SERVICE="${PROD_BACKEND_SERVICE:-brp-prod-backend.service}"
TARGET_HEAD="${1:-}"

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
sleep 5
curl -fsS http://127.0.0.1:8000/health >/dev/null

echo "CN_PROD_HEAD=$prod_head"
echo "CN_PROD_DIST=ok"
echo "CN_PROD_BACKEND=ok"
