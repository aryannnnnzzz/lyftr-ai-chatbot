#!/bin/sh
# Writes the runtime config consumed by index.html, so the API URL comes from an env var.
set -eu
cat > /usr/share/nginx/html/config.js <<EOF
window.__APP_CONFIG__ = { API_BASE_URL: "${API_BASE_URL:-}" };
EOF
echo "runtime config: API_BASE_URL=${API_BASE_URL:-<unset>}"
