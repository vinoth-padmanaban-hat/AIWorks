# AIWorks — local development and Docker orchestration
# Requires: Docker with Compose v2, GNU Make, optional uv + Node for local targets.

COMPOSE ?= docker compose
ROOT   := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
ENV_EX := $(ROOT)docker/compose.env.example

# Base stack (production-style images, no bind mounts).
FILES_BASE := -f "$(ROOT)docker-compose.yml"
# Dev stack: same as base + docker-compose.dev.yml (Python bind mounts + --reload).
FILES_DEV  := $(FILES_BASE) -f "$(ROOT)docker-compose.dev.yml"

.PHONY: help
help:
	@echo "AIWorks targets"
	@echo ""
	@echo "  Docker — production-style (no hot reload; rebuild after code changes)"
	@echo "    make docker-build       Build backend + web images"
	@echo "    make docker-migrate     Apply SQL migrations only (idempotent)"
	@echo "    make docker-up          Migrate + compose up"
	@echo "    make docker-bootstrap   First-time: docker-up + register agents"
	@echo "    make docker-down        Stop base stack (use docker-down-dev if you used docker-up-dev)"
	@echo "    make docker-reset       Stop and remove Postgres volume"
	@echo "    make docker-logs        Follow logs (base compose file)"
	@echo "    make docker-register    Upsert skills/agents (base stack)"
	@echo "    make docker-seed / docker-seed-force"
	@echo ""
	@echo "  Docker — dev (Python bind mounts + uvicorn --reload)"
	@echo "    make docker-up-dev      Like docker-up but with docker-compose.dev.yml"
	@echo "    make docker-down-dev    Stop dev stack (same compose files as docker-up-dev)"
	@echo "    make docker-bootstrap-dev   First-time on dev stack + register"
	@echo "    make docker-register-dev    register_agents (dev stack)"
	@echo "    make docker-seed-dev / docker-seed-force-dev"
	@echo "    make docker-logs-dev    Follow logs (dev compose files)"
	@echo "    Next.js HMR: cd web && npm run dev  (API at :8000 in Docker)"
	@echo ""
	@echo "  Local (no Docker)"
	@echo "    make local-install    uv sync + Playwright chromium"
	@echo "    make local-up         Print uvicorn commands"

.PHONY: docker-build
docker-build:
	@test -f "$(ROOT).env" || (echo "Creating .env from docker/compose.env.example — add OPENAI_API_KEY"; cp "$(ENV_EX)" "$(ROOT).env")
	$(COMPOSE) $(FILES_BASE) build

.PHONY: docker-migrate
docker-migrate:
	$(COMPOSE) $(FILES_BASE) up -d postgres
	@echo "Waiting for Postgres (up to 60s)..."
	@n=0; until $(COMPOSE) $(FILES_BASE) exec -T postgres pg_isready -U aiworks -d aiworks >/dev/null 2>&1; do \
		n=$$((n+1)); test $$n -le 60 || (echo "Postgres did not become ready"; exit 1); sleep 1; \
	done
	$(COMPOSE) $(FILES_BASE) --profile migrate run --rm db-migrate

.PHONY: docker-init-db
docker-init-db: docker-migrate

.PHONY: docker-up
docker-up:
	@test -f "$(ROOT).env" || (echo "Creating .env from docker/compose.env.example — add OPENAI_API_KEY for LLM features"; cp "$(ENV_EX)" "$(ROOT).env")
	@$(MAKE) -f "$(ROOT)Makefile" docker-migrate
	$(COMPOSE) $(FILES_BASE) up -d
	@echo "Waiting for control-plane /health (up to 90s)..."
	@n=0; until $(COMPOSE) $(FILES_BASE) exec -T control-plane \
		curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do \
		n=$$((n+1)); test $$n -le 90 || (echo "control-plane did not become healthy"; exit 1); sleep 1; \
	done
	@echo ""
	@echo "UI: http://localhost:3000  |  API: http://localhost:8000/health"
	@echo "First time on an empty DB: make docker-bootstrap   (then optional: make docker-seed)"

