#!/bin/sh
# =====================================================================
# 40-app-config.sh -- runs from nginx's own /docker-entrypoint.d/ hook,
# so the official image's entrypoint and CMD are left untouched.
#
# Rewrites /config.js from the environment on every container start, so
# ONE image serves dev, UAT and prod. index.html loads /config.js as a
# classic script before the module bundle, so src/runtimeConfig.js sees
# window.__APP_CONFIG__ at import time -- no async gate in the app.
#
# If API_BASE_URL is unset the file is written empty and the bundle falls
# back to the VITE_API_BASE_URL baked at build time, so an unconfigured
# task degrades to the old behaviour rather than to a blank screen.
# =====================================================================
set -eu
CONFIG_PATH=/usr/share/nginx/html/config.js
API_BASE_URL=$(printf '%s' "${API_BASE_URL:-}" | sed 's:/*$::')

cat > "$CONFIG_PATH" <<JS
window.__APP_CONFIG__ = { apiBaseUrl: "${API_BASE_URL}" };
JS

if [ -n "$API_BASE_URL" ]; then
  echo "[app-config] API base URL: ${API_BASE_URL}"
else
  echo "[app-config] WARNING: API_BASE_URL unset -- falling back to the build-time VITE_API_BASE_URL baked into the bundle."
fi
