# Implementation Plan: Multi-Tenant Generic Agentic Platform

## Current State Assessment

### What exists (good foundations)

- Multi-tenant DB isolation (control plane + per-tenant DBs)
- Persona system (PersonaRecord, PersonaSnapshot, PersonaRepository)
- Policy engine (PolicyEngine, effective policy, skill checks)
- Agent registry (AgentManifest, find_agents_for_skill)
- Skill registry (DB table, but no Python client)
- Execution engine (HTTP dispatch to agent services)
- Content ingestion agent (LangGraph: scrape → normalize → tag → format)
- Scraper MCP tool (crawl4ai/playwright)
- Admin UI (Next.js) with tenant views
- Seed scripts for tenants and agents

### What's wrong (architecture violations)

1. **Hardcoded routes**: `/ingestion/run/{tenant_id}` — domain-specific, not generic
2. **Hardcoded orchestrator**: `run_content_ingestion()` — one method per use case
3. **No planner**: orchestrator directly maps to a single skill
4. **No skill registry client**: skills exist in DB but no Python query layer
5. **No product catalog**: PoC needs product/service matching
6. **No newsletter generation**: PoC needs content creation from scraped + product data
7. **Tightly coupled**: agent graph is monolithic (scrape+normalize+tag+format in one graph)

### PoC target

A tenant queries websites daily, creates newsletter/articles referencing their
products/services, and lists them in a UI for human review before social media
publishing.

---

## Implementation Steps

### Step 1: Skill Registry Client

**Why**: The planner and orchestrator need to query available skills programmatically.

- Create `app/domain/registries/skill_registry.py`
- `SkillRegistryClient.list_skills(tags?, active_only?)` → list of SkillManifest
- `SkillRegistryClient.get_skill(skill_id)` → SkillManifest | None
- Pydantic model `SkillManifest(skill_id, name, description, domain, tags, input_schema, output_schema)`

### Step 2: Generic Orchestrator

**Why**: Replace the hardcoded `run_content_ingestion` with a generic `execute` method.

- Refactor `app/core/orchestrator.py`:
  - `execute(tenant_id, persona_id?, goal, skill_ids?)` → AgentInvocationResult
  - Flow: resolve persona → load policy → filter skills by policy → call planner → dispatch plan
- Keep `run_content_ingestion` as a thin wrapper (backward compat) that calls `execute`

### Step 3: Simple Planner

**Why**: The planner decides which skills to run and in what order, based on goal + persona + allowed skills.

- Create `app/orchestration/planner.py`
- For PoC: LLM-based planner that:
  - Takes: goal, persona context, list of available skills (from registry, filtered by policy)
  - Returns: ordered list of `PlanStep(step_id, skill_id, input_spec, depends_on)`
- Uses structured output (JSON) from the LLM
- Falls back to simple heuristics if LLM fails

### Step 4: Generic API Route

**Why**: Replace domain-specific `/ingestion/run` with generic `/execute`.

- Create `app/api/execute_routes.py`:
  - `POST /execute` — body: `ExecuteRequest(tenant_id, persona_id?, goal, skill_ids?)`
  - Response: `ExecuteResponse(execution_id, status, plan, results, cost)`
- Keep `/ingestion/run/{tenant_id}` as deprecated alias
- Update `app/main.py` to include new router

### Step 5: Tenant Product Catalog (DB + API)

**Why**: PoC needs product/service data for article-product matching.

- New tenant DB migration: `db/migrations/tenant/004_product_catalog.sql`
  - `tenant_products(id, name, description, url, category, tags[], features[], active)`
- New control plane admin routes for products CRUD
- Seed product data for test tenant (NeuralEdge AI)

### Step 6: Execution Tracking Generalization

**Why**: Current `ingestion_executions` table is too specific. Need generic execution tracking.

- New tenant DB migration: `db/migrations/tenant/005_executions.sql`
  - `executions(execution_id, skill_id, persona_id, goal, started_at, finished_at, status, plan_json, result_json, cost_json)`
  - `execution_steps(id, execution_id, step_id, skill_id, status, input_json, output_json, cost_json, started_at, finished_at)`
- Keep `ingestion_executions` for backward compatibility

### Step 7: Content Curation Agent (the PoC agent)

