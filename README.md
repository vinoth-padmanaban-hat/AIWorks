# AIWorks

Multi-tenant **agentic platform** PoC: a **FastAPI control plane** (orchestrator, planner, execution engine, admin API) coordinates agent services (content ingestion, curation, generic template agents) plus a **FastMCP scraper server**. Data lives in **PostgreSQL** — one **control-plane** database plus **per-tenant** databases. A **Next.js** app in `web/` provides an admin UI.

## Architecture (short)

- **Control plane** (`app/`, port **8000**): `POST /execute`, `GET /admin/*`, `GET /health`. Dispatches work using the skill/agent registries in Postgres.
- **Agents** (`agents/`, **8001–8005**): LangGraph pipelines and tools; call scraper tools via FastMCP.
- **Scraper MCP** (`tools/scraper_mcp/`, **8002**, endpoint `/mcp`): FastMCP + crawl4ai / Playwright + fallbacks; needs extra **shared memory** in Docker (`shm_size`).
- **Web** (`web/`, **3000**): admin UI; browser calls the API on the host; server components use `API_URL` when the UI runs in Docker (see [Web + API](#web--api)).

More detail: [`architecture.md`](architecture.md), [`docs/`](docs/).

## Prerequisites

- **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)** for local Python workflows
- **Docker** with **Compose v2** for the container stack
- **Node.js 20+** for the Next.js app (local dev or image build)
- **OpenAI API key** (or compatible setup) for planner/agents — set `OPENAI_API_KEY` in `.env`

## Configuration

| File | Purpose |
|------|--------|
| [`.env`](docker/compose.env.example) (repo root) | Copy from `docker/compose.env.example`. Used by Compose substitution and `pydantic-settings` (`app/core/config.py`). |
| [`web/.env.local`](web/.env.local.example) | `NEXT_PUBLIC_API_URL`, optional `API_URL` for local `npm run dev`. |

## Docker quick start

### First time (empty database)

1. Copy env: `cp docker/compose.env.example .env` and set **`OPENAI_API_KEY`**.
2. Build images: **`make docker-build`**
3. Start stack and apply migrations: **`make docker-up`**
4. Register skills/agents in the DB: **`make docker-bootstrap`**
5. *(Optional)* Demo tenants/sources: **`make docker-seed`** (skips automatically if demo data already exists; use **`make docker-seed-force`** to redo)

### Day to day

- **`make docker-up`** — runs idempotent migrations, starts all services. Does **not** re-seed or re-register agents.
- **`make docker-down`** — stop containers.

### Dev stack (Python hot reload)

- **`make docker-up-dev`** — same as `docker-up` but merges [`docker-compose.dev.yml`](docker-compose.dev.yml): bind-mounts `app/`, `agents/`, `tools/`, `scripts/` and runs **uvicorn `--reload`**.
- **`make docker-down-dev`** — stop the dev stack (use this if you used `docker-up-dev`).
- **Next.js HMR**: run the UI on the host — `cd web && npm install && npm run dev` — with `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`.

Full target list: **`make help`**.

## Services and ports

| Port | Service |
|------|---------|
| 5432 | Postgres |
| 8000 | Control plane |
| 8001 | Content ingestion agent |
| 8002 | Scraper MCP |
| 8003 | Content curator agent |
| 8004 | Generic scraper agent |
| 8005 | Generic matcher agent |
| 3000 | Next.js (Docker production build or local `npm run dev`) |

Ensure **8001–8005** are free on the host, or stop conflicting containers before `docker compose up`.

## Web + API

- **Browser** uses **`NEXT_PUBLIC_API_URL`** (default `http://127.0.0.1:8000`).
- **Next.js Server Components** use **`API_URL`** when defined; the **`web`** service in Compose sets `API_URL=http://control-plane:8000` so server-side fetches reach the API inside the Docker network.

## Database migrations

Migrations are **not** a long-lived container. They run as a one-off job:

- **`make docker-migrate`** (requires Postgres up)

Scripts apply control-plane and tenant SQL in order; progress is tracked so re-runs are safe. See [`docker/init-db.sh`](docker/init-db.sh).

## Local development (no Docker)

```bash
uv sync
uv run playwright install chromium   # for scraping stack
```

Run Postgres locally, apply migrations (same order as `docker/init-db.sh`), then start each process — see **`make local-install`** and **`make local-up`** for the exact `uvicorn` lines.

## Project layout

```
app/           # Control plane: API, orchestration, policies, registries, DB access
agents/        # Agent FastAPI apps + LangGraph graphs
tools/         # Scraper MCP server + client
web/           # Next.js admin UI
db/migrations/ # Postgres SQL (control plane + per-tenant)
scripts/       # register_agents.py, seed_content_tenants.py, …
docker/        # init-db.sh, compose env example
```

## Troubleshooting

- **Bind errors on 8001–8005**: something else is using those ports; stop those processes or containers.
- **UI loads but admin data fails in Docker**: ensure the **`web`** service has **`API_URL=http://control-plane:8000`** (already set in `docker-compose.yml`).
- **CORS**: set **`CORS_ORIGINS`** in `.env` if you open the UI from a host other than `localhost` / `127.0.0.1`.
- **Code changes in Docker (production compose)**: rebuild with **`make docker-build`** and recreate containers. Use **`make docker-up-dev`** for Python reload without rebuilding.

## License / version

See `pyproject.toml` for the Python package name and dependencies. Versioning follows the repo’s release practice (if any).
