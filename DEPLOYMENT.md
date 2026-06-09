# DEPLOYMENT.md — Deploy `rapid-cdl-admin` to K3s on Nectar via GitHub Actions + Argo CD

> **GitOps deploy.** You no longer build or push images by hand. On every push to
> `main`, **GitHub Actions** builds the image, pushes it to **ghcr.io**, and
> commits the new image tag into `k8s/`. **Argo CD** (running inside the cluster)
> watches this repo and **auto-syncs** the change to K3s. No manual `docker push`,
> no manual `kubectl apply`.
>
> **Stack:** K3s · Traefik (IngressRoute) · **Caddy** (static SPA) · ghcr.io ·
> Kustomize · **GitHub Actions** · **Argo CD**
>
> **Why GitOps here (not `kubectl` from CI)?** On the `rapid-cdl` cluster the K3s
> API (`:6443`) and SSH (`:22`) are firewalled to a **single admin IP**
> (`58.179.68.136/32`); only `:80`/`:443` are open to the world. GitHub-hosted
> runners therefore **cannot** reach the cluster to `kubectl apply`. Argo CD
> flips the direction — the cluster *pulls* from git — so nothing inbound is
> needed and no kubeconfig ever leaves the cluster.
>
> **Scope:** deploys the *application* into a cluster that already exists. VM
> provisioning + K3s/Traefik install were done once by the [`infra`](../infra)
> repo. This guide is `rapid-cdl-admin`-specific.

---

## Conventions used below

Every command is followed by the result you should expect and a status marker:

- ✅ succeeded — and why you know it did
- 🎉 excellent — a particularly good signal
- ⚠️ warning — works, but read the note
- ❌ failed — what it means and how to recover

---

**What changed vs. the old nginx design:** the container no longer proxies
`/api`. The browser bundle still calls same-origin `/api/*` (baked at build via
`VITE_API_BASE_URL=/api`), but **Traefik** now strips `/api` and routes it to the
backend; **Caddy** only serves static files. The same split is mirrored locally
in [`docker/docker-compose.yml`](docker/docker-compose.yml) (a local Traefik +
the Caddy admin), so local still matches the cluster.

---

## 1. Prerequisites

The **normal deploy needs nothing on your laptop** — it happens in CI + the
cluster. You only need local tools for the **one-time setup** (Sections 2–7) and
for verification:

| Tool | Used for | Check |
|------|----------|-------|
| `git` + a GitHub account with push access | the deploy itself (push to `main`) | `git --version` |
| `gh` (GitHub CLI) | setting the `VITE_API_TOKEN` secret | `gh --version` |
| `openstack` | create the Nectar keypair (Section 3) | `openstack --version` |
| `kubectl` | one-time Argo CD install (Section 6) | `kubectl version --client` |
| `openssl` | generate `VITE_API_TOKEN` (Section 5) | `openssl version` |

```bash
for t in git gh openstack kubectl openssl; do command -v "$t" >/dev/null && echo "ok: $t" || echo "MISSING: $t"; done
```

> ✅ Every line prints `ok: <tool>`. `MISSING:` → install it (`brew install <tool>`).

You also need, from the project maintainer: a Nectar **application credential**
for the `rapid-cdl` project, and (for the one-time Argo CD install) **kubeconfig**
or SSH access from the allow-listed admin IP.

---

## 2. Connect to Nectar OpenStack from the terminal

The `rapid-cdl` cloud uses a **Keystone application credential**
(`auth_type: v3applicationcredential`), region **`Melbourne`**.

```bash
export OS_CLIENT_CONFIG_FILE="$HOME/.config/openstack/clouds.yaml"
export OS_CLOUD=rapid-cdl
openstack token issue -f value -c expires
```

> ✅ Prints an expiry timestamp, exit `0` — your credential is valid.
> ⚠️ **Gotcha:** if you run from a directory that has its own `clouds.yaml`
> (e.g. `infra/`, which only defines `nectar-*`), the CLI uses *that* file and
> says `Cloud rapid-cdl was not found`. The `OS_CLIENT_CONFIG_FILE` export above
> forces the user-level file and avoids it.
> ❌ `requires authentication` → the application credential is wrong/expired;
> recreate it in the dashboard (**Identity → Application Credentials**).

