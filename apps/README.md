# LocallyAI — Frontends

Two single-page applications that talk to the FastAPI backend in the parent
directory:

| App           | Path                | Audience       | Default port |
| ------------- | ------------------- | -------------- | ------------ |
| Manager UI    | `apps/manager-ui`   | Administrators | `5173`       |
| Worker UI     | `apps/worker-ui`    | End users      | `5174`       |

Both apps are Vite + TanStack Router + shadcn-ui. They authenticate with the
backend by sending a single bearer token in the `Authorization` header:

- **Manager UI** uses the operator's `LOCALLYAI_ADMIN_KEY`. The backend treats
  this key as the synthetic user `admin`, so the same key works for chat,
  ingest, and every `/admin/*`, `/monitor/*`, `/export/*`, `/billing/*`,
  `/diagnostician/*` endpoint.
- **Worker UI** uses a per-user API key issued from the Manager UI's Users
  page (or via `python manage_users.py add <name>`).

Tokens live only in `localStorage` on the operator's machine — they are sent
only as a Bearer header to the LocallyAI backend.

## 1. Backend

From the repository root:

```bash
cp .env.example .env
# Generate the three required secrets
python -c "import secrets; print(secrets.token_hex(32))"   # LOCALLYAI_ADMIN_KEY
python -c "import secrets; print(secrets.token_hex(32))"   # LOCALLYAI_AUDIT_SALT
python -c "import secrets; print(secrets.token_hex(32))"   # LOCALLYAI_AUDIT_HMAC_KEY

pip install -r requirements.txt
python api.py            # listens on http://localhost:8000
```

The backend's default `LOCALLYAI_CORS_ORIGINS` already allows
`http://localhost:5173` (manager) and `http://localhost:5174` (worker). For
remote installs, set it explicitly to the operator's hostname.

Provision a worker user (so the Worker UI has a key to log in with):

```bash
python manage_users.py add "Sarah Chen"
# prints: API key: <64-hex>
```

## 2. Manager UI

```bash
cd apps/manager-ui
cp .env.example .env.local            # VITE_API_BASE_URL=http://localhost:8000
bun install                           # or: npm install
bun run dev -- --port 5173            # or: npm run dev -- --port 5173
```

Open http://localhost:5173 and sign in with `LOCALLYAI_ADMIN_KEY`. The
console wires up against:

- `GET /healthz` and `GET /v1/me` — gate
- `GET /monitor/health/detailed`, `GET /monitor/alerts` — Dashboard, System
- `GET /admin/users`, `POST /admin/users`, `DELETE /admin/users/{name}`,
  `POST /admin/users/{name}/rotate` — Users
- `POST /v1/ingest`, `POST /v1/chat/completions`, `GET /v1/models` — Documents, Query
- `GET /export/summary`, `GET /export/` — Audit
- `GET /diagnostician/history` — System

## 3. Worker UI

```bash
cd apps/worker-ui
cp .env.example .env.local            # VITE_API_BASE_URL=http://localhost:8000
bun install                           # or: npm install
bun run dev -- --port 5174            # or: npm run dev -- --port 5174
```

Open http://localhost:5174 and sign in with the per-user API key minted in
step 1. The workspace wires up against:

- `GET /healthz` and `GET /v1/me` — gate, header
- `GET /v1/models` — model picker
- `POST /v1/chat/completions` — chat (with retrieval grounding count)

## Networking notes

- The backend default CORS allowlist covers `localhost`/`127.0.0.1` on dev
  ports `5173` and `5174`. For LAN deployments, set
  `LOCALLYAI_CORS_ORIGINS=http://<workstation-host>:<port>` in `.env`.
- The `/v1/chat/completions` endpoint is rate-limited to 30/minute per IP
  by `slowapi`. Adjust `default_limits=` in `api.py` if you serve a large
  internal team from a single egress IP.
- For production builds, run `bun run build` in each app and serve the
  resulting `dist/` from a static file server. Nothing about the apps assumes
  Cloudflare; the `wrangler.jsonc` is left in place for those who want it.
