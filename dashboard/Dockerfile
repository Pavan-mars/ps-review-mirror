# CUBIC MARS Chicago dashboard (Vite + React SPA) -- ECS Fargate image
# Multi-stage: build the static bundle with Node, serve it with nginx.
# The SPA calls the already-live cubic-mars-dashboard-api HTTP API GW at
# runtime (VITE_API_BASE_URL) -- this container never talks to RDS/AWS
# services directly, so its IAM footprint is just "pull image, write logs".

# ---- build stage ----
FROM node:20-slim AS build
WORKDIR /app

# VITE_API_BASE_URL is a Vite *build-time* env (baked into the JS bundle,
# not read at container start) -- must be supplied as a --build-arg.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- serve stage ----
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# ONE IMAGE, MANY ENVIRONMENTS.                        05-Aug-2026
# VITE_API_BASE_URL above is still honoured as a fallback, but the
# entrypoint rewrites /config.js from the API_BASE_URL env var on every
# start, so a dev image can be promoted to UAT/prod without a rebuild.
COPY docker-entrypoint.sh /docker-entrypoint.d/40-app-config.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/40-app-config.sh \
  && chmod +x /docker-entrypoint.d/40-app-config.sh

# unprivileged listen port so the task can run as a non-root user if the
# cluster's task-definition enforces it later
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