If the `rapid-cdl` cloud is missing from `~/.config/openstack/clouds.yaml`, add:

```yaml
clouds:
  rapid-cdl:
    auth:
      auth_url: https://keystone.rc.nectar.org.au:5000/v3/
      application_credential_id: "<YOUR_APP_CRED_ID>"
      application_credential_secret: "<YOUR_APP_CRED_SECRET>"
    region_name: "Melbourne"
    interface: "public"
    identity_api_version: 3
    auth_type: "v3applicationcredential"
```

> ⚠️ `clouds.yaml` holds a credential secret — never commit it; `chmod 600` it.

---

## 3. Create a new keypair in Nectar (terminal)

A keypair lets you SSH to the VMs (e.g. for the one-time Argo CD install via the
node, or debugging). **Generate the key locally and upload only the public half**
— the private key then never leaves your machine.

```bash
# 3.1 Generate a local SSH keypair (skip if you already have one you want to use)
ssh-keygen -t ed25519 -f ~/.ssh/rapid-cdl -C "rapid-cdl deploy" -N ''

# 3.2 Upload the PUBLIC key to Nectar as a named keypair
openstack keypair create --public-key ~/.ssh/rapid-cdl.pub rapid-cdl-admin

# 3.3 Verify it registered
openstack keypair list -f value -c Name | grep rapid-cdl-admin
```

> ✅ `keypair create` returns without error and `keypair list` shows
> `rapid-cdl-admin`, exit `0`. 🎉 The private key (`~/.ssh/rapid-cdl`) stayed on
> your laptop — Nectar only ever saw the public key.
> ⚠️ `-N ''` creates a key with **no passphrase** (needed for unattended use). For
> a human key, drop `-N ''` and set a passphrase.
> ❌ `More than one keypair exists with the name` → it already exists; pick a new
> name or delete the old one (`openstack keypair delete rapid-cdl-admin` — confirm
> first, it is destructive).

> **Alternative — let Nectar generate the key** (private key is printed once; you
> must save it immediately):
> ```bash
> openstack keypair create rapid-cdl-admin > ~/.ssh/rapid-cdl && chmod 600 ~/.ssh/rapid-cdl
> ```
> ⚠️ Less safe — the private key transits the API. Prefer 3.1–3.2.

> **Note:** because SSH (`:22`) is firewalled to the admin IP
> (`58.179.68.136/32`), this key only works from that network. It is **not**
> needed for the day-to-day GitOps deploy — only for one-time/manual cluster work.

---

## 4. Connect to the K3s cluster (one-time, for Argo CD install)

Needed only to install Argo CD in Section 6. Day-to-day deploys never touch the
cluster from your laptop.

### Path A — you already have the kubeconfig

```bash
export KUBECONFIG="$HOME/.kube/rcdl-k3s-dev.yaml"
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}{"\n"}'
kubectl get nodes
```

> ✅ Prints `https://203.101.238.254:6443` and a `Ready` node.
> ❌ `connection timed out` → you are not on the admin-IP network; `:6443` is
> allow-listed to `58.179.68.136/32`. Connect from there (or via the maintainer).

### Path B — fetch the kubeconfig from the server

```bash
FIP=203.101.238.254
ssh -o StrictHostKeyChecking=accept-new -i ~/.ssh/rapid-cdl ubuntu@"$FIP" \
  "sudo cat /etc/rancher/k3s/k3s.yaml" \
  | sed "s|https://127.0.0.1:6443|https://${FIP}:6443|g" \
  > ~/.kube/rcdl-k3s-dev.yaml
chmod 600 ~/.kube/rcdl-k3s-dev.yaml
export KUBECONFIG="$HOME/.kube/rcdl-k3s-dev.yaml"
```

> ✅ Writes a ~3 KB kubeconfig; `kubectl get nodes` works.
> ⚠️ Cluster-admin file — never commit. SSH also requires the admin IP.