**Why**: This is the main PoC — scrape websites, create newsletter articles with product references.

- Create `agents/content_curator/` with:
  - `graph.py` — LangGraph pipeline:
    1. `load_config` — load tenant sources, products, tags, persona
    2. `scrape_sources` — reuse existing scraper tool
    3. `extract_content` — normalize raw content into structured articles
    4. `match_products` — LLM matches each article to relevant tenant products
    5. `generate_newsletter` — LLM generates newsletter-ready article with product refs
    6. `save_results` — persist to tenant DB
  - `main.py` — FastAPI agent service (port 8003)
  - `models.py` — Pydantic I/O models

### Step 8: Register New Skills and Agent

**Why**: The new agent and skills need to be discoverable.

- Update `scripts/register_agents.py`:
  - Register skills: `content_curation`, `match_products`, `generate_newsletter`
  - Register agent: `content_curator_agent` → port 8003
  - Map skills to agent

### Step 9: Seed PoC Tenant Data

**Why**: Need test data to run the PoC end-to-end.

- Update seed script or create new one:
  - Add products for NeuralEdge AI tenant (AI/ML products/services)
  - Ensure persona has `content_curation` in default_skills

### Step 10: Admin UI Updates

**Why**: Human review workflow needs product catalog view and newsletter article view.

- Add product catalog page: `/admin/tenants/[tenantId]/products`
- Add newsletter view: `/admin/tenants/[tenantId]/newsletters`
- Show product references in article detail view
- Add execution trigger from UI (POST /execute)

---

## Architecture After Changes

```
Request → POST /execute { tenant_id, goal }
  │
  ├─ Orchestrator
  │   ├─ Resolve Persona (PersonaRepository)
  │   ├─ Load Policy (PolicyEngine → EffectivePolicy)
  │   ├─ Load Skills (SkillRegistry → filter by policy)
  │   ├─ Call Planner (goal + persona + skills → PlanGraph)
  │   └─ Dispatch Plan (ExecutionEngine → Agent Registry → HTTP)
  │
  ├─ Agent Service (e.g. Content Curator)
  │   ├─ LangGraph: scrape → extract → match_products → generate → save
  │   └─ Uses: ScraperTool, LLM, TenantDB
  │
  └─ Results → Tenant DB → Admin UI → Human Review → Publish
```

## File Changes Summary

### New files

- `app/domain/registries/skill_registry.py`
- `app/orchestration/planner.py`
- `app/api/execute_routes.py`
- `agents/content_curator/` (graph.py, main.py, models.py, **init**.py)
- `db/migrations/tenant/004_product_catalog.sql`
- `db/migrations/tenant/005_executions.sql`

### Modified files

- `app/core/orchestrator.py` — generic execute method
- `app/main.py` — include new router
- `app/domain/models/invocation.py` — add ExecuteRequest/ExecuteResponse
- `app/api/admin_routes.py` — add product/execution endpoints
- `app/core/config.py` — add content_curator_agent config
- `scripts/register_agents.py` — register new skills/agent
- `scripts/seed_content_tenants.py` — add product seed data
- `pyproject.toml` — any new dependencies

### Kept as-is (backward compatible)

- `agents/content_ingestion/` — existing agent, still works
- `app/api/ingestion_routes.py` — deprecated but functional
- `tools/scraper_mcp/` — reused by new agent

---

## Execution Order

All steps are COMPLETE:

1. [x] **Step 1** (Skill Registry Client) — `app/domain/registries/skill_registry.py`
2. [x] **Step 5** (Product Catalog DB) — `db/migrations/tenant/004_product_catalog.sql`
3. [x] **Step 6** (Generic Executions DB) — `db/migrations/tenant/005_executions.sql`
4. [x] **Step 2** (Generic Orchestrator) — `app/core/orchestrator.py` refactored
5. [x] **Step 3** (Planner) — `app/orchestration/planner.py`
6. [x] **Step 4** (Generic API Route) — `app/api/execute_routes.py`
7. [x] **Step 7** (Content Curator Agent) — `agents/content_curator/`
8. [x] **Step 8** (Register Skills/Agent) — `scripts/register_agents.py` updated
9. [x] **Step 9** (Seed Data) — products added to seed script
10. [x] **Step 10** (UI Updates) — products, newsletters, execute pages