.PHONY: docker-up-dev
docker-up-dev:
	@test -f "$(ROOT).env" || (echo "Creating .env from docker/compose.env.example — add OPENAI_API_KEY for LLM features"; cp "$(ENV_EX)" "$(ROOT).env")
	@$(MAKE) -f "$(ROOT)Makefile" docker-migrate
	$(COMPOSE) $(FILES_DEV) up -d
	@echo "Waiting for control-plane /health (up to 90s)..."
	@n=0; until $(COMPOSE) $(FILES_DEV) exec -T control-plane \
		curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do \
		n=$$((n+1)); test $$n -le 90 || (echo "control-plane did not become healthy"; exit 1); sleep 1; \
	done
	@echo ""
	@echo "API: http://localhost:8000/health  |  Python edits reload automatically."
	@echo "Web: docker web container on :3000, or run  cd web && npm run dev  for HMR."
	@echo "First time: make docker-bootstrap-dev   |  Stop with: make docker-down-dev"

.PHONY: docker-bootstrap
docker-bootstrap: docker-up
	@echo "Registering skills/agents (idempotent)..."
	@$(COMPOSE) $(FILES_BASE) exec -T control-plane \
		uv run python scripts/register_agents.py
	@echo ""
	@echo "Optional demo data (skips automatically if already seeded): make docker-seed"

.PHONY: docker-bootstrap-dev
docker-bootstrap-dev: docker-up-dev
	@echo "Registering skills/agents (idempotent)..."
	@$(COMPOSE) $(FILES_DEV) exec -T control-plane \
		uv run python scripts/register_agents.py
	@echo ""
	@echo "Optional demo data: make docker-seed-dev"

.PHONY: docker-down
docker-down:
	$(COMPOSE) $(FILES_BASE) down

.PHONY: docker-down-dev
docker-down-dev:
	$(COMPOSE) $(FILES_DEV) down

.PHONY: docker-reset
docker-reset:
	$(COMPOSE) $(FILES_BASE) down -v

.PHONY: docker-logs
docker-logs:
	$(COMPOSE) $(FILES_BASE) logs -f

.PHONY: docker-logs-dev
docker-logs-dev:
	$(COMPOSE) $(FILES_DEV) logs -f

.PHONY: docker-register
docker-register:
	$(COMPOSE) $(FILES_BASE) exec -T control-plane \
		uv run python scripts/register_agents.py

.PHONY: docker-register-dev
docker-register-dev:
	$(COMPOSE) $(FILES_DEV) exec -T control-plane \
		uv run python scripts/register_agents.py

.PHONY: docker-seed
docker-seed:
	$(COMPOSE) $(FILES_BASE) exec -T control-plane \
		uv run python scripts/seed_content_tenants.py

.PHONY: docker-seed-dev
docker-seed-dev:
	$(COMPOSE) $(FILES_DEV) exec -T control-plane \
		uv run python scripts/seed_content_tenants.py

.PHONY: docker-seed-force
docker-seed-force:
	$(COMPOSE) $(FILES_BASE) exec -T control-plane \
		uv run python scripts/seed_content_tenants.py --force

.PHONY: docker-seed-force-dev
docker-seed-force-dev:
	$(COMPOSE) $(FILES_DEV) exec -T control-plane \
		uv run python scripts/seed_content_tenants.py --force

.PHONY: local-install
local-install:
	cd "$(ROOT)" && uv sync && uv run playwright install chromium

.PHONY: local-up
local-up:
	@echo "Run Postgres locally, apply db/migrations (see docker/init-db.sh for file order), then:"
	@echo "  cd $(ROOT) && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
	@echo "  cd $(ROOT) && uv run uvicorn agents.content_ingestion.main:app --port 8001 --reload"
	@echo "  cd $(ROOT) && uv run python -m tools.scraper_mcp.server"
	@echo "  cd $(ROOT) && uv run uvicorn agents.content_curator.main:app --port 8003 --reload"
	@echo "  cd $(ROOT) && uv run uvicorn agents.templates.scraper_main:app --port 8004 --reload"
	@echo "  cd $(ROOT) && uv run uvicorn agents.templates.matcher_main:app --port 8005 --reload"
	@echo "  cd $(ROOT)/web && npm install && npm run dev"
	@echo "First time: uv run python scripts/register_agents.py"
	@echo "Optional demo: uv run python scripts/seed_content_tenants.py  (skips if seeded; --force to redo)"
