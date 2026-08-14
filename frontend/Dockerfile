# syntax=docker/dockerfile:1.7
FROM node:20-bookworm-slim AS builder
WORKDIR /build
COPY package*.json ./
RUN npm ci
COPY . .
# Build-time config. Vite inlines these into the bundle, so ONLY public,
# non-secret values belong here — never AZURE_AD_CLIENT_SECRET, a JWT
# secret, a DB password, the plot-password pepper, or any API key.
# VITE_API_BASE_URL left empty ⇒ same-origin (see src/api/client.ts).
# VITE_PUBLIC_APP_URL / VITE_AUTH_SCOPE added round 8-16A: both are read at
# runtime by the app (plot-qr.ts, AuthSettings.tsx) and were previously
# missing here, so a container build silently lost them.
ARG VITE_APP_NAME VITE_API_BASE_URL VITE_PUBLIC_APP_URL VITE_AUTH_SCOPE=both VITE_AZURE_AD_TENANT_ID VITE_AZURE_AD_CLIENT_ID VITE_AZURE_AD_REDIRECT_URI VITE_DEFAULT_LANGUAGE=th
ENV VITE_APP_NAME=$VITE_APP_NAME VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_PUBLIC_APP_URL=$VITE_PUBLIC_APP_URL VITE_AUTH_SCOPE=$VITE_AUTH_SCOPE VITE_AZURE_AD_TENANT_ID=$VITE_AZURE_AD_TENANT_ID VITE_AZURE_AD_CLIENT_ID=$VITE_AZURE_AD_CLIENT_ID VITE_AZURE_AD_REDIRECT_URI=$VITE_AZURE_AD_REDIRECT_URI VITE_DEFAULT_LANGUAGE=$VITE_DEFAULT_LANGUAGE
RUN npm run build

# nginxinc/nginx-unprivileged ships a non-root nginx that listens on 8080
# by default — no USER directive needed, no root master process (audit #6).
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html
EXPOSE 8080
# 127.0.0.1, not localhost: nginx binds 0.0.0.0:8080 (IPv4 only) while
# `localhost` resolves to ::1 in this image, so a localhost probe is
# refused and the container reports unhealthy while actually serving fine.
# Found round 8-16A, the first time this container was ever run.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q --spider http://127.0.0.1:8080/health || exit 1
CMD ["nginx", "-g", "daemon off;"]
