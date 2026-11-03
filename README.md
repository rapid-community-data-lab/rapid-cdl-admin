# rapid-cdl-admin

> **Deploying to the K3s cluster on Nectar OpenStack?** See
> [`DEPLOYMENT.md`](DEPLOYMENT.md) — a GitOps guide: push to `main`, **GitHub
> Actions** builds + pushes the image, **Argo CD** auto-syncs it to K3s. Also
> covers connecting to OpenStack, creating a Nectar keypair, and configuring the
> shared `API_AUTH_JWT_SECRET` in both backend services.

## Run with Docker (production-like)

The frontend ships its own self-contained Docker image (`docker/Dockerfile`):
a multi-stage build that compiles the Vue SPA and serves it with **Caddy**
(static files only — no in-container proxy). The local stack in
[`docker/docker-compose.yml`](docker/docker-compose.yml) runs a small **Traefik**
edge (routes in [`docker/traefik/dynamic.yml`](docker/traefik/dynamic.yml)) that sends
`/api/*` to the portal API, `/admin-api/*` to the admin API, `/ingest-api/*` to the
[data ingester](data-ingester/README.md) and everything else to the Caddy admin —
mirroring the K3s setup, where the cluster Traefik does the same. The browser
bundle calls same-origin paths (baked at build via `VITE_API_BASE_URL=/api`,
`VITE_ADMIN_API_BASE_URL=/admin-api`, `VITE_INGEST_API_BASE_URL=/ingest-api`),
so the same image runs locally and on K3s unchanged.

The **Ingest Data** page downloads zipped RO-Crates from portal links (single or
split parts) or takes uploaded zips, then assembles, imports into OCFL and indexes
them through the data ingester, with resumable downloads and uploads.

***If the Docker network and backend are already up (e.g. from oni-ui), skip steps 1–2.***

### 1. Prerequisites

- Docker 24+ and Docker Compose v2
- The backend repo cloned at `../rapid-community-data-lab-api`
- Set the same `API_AUTH_JWT_SECRET` value in the admin API and lab-api
  environments. Users receive their bearer token from `/login`.
- The UI calls admin authentication through the same-origin `/admin-api` path.
  Local Traefik and production Kubernetes route this path to admin-api.

### 2. Create the shared Docker network (one-time)

The containers join a shared **external** Docker network so that Traefik can
resolve the backend by Docker DNS at the hostname `api`:

```bash
docker network create rapid-community-data-lab
```

Both `docker/docker-compose.yml` (this project) and
`../rapid-community-data-lab-api/docker-compose.yml` reference this network as
`external: true`, so no project owns it.

### 3. Start the backend stack

Start postgres + opensearch + api:

```bash
cd ../rapid-community-data-lab-api
docker compose up -d        # postgres + opensearch + api
curl -s http://localhost:8080/version
```

The admin API keeps its accounts in its own database (as in production), so the
portal API's Prisma schema never conflicts with the admin role enum. Create it once:

```bash
docker exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE rapid_cdl_admin"'
```

Start the admin API on the shared Docker network. `api/.env` (copy `api/env.default`)
needs `DB_NAME=rapid_cdl_admin`, `DB_USER`, `DB_PASSWORD`, `PORT=8083` and the shared
`API_AUTH_JWT_SECRET`:

```bash
cd ../rapid-cdl-admin/api
docker compose up -d --build
```

Start the data ingester (`data-ingester/.env` from `data-ingester/env.default`, with the
same `API_AUTH_JWT_SECRET`):

```bash
cd ../data-ingester
docker compose up -d --build
```

### 4. Build and start the frontend

```bash
cd ../rapid-cdl-admin
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
```

This builds `rapid-cdl-admin:local` (Caddy static SPA) and starts a local Traefik
that publishes on host port **8082**, routing `/api/*` to lab-api,
`/admin-api/*` to admin-api, `/ingest-api/*` to the data ingester, and `/` to the admin.
All routes come from the file provider, so Traefik needs no Docker socket.

### 5. Create the first admin account

A fresh admin database has no accounts. Create one (the password prompt is hidden):

```bash
docker exec -it admin-api node /app/create-admin-user.mjs
```

### 6. Verify

```bash
curl -s http://localhost:8082/api/version            # Traefik → backend
curl -s http://localhost:8082/admin-api/health       # Traefik → admin-api
curl -s http://localhost:8082/ingest-api/health      # Traefik → data ingester
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8082/   # SPA → 200
open http://localhost:8082                           # SPA in browser Mac
explorer.exe http://localhost:8082                   # SPA in browser Windows WSL2
```

You should see `{"version":"1.0.0"}` from the backend call routed through Traefik.

### 7. Tear down

```bash
docker compose -f docker/docker-compose.yml down
(cd data-ingester && docker compose down)
(cd api && docker compose down)
cd ../rapid-community-data-lab-api && docker compose down
```

Optionally remove the shared network with `docker network rm rapid-community-data-lab`.

## Developer mode for set up

```sh
npm install
```

### Compile and Hot-Reload for Development

```sh
npm run dev
```

### Type-Check, Compile and Minify for Production

```sh
npm run build
```

### Lint with [ESLint](https://eslint.org/)

```sh
npm run lint
```