---

## 5. Generate a secure `VITE_API_TOKEN`

`VITE_API_TOKEN` is the **bearer token the admin UI sends to the API**'s
`/admin/*` endpoints. It is generated once, stored as a GitHub Actions secret
(baked into the build), **and** must match the token the API accepts.

```bash
# 5.1 Generate a strong random token (32 bytes, URL-safe)
TOKEN="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"

# 5.2 Store it as the repo secret CI bakes into the image (reads from stdin —
#     the value is never shown on screen or saved in shell history args)
printf '%s' "$TOKEN" | gh secret set VITE_API_TOKEN \
  --repo rapid-community-data-lab/rapid-cdl-admin

unset TOKEN
```

> ✅ `gh secret set` prints `✓ Set Actions secret VITE_API_TOKEN`, exit `0`. 🎉
> The token never appeared on screen (it went through `stdin` and a variable that
> is then `unset`).
> ⚠️ `openssl rand -base64 32` gives ~43 chars of entropy — strong. Avoid weak,
> human-chosen tokens.

> **Important — the API must accept the same value.** This token only works if the
> backend (`rapid-community-data-lab-api`) validates it as its admin token (the
> `TOKEN_ADMIN` in the API's Kubernetes Secret, managed by the [`infra`](../infra)
> repo's `k8s/.../api-secret.yaml`). Set the **same** value there and roll the API.
> Coordinate with the API maintainer; do not paste the token into chat or commit it.

> **Security reality:** because this is a SPA, the token is **baked into the public
> JS bundle** and is readable by anyone who loads the page. Treat it as a
> low-privilege, rotatable admin token and restrict access at the ingress (see
> [Section 10](#10-security-notes)). Rotate by repeating 5.1–5.2, updating the API,
> and pushing (a fresh build re-bakes it).

---

## 6. One-time cluster setup: install Argo CD and point it at this repo

Run these once, with `KUBECONFIG` set (Section 4), from the admin-IP network.

### 6.1 Install Argo CD

```bash
kubectl create namespace argocd
kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml \
  --server-side \
  --force-conflicts
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
```

> ✅ Every resource prints `serverside-applied`, `rollout status` ends with
> `deployment "argocd-server" successfully rolled out`. 🎉
> ⚠️ `namespace already exists` on the first command is fine if you re-run.
> ⚠️ **Why `--server-side --force-conflicts` and not plain `kubectl apply`?**
> The Argo CD `applicationsets` CRD is larger than Kubernetes's 256 Ki annotation
> limit. Plain `kubectl apply` (client-side) stores the full manifest as a
> `kubectl.kubernetes.io/last-applied-configuration` annotation and fails with:
> `metadata.annotations: Too long: may not be more than 262144 bytes`.
> `--server-side` moves field management to the server (no annotation is stored),
> so the size limit does not apply. `--force-conflicts` migrates any field
> ownership from a prior client-side apply attempt.

### 6.2 Give Argo CD read access to this repo (only if the repo is private)

If `rapid-community-data-lab/rapid-cdl-admin` is **public**, skip this. If
private, add a read-only credential (a GitHub PAT with `repo:read`, or a deploy
key). CLI method:

```bash
# Log in to the Argo CD API first (port-forward + initial admin password):
kubectl -n argocd port-forward svc/argocd-server 8080:443   # leave running in one terminal
# In another terminal — the initial password is in a secret (do NOT paste it in chat):
argocd login localhost:8080 --username admin \
  --password "$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)" \
  --insecure
argocd repo add https://github.com/rapid-community-data-lab/rapid-cdl-admin.git \
  --username <github-user> --password <github-PAT-with-repo-read>
```

> ✅ `argocd repo add` prints `repository '…' added`. ⚠️ The `port-forward` uses
> the K3s API (`:6443`), so run it from the admin IP. Change the admin password
> after first login.

### 6.3 Create the Argo CD Application

The Application is committed in this repo at
[`argocd/application.yaml`](argocd/application.yaml) — it watches `path: k8s`,
`targetRevision: main`, with automated prune + self-heal.

```bash
kubectl apply -n argocd -f argocd/application.yaml
kubectl -n argocd get application rapid-cdl-admin
```

> ✅ `application.argoproj.io/rapid-cdl-admin created`; the app appears with
> `SYNC STATUS` heading toward `Synced` and `HEALTH` toward `Healthy`. 🎉 From now
> on, every push to `main` is deployed automatically.
> ❌ `Health: Degraded` / `ImagePullBackOff` → the image isn't pushed yet (do one
> push, Section 8) or `ghcr-secret` is missing (it already exists in `rcdl`; verify
> with `kubectl -n rcdl get secret ghcr-secret`).

---

## 7. One-time GitHub setup: secret + permissions

| What | How | Why |
|------|-----|-----|
| Secret `VITE_API_TOKEN` | Section 5 (`gh secret set`) | Baked into the image by CI |
| Workflow permissions | **Settings → Actions → General → Workflow permissions → "Read and write permissions"** | Lets the workflow push the image to ghcr.io and commit the tag bump back |
| Package visibility | After the first push: **your org → Packages → rapid-cdl-admin → Package settings** | Public avoids needing pull creds; private uses the existing `ghcr-secret` |

> The workflow [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)
> already declares `permissions: { contents: write, packages: write }` and uses
> the built-in `GITHUB_TOKEN` — no PAT is needed for the image push or the
> commit-back. `GITHUB_TOKEN` cannot reach the cluster (that is Argo CD's job).

---

## 8. Deploy — just push to `main`

```bash
git add -A
git commit -m "feat: <your change>"
git push origin main
```

> ✅ `git push` succeeds. That single push is the entire deploy. 🎉

What happens automatically:

1. **GitHub Actions** (`Build and deploy (GitOps)`) builds the Caddy image with
   `VITE_API_BASE_URL=/api` + your `VITE_API_TOKEN`, and pushes
   `ghcr.io/rapid-community-data-lab/rapid-cdl-admin:sha-<short>` + `:latest`.
2. The workflow runs `kustomize edit set image` to pin the new `sha-` tag in
   `k8s/kustomization.yaml` and commits it back with `[skip ci]`.
3. **Argo CD** sees the new commit and applies `k8s/` to the `rcdl` namespace.

Watch it:

```bash
gh run watch --repo rapid-community-data-lab/rapid-cdl-admin          # CI build
kubectl -n argocd get application rapid-cdl-admin -w                  # GitOps sync (admin IP)
```

> ✅ The CI run goes green; the Argo CD app shows `Synced` / `Healthy` and a new
> `rapid-cdl-admin-…` pod is `Running`.
> ⚠️ First-ever run: the commit-back step needs "Read and write permissions"
> (Section 7) or it fails with `403` on `git push`.

---

## 9. Verify the deployment

```bash
HOST=rapid-cdl-admin-cb65eefe.nip.io
curl -s -o /dev/null -w '%{http_code}\n' "http://$HOST/"          # SPA  → 200
curl -s "http://$HOST/api/version"                                # API  → version JSON
```

> ✅ `/` returns `200` (Caddy serves `index.html`); `/api/version` returns the
> backend's version JSON — proving **Traefik** stripped `/api` and routed it to the
> API service (no proxy in the admin pod). 🎉
> ❌ `/api/version` returns HTML → the IngressRoute `/api` rule didn't match; check
> `kubectl -n rcdl get ingressroute rapid-cdl-admin -o yaml`.

Open `http://rapid-cdl-admin-cb65eefe.nip.io` in a browser to use the admin UI.

---

## 10. Security notes

| Item | Advice |
|------|--------|
| `VITE_API_TOKEN` in the JS bundle | Client-side and readable by anyone loading the page. Keep it low-privilege, restrict admin access at the ingress, rotate via Section 5. Long-term: replace baked token with a login flow. |
| Plain HTTP ingress | This guide uses `http://` for simplicity; an admin token over HTTP is unsafe on untrusted networks. Add TLS (see [Appendix B](#appendix-b--optional-tls)). |
| Argo CD admin password | Change it after first login; consider SSO. The initial password lives in `argocd-initial-admin-secret` — read it via `kubectl`, don't paste it anywhere. |
| Cluster API firewall | `:6443`/`:22` stay locked to the admin IP — GitOps means CI never needs them open. Do **not** widen them for convenience. |
| `clouds.yaml`, kubeconfig, SSH key, PATs | Never commit; `chmod 600`. |

---

## 11. Day-2 operations

```bash
# Roll back a bad deploy — revert the source commit; CI rebuilds, Argo CD syncs.
git revert <bad-sha> && git push origin main

# Force an Argo CD sync now (admin IP):
argocd app sync rapid-cdl-admin           # or: kubectl -n argocd annotate application rapid-cdl-admin argocd.argoproj.io/refresh=hard --overwrite

# Inspect the running app:
kubectl -n rcdl get pods -l app=rapid-cdl-admin
kubectl -n rcdl logs deploy/rapid-cdl-admin -f
```

> ⚠️ `selfHeal: true` is on, so manual `kubectl edit` to the admin objects is
> reverted to match git. Change things in git, not live.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| CI fails at "Commit the tag bump" with `403` | Workflow can't push | Settings → Actions → Workflow permissions → **Read and write** (Section 7) |
| CI build warns `SecretsUsedInArgOrEnv … VITE_API_TOKEN` | Expected | The token is baked into the client bundle by design (Section 5/10); not an error |
| Argo CD app `OutOfSync` forever | Repo not readable | Add repo creds (Section 6.2) if private |
| Pod `ImagePullBackOff` | Image not pushed yet, or `ghcr-secret` missing | Push once (Section 8); `kubectl -n rcdl get secret ghcr-secret` |
| `404` on `http://<host>/` | Traefik can't match the Host | Host must equal the nip.io name and resolve to `203.101.238.254` |
| `/api/*` returns the SPA HTML | `/api` IngressRoute rule missing/typo | `kubectl -n rcdl get ingressroute rapid-cdl-admin -o yaml` |
| Browser calls go to `localhost:8080` | Bundle built with wrong `VITE_API_BASE_URL` | Must be `/api`; it's hard-set in the workflow + Dockerfile default |
| `Cloud rapid-cdl was not found` | CLI read another `clouds.yaml` | `export OS_CLIENT_CONFIG_FILE=$HOME/.config/openstack/clouds.yaml` |

---

## Appendix A — local development (Traefik + Caddy, mirrors the cluster)

The local stack now matches the cluster: a local Traefik routes `/api` → backend
and `/` → the Caddy static admin. No nginx anywhere.

```bash
docker network create rapid-community-data-lab            # one-time
cd ../rapid-community-data-lab-api && docker compose up -d # postgres + opensearch + api
cd ../rapid-cdl-admin
export VITE_API_TOKEN='<dev-token>'                       # baked into the local build
docker compose -f docker/docker-compose.yml up -d --build
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8082/        # SPA → 200
curl -s http://localhost:8082/api/version                              # routed to api
```

> ✅ Same image and same routing model as K3s — only the Traefik provider differs
> (Docker labels + a file route locally vs. an IngressRoute in-cluster).

## Appendix B — optional TLS

cert-manager is already installed (used by the shared host). To serve the admin
over HTTPS, add a `Certificate` for `rapid-cdl-admin-cb65eefe.nip.io` and a
`websecure` IngressRoute referencing its secret (mirror
[`infra/k8s/base/traefik/`](../infra/k8s/base/traefik/)), add the files to
`k8s/kustomization.yaml`, and push — Argo CD deploys them.

## Appendix C — manual one-off deploy (fallback, admin IP only)

If Argo CD is down and you must deploy from the admin network:

```bash
export KUBECONFIG="$HOME/.kube/rcdl-k3s-dev.yaml"
kubectl apply -k k8s
kubectl -n rcdl rollout status deploy/rapid-cdl-admin
```

> ⚠️ This bypasses GitOps; Argo CD will re-sync to git state on its next pass.