---

## How to Run (End-to-End)

### 1. Apply new migrations to each tenant DB

```bash
# For each tenant DB (e.g. aiworks_t001):
psql postgresql://postgres@localhost:5432/aiworks_t001 \
  -f db/migrations/tenant/004_product_catalog.sql
psql postgresql://postgres@localhost:5432/aiworks_t001 \
  -f db/migrations/tenant/005_executions.sql
```

### 2. Register skills and agents

```bash
uv run python scripts/register_agents.py
```

### 3. Seed tenant data (includes products now)

```bash
uv run python scripts/seed_content_tenants.py
```

### 4. Start services

```bash
# Terminal 1: Scraper MCP tool
uv run uvicorn tools.scraper_mcp.server:app --port 8002 --reload

# Terminal 2: Content Ingestion Agent (legacy)
uv run uvicorn agents.content_ingestion.main:app --port 8001 --reload

# Terminal 3: Content Curator Agent (new)
uv run uvicorn agents.content_curator.main:app --port 8003 --reload

# Terminal 4: Control Plane
uv run uvicorn app.main:app --port 8000 --reload

# Terminal 5: Web UI
cd web && npm run dev
```

### 5. Trigger content curation

```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "00000000-0000-0000-0000-000000000001",
    "goal": "Scrape configured sources, create newsletter articles with product references"
  }'
```

### 6. Review in UI

Open http://localhost:3000/admin/tenants/00000000-0000-0000-0000-000000000001/newsletters

---

## Phase 2: Generic Agents, Scraping Limits, Verbose Logging & Guardrails

The following requirements have been captured from design review and are the
next implementation phase. They are **not yet implemented**.

### What changed / was decided

1. **Tenant scraping limits in policy** — each tenant's `TenantPolicy` gains a
   `scraping_limits` block (max depth, max links per page, max total links,
   allow external domains, allow subdomains). The Scraper MCP server enforces
   these limits on every crawl. See §12.1 of `SYSTEM_DOC_UPDATED.md`.

2. **Generic reusable agents** — instead of one monolithic content-curator graph,
   we extract two generic agents that any domain can reuse:
   - `generic_scraper_agent` — web search + crawl + media extraction + schema
     normalisation, backed by a FastMCP server with N crawl4ai tools.
   - `generic_content_matcher_agent` — vector search + DB lookup + LLM re-ranking
     to match any content to any tenant entity type.

3. **Verbose structured logging** — every node, tool call, LLM call, policy
   check, and guardrail check emits a structured JSON log at the appropriate
   level (`INFO` / `DEBUG` / `TRACE`). Full details in §10.1 of
   `SYSTEM_DOC_UPDATED.md`.

4. **Guardrails** — inline guardrail library (`app/guardrails/`) with:
   - `input_filters.py` — schema validation, prompt injection heuristics, PII checks.
   - `tool_policies.py` — per-tool/skill allow-deny, scraping limit enforcement.
   - `output_filters.py` — length/format checks, unsafe content filters, redaction.

---

### Phase 2 Steps

#### P2-Step 1: Scraping Limits in Policy

**Why**: Tenants need per-tenant crawl quotas enforced at the tool layer.

- Add `scraping_limits_json` column to `tenant_policies` in control plane DB.
  - Migration: `db/migrations/control/006_scraping_limits.sql`
- Update `TenantPolicy` Pydantic model (`app/domain/policy/models.py`) with
  `ScrapingLimits` sub-model.
- Update `PolicyEngine.get_effective_policy()` to include scraping limits.
- Pass `scraping_limits` from `EffectivePolicy` into Scraper MCP tool call config.
- Update `tools/scraper_mcp/server.py` to read and enforce limits per request.
- Seed default scraping limits for test tenant.

#### P2-Step 2: Expand Scraper MCP Server (FastMCP, crawl4ai tools)

**Why**: Current scraper has only `fetch_page` and `fetch_links`. Every other scenario below is unhandled.

---

##### Current state (what already exists in `tools/scraper_mcp/`)

