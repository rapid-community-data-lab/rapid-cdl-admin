# rapid-cdl-admin

> **Deploying to the K3s cluster on Nectar OpenStack?** See
> [`DEPLOYMENT.md`](DEPLOYMENT.md) — a GitOps guide: push to `main`, **GitHub
> Actions** builds + pushes the image, **Argo CD** auto-syncs it to K3s. Also
> covers connecting to OpenStack, creating a Nectar keypair, and generating a
> secure `VITE_API_TOKEN`.

## Run with Docker (production-like)

The frontend ships its own self-contained Docker image (`docker/Dockerfile`):
a multi-stage build that compiles the Vue SPA and serves it with **Caddy**
(static files only — no in-container proxy). The local stack in
[`docker/docker-compose.yml`](docker/docker-compose.yml) runs a small **Traefik**
edge that routes `/api/*` to the backend and everything else to the Caddy admin —
mirroring the K3s setup, where the cluster Traefik does the same. The browser
bundle calls same-origin `/api/*` (baked at build via `VITE_API_BASE_URL=/api`),
so the same image runs locally and on K3s unchanged.

***If the Docker network and backend are already up (e.g. from oni-ui), skip steps 1–2.***

### 1. Prerequisites

- Docker 24+ and Docker Compose v2
- The backend repo cloned at `../rapid-community-data-lab-api`
- (Optional) export `VITE_API_TOKEN` before building so the admin bearer token is
  baked into the local bundle:

   ```bash
   export VITE_API_TOKEN='<dev-token>'
   ```

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

```bash
cd ../rapid-community-data-lab-api
docker compose up -d        # postgres + opensearch + api
curl -s http://localhost:8080/version
```

### 4. Build and start the frontend

```bash
cd ../rapid-cdl-admin
docker compose -f docker/docker-compose.yml up -d --build
```

This builds `rapid-cdl-admin:local` (Caddy static SPA) and starts a local Traefik
that publishes on host port **8082**, routing `/api/*` to the backend and `/` to
the admin.

### 5. Verify

```bash
curl -s http://localhost:8082/api/version            # Traefik → backend
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8082/   # SPA → 200
open http://localhost:8082                           # SPA in browser Mac
explorer.exe http://localhost:8082                   # SPA in browser Windows WSL2
```

You should see `{"version":"1.0.0"}` from the backend call routed through Traefik.

### 6. Tear down

```bash
docker compose -f docker/docker-compose.yml down
cd ../rapid-community-data-lab-api && docker compose down
# Optional: remove the shared network
docker network rm rapid-community-data-lab
```

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
