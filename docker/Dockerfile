FROM node:lts-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm install

COPY . .

# Build-time API config baked into the static bundle (Vite import.meta.env).
ARG VITE_API_BASE_URL=/api
ARG VITE_API_TOKEN=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_API_TOKEN=${VITE_API_TOKEN}

RUN npm run build

# Runtime: a tiny static file server (Caddy) — no nginx, no in-container proxy.
# Caddy serves the built SPA and falls back to /index.html for client-side
# routes. All /api traffic is routed away to the backend by Traefik before it
# ever reaches this pod, so this image is purely static.
FROM caddy:2-alpine

EXPOSE 80

COPY docker/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /usr/share/caddy