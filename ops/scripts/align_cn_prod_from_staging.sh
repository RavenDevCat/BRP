#!/usr/bin/env bash
set -euo pipefail
stage=${STAGING_ROOT:-/opt/brp/staging/app}
prod=${PROD_ROOT:-/opt/brp/prod/app}
service=${PROD_BACKEND_SERVICE:-brp-prod-backend.service}
health=${PROD_BACKEND_HEALTH_URL:-http://127.0.0.1:8000/health}
ready=${PROD_BACKEND_READY_URL:-${health%/health}/ready}
attempts=${PROD_BACKEND_HEALTH_ATTEMPTS:-15}
delay=${PROD_BACKEND_HEALTH_DELAY_SECONDS:-2}
[[ $attempts =~ ^[1-9][0-9]*$ && $delay =~ ^[0-9]+$ ]]
target=$(git -C "$stage" rev-parse HEAD)
version=$(git -C "$stage" rev-parse --short HEAD)
[[ ${1:-$version} == "$version" ]]
web="$prod/apps/web"
tool="$stage/ops/scripts/verified_web_release.py"
exec 9>"$web/.brp-deploy.lock"
flock -n 9 || { echo 'Another release is in progress' >&2; exit 1; }
cd "$prod"
[[ -z $(git status --porcelain -uno) ]]
old=$(git rev-parse HEAD)
git fetch origin main
[[ $(git rev-parse FETCH_HEAD) == "$target" ]]
git merge-base --is-ancestor "$old" "$target"
# Build and verify the staging bundle before any production code or service change.
previous=$(python3 "$tool" activate "$web" --source "$stage/apps/web/dist" --version "$version" --reader www-data)
wait_ok() {
    local url=$1
    for ((i=1;i<=attempts;i++)); do
        if [[ $url == "$ready" ]]; then
            if curl --max-time 3 -fsS "$url" | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("status") != "ready")' 2>/dev/null; then return 0; fi
        elif curl --max-time 3 -fsS "$url" >/dev/null; then return 0; fi
        sleep "$delay"
    done
    return 1
}
rollback() {
    trap - ERR INT TERM
    set +e
    python3 "$tool" restore "$web" --source "$previous" &&
        git switch --detach "$old" &&
        sudo systemctl restart "$service" &&
        wait_ok "$health" || { echo 'Rollback failed; requires operator' >&2; exit 2; }
    echo 'Previous release restored' >&2
    exit 1
}
trap rollback ERR INT TERM
git switch main
git merge --ff-only "$target"
test "$(git rev-parse HEAD)" = "$target"
sudo systemctl restart "$service"
wait_ok "$health"
wait_ok "$ready"
python3 "$tool" verify "$web" --source "$stage/apps/web/dist" --version "$version" --reader www-data
touch "$web/dist/.accepted"
trap - ERR INT TERM
if [[ ${BRP_WEB_PRUNE_RELEASES:-0} == 1 ]]; then
    python3 "$tool" prune "$web" --keep "${BRP_WEB_RELEASE_KEEP:-3}" --apply
fi
printf 'CN_PROD_HEAD=%s\nCN_PROD_DIST=ok\nCN_PROD_BACKEND=ok\n' "$version"
