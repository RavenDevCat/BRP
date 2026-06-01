#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  sudo SITE_NAME=brp-staging \
    APP_ROOT=/opt/brp/staging/app \
    FRONTEND_PORT=8501 \
    BACKEND_URL=http://127.0.0.1:8001 \
    SERVER_NAMES="staging.example.com" \
    ops/scripts/install_nginx_react_site.sh

Installs an Nginx server block that serves apps/web/dist with SPA fallback and
proxies /api and /api/* to the backend. If APP_ROOT/ops/env/local.env defines
BRP_BACKEND_SERVICE_TOKEN, the generated Nginx include injects it server-side.
EOF
}

if [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root because this writes /etc/nginx." >&2
  exit 1
fi

SITE_NAME="${SITE_NAME:-}"
APP_ROOT="${APP_ROOT:-}"
FRONTEND_PORT="${FRONTEND_PORT:-}"
BACKEND_URL="${BACKEND_URL:-}"
SERVER_NAMES="${SERVER_NAMES:-}"
DIST_DIR="${DIST_DIR:-}"
REQUIRE_ACCESS="${REQUIRE_ACCESS:-true}"

if [ -z "$SITE_NAME" ] || [ -z "$APP_ROOT" ] || [ -z "$FRONTEND_PORT" ] || [ -z "$BACKEND_URL" ] || [ -z "$SERVER_NAMES" ]; then
  usage
  exit 2
fi

if [ -z "$DIST_DIR" ]; then
  DIST_DIR="$APP_ROOT/apps/web/dist"
fi

LOCAL_ENV_FILE="$APP_ROOT/ops/env/local.env"
if [ -f "$LOCAL_ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$LOCAL_ENV_FILE"
  set +a
fi

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "React build not found: $DIST_DIR/index.html" >&2
  exit 3
fi

if ! command -v nginx >/dev/null 2>&1; then
  echo "nginx is not installed." >&2
  exit 4
fi

install -d -m 0755 /etc/nginx/brp /etc/nginx/sites-available /etc/nginx/sites-enabled

cat > /etc/nginx/brp/proxy-common.conf <<'EOF'
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Cf-Access-Authenticated-User-Email $http_cf_access_authenticated_user_email;
proxy_set_header X-BRP-User-Email $http_cf_access_authenticated_user_email;
proxy_set_header Connection "";
proxy_request_buffering off;
proxy_read_timeout 1800s;
proxy_send_timeout 1800s;
proxy_redirect off;
EOF

TOKEN_INCLUDE="/etc/nginx/brp/${SITE_NAME}-backend-token.conf"
if [ -n "${BRP_BACKEND_SERVICE_TOKEN:-}" ]; then
  umask 077
  printf 'proxy_set_header Authorization "Bearer %s";\n' "$BRP_BACKEND_SERVICE_TOKEN" > "$TOKEN_INCLUDE"
else
  : > "$TOKEN_INCLUDE"
fi
chmod 0600 "$TOKEN_INCLUDE"

ACCESS_GUARD=""
case "${REQUIRE_ACCESS,,}" in
  1|true|yes|on)
    ACCESS_GUARD='    if ($http_cf_access_authenticated_user_email = "") {
        return 401 "Cloudflare Access authentication is required.\n";
    }'
    ;;
  0|false|no|off)
    ACCESS_GUARD=""
    ;;
  *)
    echo "REQUIRE_ACCESS must be true or false." >&2
    exit 5
    ;;
esac

SITE_CONF="/etc/nginx/sites-available/${SITE_NAME}.conf"
cat > "$SITE_CONF" <<EOF
server {
    listen 127.0.0.1:${FRONTEND_PORT};
    server_name ${SERVER_NAMES};

    root ${DIST_DIR};
    index index.html;
    client_max_body_size 100m;

${ACCESS_GUARD}

    location = /api {
        proxy_pass ${BACKEND_URL};
        include /etc/nginx/brp/proxy-common.conf;
        include ${TOKEN_INCLUDE};
    }

    location /api/ {
        proxy_pass ${BACKEND_URL};
        include /etc/nginx/brp/proxy-common.conf;
        include ${TOKEN_INCLUDE};
    }

    location /assets/ {
        try_files \$uri =404;
        access_log off;
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
        add_header Cache-Control "no-cache" always;
    }
}
EOF

ln -sfn "$SITE_CONF" "/etc/nginx/sites-enabled/${SITE_NAME}.conf"
nginx -t