| Endpoint | What it does | Gaps |
|---|---|---|
| `POST /tools/fetch_page` | Single-page crawl via crawl4ai+Playwright → clean Markdown, raw HTML, title, content hash, change detection. Handles SSR/CSR/SPA/JS-heavy pages. | No media extraction. No structured extraction. No screenshot. |
| `POST /tools/fetch_links` | Crawls a page and returns filtered, deduplicated outgoing links (internal/external, pattern filter). | Single-depth only. No scoring. No nested/BFS traversal. |

The shared `AsyncWebCrawler` is already initialised once at startup (single Playwright/Chromium process). All new tools reuse it.

---

##### Scenario → Tool mapping

Each scenario below maps to one new MCP endpoint. All endpoints accept a `scraping_config` block carrying the tenant's policy limits (`max_depth`, `max_links_per_page`, `max_total_links`, `allow_external_domains`, `allow_subdomains`).

---

**Scenario 1 — Full page content including multimedia**
> "Crawl a page and get everything: text, images, videos, audio, links, metadata."

Current `fetch_page` returns text only. New tool needed:

```
POST /tools/fetch_page_full
```

Request additions over `fetch_page`:
- `include_media: bool = True` — extract images/videos/audio from `result.media`
- `include_links: bool = True` — include internal + external links from `result.links`
- `include_raw_html: bool = False` — optionally return raw HTML (large, off by default)
- `screenshot: bool = False` — capture a viewport screenshot (see Scenario 5)

Response additions:
```json
{
  "url": "...",
  "clean_text": "...(markdown)...",
  "title": "...",
  "metadata": { "author": "...", "published_at": "...", "canonical_url": "..." },
  "images": [{ "src": "...", "alt": "...", "score": 0.9 }],
  "videos": [{ "src": "...", "type": "youtube_embed" }],
  "audio":  [{ "src": "..." }],
  "links":  { "internal": [...], "external": [...] },
  "screenshot_base64": null
}
```

crawl4ai backing: `result.media["images"]`, `result.media["videos"]`, `result.media["audio"]`, `result.links`, `result.metadata`.

---

**Scenario 2 — All links from a page, with optional depth/nesting**
> "Get all links on a page. Or go N levels deep and collect all discovered URLs."

Two sub-cases:

**2a — Single-page links** (already exists as `fetch_links`, but add scoring):

```
POST /tools/fetch_links          ← existing, extend with:
  score_links: bool = False      ← fetch <head> of each link + BM25 score against `query`
  query: str | None = None       ← relevance query for scoring
  max_links: int = 200           ← cap before scoring
```

**2b — Multi-depth link discovery** (new):

```
POST /tools/discover_urls
```

Request:
```json
{
  "seed_url": "https://example.com",
  "max_depth": 2,
  "max_total_urls": 100,
  "same_domain_only": true,
  "include_patterns": ["/blog/", "/news/"],
  "exclude_patterns": ["/tag/", "/author/"],
  "scraping_config": { ... }
}
```

Response: flat list of all discovered URLs with their depth level and parent URL. Does **not** fetch page content — this is URL discovery only (uses crawl4ai `prefetch=True` mode, 5–10× faster).

crawl4ai backing: `BFSDeepCrawlStrategy(max_depth=N, max_pages=M, include_external=False, prefetch=True)`.

---

**Scenario 3 — Multiple page scraping (batch)**
> "Scrape 20 URLs in parallel and return their content."

```
POST /tools/fetch_pages_batch
```

Request:
```json
{
  "urls": ["https://...", "https://..."],
  "include_media": false,
  "include_links": false,
  "scraping_config": { ... }
}
```

Response: list of `FetchPageFullResult` (same shape as Scenario 1), one per URL. Failed URLs include `error` field; others succeed independently.

crawl4ai backing: `crawler.arun_many(urls, config=run_cfg)` — native parallel execution, respects `max_concurrent_requests` from `scraping_config`.

---

**Scenario 4 — Deep crawl with breadth and depth limits**
> "Start from a seed URL, crawl up to depth 3, max 50 pages, BFS strategy."

```
POST /tools/deep_crawl
```

Request:
```json
{
  "seed_url": "https://example.com/blog",
  "strategy": "bfs",
  "max_depth": 3,
  "max_pages": 50,
  "include_external": false,
  "include_patterns": ["/blog/"],
  "exclude_patterns": ["/tag/"],
  "include_media": false,
  "scraping_config": { ... }
}
```

