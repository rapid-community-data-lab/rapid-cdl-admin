# rapid-cdl-admin

## Run with Docker (production-like)

The frontend ships its own self-contained Docker image (`docker/Dockerfile`)
that is completely independent of the
[`rapid-community-data-lab-api`](../rapid-community-data-lab-api) backend repo.
At runtime the image's nginx proxies `/api/*` requests to a backend whose URL
is supplied via the `BACKEND_URL` environment variable, making the same image
deployable to local Docker Compose, Kubernetes / K3s on OpenStack, and GitLab
CI/CD pipelines without rebuilding.

***If Docker container and network have been set up for backend when setting up front end Oni-ui, can skip step 1-2.***

### 1. Prerequisites

- Docker 24+ and Docker Compose v2
- The backend repo cloned at `../rapid-community-data-lab-api`
- A `configuration.json` in this directory (copy from `configuration.sample.json`).
  In `configuration.json` set the API endpoint to a relative path so requests
  flow through the nginx proxy:

   ```json
   "api": {
     "rocrate": { "endpoint": "/api", "path": "", "clientId": "am" }
   }
   ```

### 2. Create the shared Docker network (one-time)

The frontend container joins a shared **external** Docker network so that
nginx can resolve the backend by Docker DNS at the hostname `api`:

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

This builds `rapid-cdl-admin:local`, mounts the host `configuration.json` at
`/configuration.json` inside the container, and publishes nginx on host port
**8082**.

### 5. Verify

```bash
curl -s http://localhost:8082/api/version            # proxied to backend
curl -s http://localhost:8082/configuration.json     # served from host mount
open http://localhost:8082                           # SPA in browser Mac
explorer.exe http://localhost:8082                   # SPA in browser Windows WSL2
```

You should see `{"version":"1.0.0"}` from the proxied backend call.

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
