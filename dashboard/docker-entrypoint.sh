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
# MUTATION_TOKEN: the API's write-route token (x-cubic-token). Runtime only,
# never baked. Set it on the task definition from the same value as the
# dashboard-api function. Empty = header omitted by the bundle.
MUTATION_TOKEN=$(printf '%s' "${MUTATION_TOKEN:-}" | tr -d '"\\')

cat > "$CONFIG_PATH" <<JS
window.__APP_CONFIG__ = { apiBaseUrl: "${API_BASE_URL}", mutationToken: "${MUTATION_TOKEN}" };
JS

if [ -n "$API_BASE_URL" ]; then
  echo "[app-config] API base URL: ${API_BASE_URL}"
else
  echo "[app-config] WARNING: API_BASE_URL unset -- falling back to the build-time VITE_API_BASE_URL baked into the bundle."
fi
if [ -n "$MUTATION_TOKEN" ]; then
  echo "[app-config] write-route token: present (${#MUTATION_TOKEN} chars)"
else
  echo "[app-config] write-route token: not set -- POST routes will be refused if the API requires one."
fi