`strategy` options:
- `"bfs"` — breadth-first: all pages at depth 1 before depth 2. Best for comprehensive coverage.
- `"dfs"` — depth-first: follows one branch as deep as possible. Best for article threads.
- `"best_first"` — scores URLs by relevance before visiting. Best for targeted extraction.
- `"adaptive"` — stops when it has gathered enough content to answer a `query`. Pass `query` field.

Response: list of crawled pages (same shape as `fetch_page_full`), each with `depth` and `parent_url` fields.

crawl4ai backing: `BFSDeepCrawlStrategy` / `DFSDeepCrawlStrategy` / `BestFirstCrawlingStrategy` / `AdaptiveCrawler`. Limits from `scraping_config` are applied as `max_depth`, `max_pages`, `include_external` on the strategy object.

---

**Scenario 5 — Screenshots**
> "Take a screenshot of a page for visual verification or multimodal extraction."

```
POST /tools/screenshot_page
```

Request:
```json
{
  "url": "https://example.com",
  "wait_for": ".main-content",
  "full_page": false,
  "scraping_config": { ... }
}
```

Response:
```json
{
  "url": "...",
  "screenshot_base64": "iVBORw0KGgo...",
  "width": 1280,
  "height": 900,
  "duration_ms": 1200
}
```

crawl4ai backing: `CrawlerRunConfig(screenshot=True)` → `result.screenshot` (base64 PNG).

Use cases: visual QA of scraped pages, multimodal LLM extraction (pass screenshot to vision model), debugging rendering issues.

---

**Scenario 6 — JS-heavy pages**
> "The page loads content via React/Vue/Next.js CSR. Need to wait for a selector or execute custom JS before extracting."

This is **already handled** by the existing `fetch_page` endpoint via:
- `wait_for: str` — CSS selector to wait for before extracting (e.g. `"article.post-content"`)
- `js_code: str` — custom JavaScript to execute after page load (e.g. click "Load more", dismiss cookie banner, scroll to bottom)
- `session_id: str` — reuse a Playwright browser session across calls (for multi-step flows like login → navigate → extract)

crawl4ai uses Playwright under the hood, so JS is fully executed before content extraction. This covers React, Vue, Angular, Next.js CSR, SPAs with deferred loading, infinite scroll (via `js_code`), and pages behind overlays (`remove_overlay_elements=True` is already set).

**No new endpoint needed.** The existing `fetch_page` and `fetch_page_full` both accept `wait_for`, `js_code`, and `session_id`.

Additional JS-specific options to add to `fetch_page_full`:
- `scroll_to_bottom: bool = False` — auto-scroll to trigger lazy-loaded content (implemented as `js_code`)
- `proxy: str | None = None` — proxy URL for geo-restricted or anti-bot pages (crawl4ai `BrowserConfig(proxy=...)`)
- `stealth_mode: bool = False` — enable crawl4ai's anti-bot detection evasion (v0.8.5+)

---

##### Summary: all tools after P2-Step 2

| Endpoint | Status | Primary scenario |
|---|---|---|
| `POST /tools/fetch_page` | **Exists** — extend with `scroll_to_bottom`, `proxy`, `stealth_mode` | Single page, text only |
| `POST /tools/fetch_links` | **Exists** — extend with `score_links`, `query`, `max_links` | Links from one page |
| `POST /tools/fetch_page_full` | **New** | Full page: text + media + links + optional screenshot |
| `POST /tools/fetch_pages_batch` | **New** | Parallel multi-page crawl |
| `POST /tools/discover_urls` | **New** | Multi-depth URL discovery (no content, fast) |
| `POST /tools/deep_crawl` | **New** | Full deep crawl with BFS/DFS/BestFirst/Adaptive + content |
| `POST /tools/screenshot_page` | **New** | Screenshot for visual/multimodal use |
| `POST /tools/extract_structured` | **New** | LLM-driven JSON extraction against a caller-supplied schema |
| `POST /tools/extract_structured_no_llm` | **New** | CSS/XPath schema extraction — no LLM, fast, cheap |

#### P2-Step 3: Generic Scraper Agent Graph

**Why**: Decouple scraping logic from the content-curator domain agent.

