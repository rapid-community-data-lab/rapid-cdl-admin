# rapid-cdl-data-ingester

Python (FastAPI) service behind the **Ingest Data** page of rapid-cdl-admin. It turns
zipped RO-Crates into live collections on the RAPID-CDL portal:

```
links or uploads ─▶ fetch (resumable) ─▶ assemble ─▶ extract ─▶ prepare crate ─▶ OCFL import ─▶ index ─▶ clean up
```

| Step | What happens |
|------|--------------|
| Fetch | Links are downloaded with HTTP `Range` resume, retry with back-off, and automatic resume after a restart. Uploads arrive through a chunked, offset-checked protocol and resume after interruptions or a page reload (same file = same fingerprint). |
| Assemble | Detects a single zip, several standalone zips (merged into one crate folder), raw byte-split parts (`.zip.001`, `split -b`; re-ordered by part number when needed) and Info-ZIP/WinZip spanned sets (`.z01 … .zip`, joined in Python with a rewritten central directory). |
| Extract | Safe extraction (no absolute paths, `..`, symlinks or macOS `__MACOSX`/`._*` files). *Extracted text only* keeps the metadata plus `text/*` and `ds:extractedText` files. |
| Prepare | Generalises `rapid-community-data-lab-api/data-ingestion/prepare_*.py`: a `./` root becomes `arcp://name,<identifier>`, `#slug` members become `arcp://name,<identifier>/<slug>`, LDaC Collection/Object profiles are added, the descriptor `about` is updated, and missing files/licences are reported. |
| Import | Writes OCFL 1.1 objects with the `000N-path-direct-storage-layout` used by the API (`@ocfl/ocfl-fs`). An existing collection receives a new version (`v2`, `v3`, …) whose state is exactly the new crate; unchanged content is de-duplicated. |
| Index | Calls `POST /admin/index/<id>?force=true` on the portal API with a short-lived service JWT and waits until the root, every object and every member count match the crate. Full-text search indexing finishes in the API shortly afterwards. |

The identifier is derived from the crate (an existing `arcp://` root, otherwise the single
payload folder, e.g. `alrc`) unless one is entered in the form. Re-ingesting a crate with the
same identifier updates the existing records.

## HTTP API

All routes except `/health` need `Authorization: Bearer <admin login token>` (HS256, roles
`INDEX_ADMIN`, `ROLES_ADMIN`, `ADMIN` or `SUPER_ADMIN`). Behind Traefik the prefix is `/ingest-api`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness |
| GET | `/status` | Disk headroom, queue, portal API and OCFL readiness, upload limits |
| GET | `/collections` | OCFL objects with head version and file count |
| GET / POST | `/jobs` | List jobs / create `{source: "url"\|"upload", urls[], uploadIds[], name?, payload: "all"\|"text", index}` |
| GET / DELETE | `/jobs/{id}` | Job detail with progress and log / remove job and its working files |
| POST | `/jobs/{id}/pause` · `/resume` · `/reindex` | Job control |
| GET / POST | `/uploads` | List / open (or re-open) an upload session `{filename, size, fingerprint}` |
| GET / PATCH / DELETE | `/uploads/{id}` | Offset, append a chunk (`Upload-Offset` header), discard |

## Configuration

| Variable | Default | Notes |
|----------|---------|-------|
| `API_AUTH_JWT_SECRET` | — | Same secret as the admin API and the portal API |
| `LAB_API_URL` | `http://rapid-community-data-lab-api:8080` | Portal API base URL |
| `LAB_API_ADMIN_ROLES` | `ADMIN,INDEX_ADMIN` | Roles tried for the service token, in order |
| `OCFL_PATH` | `/data/ocfl/data` | OCFL storage root shared with the portal API |
| `INGEST_WORK_DIR` | `/data/ocfl/ingest` | Downloads, uploads, extraction; keep it on the same filesystem as `OCFL_PATH` so imports are hard links |
| `INGEST_STATE_DIR` | work dir | SQLite job/upload state |
| `MIN_FREE_BYTES` / `MIN_FREE_PERCENT` | 4 GiB / 15 | Space that must stay free; larger value wins |
| `DOWNLOAD_CONCURRENCY` | 2 | Parallel part downloads |
| `DOWNLOAD_RETRIES` | 30 | Attempts without progress before a job fails (it can still be resumed) |
| `UPLOAD_CHUNK_SIZE` / `MAX_UPLOAD_BYTES` | 8 MiB / 50 GiB | Upload protocol limits |
| `UPLOAD_TTL_HOURS` | 72 | Unattached partial uploads are removed after this |
| `KEEP_ARCHIVES` | false | Keep downloaded zips after extraction |
| `ALLOWED_DOWNLOAD_HOSTS` | empty | Optional host allow-list (suffix match) |
| `ALLOW_PRIVATE_URLS` / `ALLOW_HTTP` | false / false | Private addresses and plain http are refused by default |
| `INDEX_TIMEOUT` | 3600 | Seconds to wait for the portal to finish indexing |
| `CORS_ORIGINS` | empty | Only needed for `npm run dev` against the service directly |

## Run locally

Prerequisites: the shared Docker network, the portal API stack from
`../../rapid-community-data-lab-api`, and the admin API (see the main README).

```bash
cp env.default .env            # then set API_AUTH_JWT_SECRET to the shared value
docker compose up -d --build   # http://localhost:8084/health
```

The container mounts `../../rapid-community-data-lab-api/.ocfl` (the portal API's OCFL volume)
and keeps its work files under `.ocfl/scratch/data-ingester`. The admin website reaches it at
`http://localhost:8082/ingest-api`.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The suite covers crate normalisation, OCFL create/update/de-duplication, every archive layout,
resumable downloads against a flaky server, the upload protocol, and full pipelines against
a stub portal API.

## Production

Kubernetes manifests live in the `kubernetes-cluster` repository
(`clusters/app/production/rcdl/data-ingester-*.yaml`). The pod mounts the `ocfl-data` PVC at
`/data/ocfl` (so it is scheduled on the same node as the portal API), works in
`/data/ocfl/ingest`, and is exposed at `https://admin.<domain>/ingest-api`. CI builds
`ghcr.io/rapid-community-data-lab/rapid-cdl-data-ingester:sha-<7>` on every push to `main`.