> **Q: Should the scraper be a separate agent that content-curator calls, or should content-curator connect to the Scraper MCP directly?**
>
> **A: The scraper should be a separate agent (generic_scraper_agent), and content-curator calls it via the Execution Engine — not the MCP directly. Here is why:**
>
> - The Scraper MCP server is a **tool layer** — it exposes raw crawl4ai capabilities with no business logic, no policy enforcement, no deduplication, no normalisation. It is the equivalent of a DB driver.
> - The `generic_scraper_agent` is an **agent layer** — it wraps those MCP tools in a LangGraph graph that adds: policy limit injection, crawl planning (which URLs to visit, which strategy), parallel execution, media extraction, schema normalisation, deduplication, and OTel tracing. This is reusable logic that every domain needs.
> - If `content_curator` called the MCP directly, it would have to re-implement all of that itself. The next domain agent (HR, litigation) would have to do the same. That is the exact duplication the architecture is designed to avoid.
> - The correct call chain is: `content_curator graph node` → `Execution Engine` → `generic_scraper_agent` (HTTP, port 8004) → `Scraper MCP tools` (port 8002).
> - This also means policy limits are enforced once, in the scraper agent, not scattered across every domain agent.
>
> **So yes — refactoring `content_curator/graph.py` to call the generic scraper agent instead of the MCP directly is correct.**

- Create `agents/templates/scraper_graph.py`:
  - `StateGraph` nodes: `plan_crawl` → `execute_crawl` → `extract_media` →
    `normalize_output` → `deduplicate` → `emit_results`.
  - Accepts `ScraperAgentInput(urls, search_queries, target_schema, scraping_limits)`.
  - Returns `ScraperAgentOutput(pages: list[NormalizedPage])`.
- Create `agents/templates/scraper_main.py` — FastAPI wrapper (port 8004).
- Register `generic_scraper_agent` in Agent Registry.
- Register skills: `scrape_urls`, `search_and_scrape`, `extract_media_from_url`.
- Refactor `agents/content_curator/graph.py` to call the generic scraper agent
  instead of calling the scraper MCP directly.

#### P2-Step 4: Generic Content Matcher Agent Graph

**Why**: Product matching logic should be reusable across domains.

- Create `agents/templates/matcher_graph.py`:
  - `StateGraph` nodes: `embed_content` → `vector_search` → `db_lookup` →
    `rerank_candidates` → `emit_matches`.
  - Accepts `MatcherAgentInput(content, entity_type, match_schema, top_k)`.
  - Returns `MatcherAgentOutput(matches: list[MatchResult])`.
- Create `agents/templates/matcher_main.py` — FastAPI wrapper (port 8005).
- Register `generic_content_matcher_agent` in Agent Registry.
- Register skills: `match_content_to_entities`, `vector_search_entities`.
- Refactor `agents/content_curator/graph.py` `match_products` node to call the
  generic matcher agent.

#### P2-Step 5: Verbose Structured Logging

**Why**: Ops and demo need to read the full agent process from logs.

- Create `app/core/logging.py`:
  - `get_logger(service_name)` — returns a structlog/standard logger configured
    for JSON output.
  - `log_node_entry(logger, node, state)` / `log_node_exit(logger, node, state, elapsed_ms)`.
  - `log_tool_call(logger, tool, args, result)`.
  - `log_llm_call(logger, model, prompt_tokens, completion_tokens, latency_ms)`.
  - `log_policy_check(logger, check_type, resource_id, result, reason)`.
  - `log_guardrail_check(logger, guard_type, input_summary, verdict, reason)`.
- All log records include: `execution_id`, `tenant_id`, `step_id`, `service`.
- Log level controlled by `LOG_LEVEL` env var (default `INFO`).
- PII redaction at `INFO` level (raw text/URLs truncated to 100 chars).
- Instrument all existing LangGraph nodes in `agents/content_curator/graph.py`
  and `agents/content_ingestion/` with the new logging helpers.

#### P2-Step 6: Guardrails Library

**Why**: Safety and compliance checks must run inline, before/after every LLM
call and tool invocation.

> **Q: Should guardrails be a shared library used by all modules, or a separate service that all modules call?**
>
> **A: Start as a shared library (`app/guardrails/`). Add a central service only for the checks that genuinely need it. Here is the reasoning:**
>
> **Library-first (what we build now):**
> - Cheap, deterministic checks (schema validation, prompt injection heuristics, scraping limit counters, keyword filters, PII redaction) run in-process with zero network overhead. They are the first line of defence and must never be a bottleneck.
> - Each service imports `app/guardrails/` and calls guards inline — before/after LLM calls and tool invocations. No extra hop.
> - Failures are local and easy to debug; no distributed failure mode.
> - This covers ~90% of what we need for the PoC and near-term production.
>
> **Central guardrail service (add later, for specific cases):**
> - Org-wide safety classifiers (toxicity, self-harm, harassment) that are too heavy to run in every process.
> - Regulatory rules that must be managed centrally and versioned independently of agent code.
> - Cross-tenant policy checks (e.g., "this content is blocked for all tenants in region X").
> - These are called via a small `app/guardrails/client.py` that the library already provides a stub for.
>
> **Decision: build the library now. The central service is a future addition when a specific check outgrows in-process execution.**

- Create `app/guardrails/`:
  - `__init__.py` — exports `InputGuard`, `ToolPolicyGuard`, `OutputGuard`.
  - `input_filters.py`:
    - `validate_json_schema(data, schema)` — raise `GuardrailViolation` if invalid.
    - `check_prompt_injection(text)` — heuristic check, log + raise on detection.
    - `redact_pii(text)` — regex-based PII scrubbing before logging.
  - `tool_policies.py`:
    - `check_tool_allowed(tool_id, effective_policy)` — raise if tool not in allowlist.
    - `check_scraping_limits(url, current_counts, limits)` — raise if any limit exceeded.
  - `output_filters.py`:
    - `check_output_schema(output, schema)` — validate LLM output structure.
    - `check_unsafe_content(text)` — keyword/regex filter for obviously unsafe output.
    - `redact_sensitive_fields(output, sensitive_keys)` — strip secrets before logging.
  - `exceptions.py` — `GuardrailViolation(guard_type, reason, severity)`.
- Integrate guardrails into:
  - `agents/content_curator/graph.py` — wrap each node's tool calls.
  - `tools/scraper_mcp/server.py` — enforce scraping limits before crawl.
  - `app/orchestration/execution_engine.py` — check tool allowlist before dispatch.

---

### Phase 2 Execution Order

| #   | Step                                 | Key output                                                    |
| --- | ------------------------------------ | ------------------------------------------------------------- |
| 1   | P2-Step 1: Scraping Limits in Policy | `ScrapingLimits` model, DB migration, policy enforcement      |
| 2   | P2-Step 5: Verbose Logging           | `app/core/logging.py`, all nodes instrumented                 |
| 3   | P2-Step 6: Guardrails Library        | `app/guardrails/`, integrated into scraper + execution engine |
| 4   | P2-Step 2: Expand Scraper MCP        | N crawl4ai tools, `search_and_crawl`, `extract_media`         |
| 5   | P2-Step 3: Generic Scraper Agent     | `agents/templates/scraper_graph.py`, port 8004                |
| 6   | P2-Step 4: Generic Matcher Agent     | `agents/templates/matcher_graph.py`, port 8005                |

### Phase 2 New Files

- `db/migrations/control/006_scraping_limits.sql`
- `app/core/logging.py`
- `app/guardrails/__init__.py`
- `app/guardrails/input_filters.py`
- `app/guardrails/tool_policies.py`
- `app/guardrails/output_filters.py`
- `app/guardrails/exceptions.py`
- `agents/templates/scraper_graph.py`
- `agents/templates/scraper_main.py`
- `agents/templates/matcher_graph.py`
- `agents/templates/matcher_main.py`

### Phase 2 Modified Files

- `app/domain/policy/models.py` — add `ScrapingLimits`
- `app/domain/policy/engine.py` — include scraping limits in effective policy
- `tools/scraper_mcp/server.py` — N tools + limit enforcement + logging
- `tools/scraper_mcp/helpers.py` — crawl4ai wrappers for new tools
- `agents/content_curator/graph.py` — use generic scraper + matcher agents
- `app/orchestration/execution_engine.py` — guardrail tool-allowlist check
- `scripts/register_agents.py` — register generic scraper + matcher agents
