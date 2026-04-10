# Content Ingestion Job – Agentic PoC Design

Goal of this PoC:

- For each tenant, **periodically ingest** content from a configured list of websites.
- **Incrementally scrape**: skip when there is no new content.
- Create normalized **articles/feeds** using a **tenant-specific article format template** stored in DB.
- Tag each article with up to 6 tags from the tenant’s tag taxonomy.
- Store all steps and metadata in Postgres + vector DB so tenants have a full history.
- Run as a **scheduled job** (cron/Temporal/LangGraph loop), no UI yet.
- Use a **scraper MCP server** (e.g., wrapping `crawl4ai` or similar) for robust scraping.
- Log each step clearly and **track cost** (tokens, scraping time, etc.).

The design follows our persona / skill / policy / registry model.

---

## 1. Scope of this PoC

Out of the full system, this PoC will implement:

- **Personas**
  - `content_ingestion_daemon` (system persona for scheduled jobs).
- **Policy**
  - Tenant‑level policy for ingestion and scraping.
- **Skills**
  - `fetch_tenant_sources`
  - `scrape_source_urls_incremental`
  - `extract_and_normalize_articles`
  - `tag_content_item`
  - `apply_article_format_template`
  - `record_ingestion_log_entry`
- **Agents**
  - `content_ingestion_agent` (LangGraph graph).
  - `content_enrichment_agent` (can be shared with later flows).
- **Tools / MCP**
  - `scraper_mcp_tool` (wrapping `crawl4ai` or similar).
  - `html_to_text_tool` (can be internal lib).
  - `tagging_model_tool`.
  - `cost_tracker_tool` (internal helper / DB table).
- **DB + Memory**
  - Postgres tables for tenants, sources, tags, article formats, ingestion logs, articles, article_tags.
  - Vector DB entries for article embeddings (optional in this first step).

No tenant onboarding UI; instead we use **seed scripts** to populate multiple tenants with different policies, websites, tag taxonomies, and article format templates for testing.

---

## 2. Persona for the ingestion job

### 2.1 Persona: `content_ingestion_daemon`

This persona is used by the orchestrator / planner when running scheduled jobs.

- **Display name**: “Content Ingestion Daemon”
- **Role description**:
  - A background co‑worker responsible for _safe, efficient, incremental_ ingestion of web content for each tenant.
  - It never talks to end‑users; it only reads from configured sources and writes to storage.
- **Goals**:
  - For each tenant, at scheduled intervals:
    - Discover which sources to crawl.
    - Scrape new content from those sources only if changed since the last run.
    - Normalize into article objects using the tenant’s article format template.
    - Tag articles with at most 6 tags from the tenant’s taxonomy.
    - Log all actions and cost metrics.
- **Constraints**:
  - Only scrapes **whitelisted sources** from the DB for that tenant.
  - Cannot post to external social/blog platforms.
  - Must respect rate limits and robots.txt if you enforce it (later).
  - Must not exceed per‑tenant ingestion cost budgets.

---

## 3. Policy for ingestion

Example TenantPolicy for `content_curation_tenant_*` focused on ingestion:

```yaml
tenantId: content_curation_tenant_X

capabilities:
  allowed:
    - fetch_tenant_sources
    - scrape_source_urls_incremental
    - extract_and_normalize_articles
    - tag_content_item
    - apply_article_format_template
    - record_ingestion_log_entry
  blocked: []
  defaultAllow: false

tools:
  allowedTools:
    - scraper_mcp_tool
    - html_to_text_tool
    - tagging_model_tool
    - cost_tracker_tool
  blockedTools: []

security:
  allowExternalApiCalls: true
  allowWebScraping: true
  allowedDataTags:
    - PUBLIC_WEB_CONTENT
  blockedDataTags: []

budget:
  monthlyUsdLimit: 100.0
  perExecutionUsdLimit: 1.0
  maxTokensPerExecution: 50000

evaluation:
  autoEvaluateCapabilities:
    - extract_and_normalize_articles
    - tag_content_item
  minQualityScore: 0.8
  logAllExecutionsForCapabilities:
    - scrape_source_urls_incremental
    - apply_article_format_template
```

The **Planner** must plan _only_ with these ingestion skills when running this job.

---

## 4. Skills for this job

These skills are for the _ingestion pipeline only_.

### 4.1 `fetch_tenant_sources`

- **Purpose**: Fetch list of active sources + tag taxonomy + article format template for this tenant from DB.
- **Input**:
  - `tenant_id: string`
- **Output**:
  - `sources: { id: string; url: string; type: "rss" | "html"; last_scraped_at?: ISO8601 }[]`
  - `tag_taxonomy: string[]` (candidate tags, possibly per‑tenant)
  - `article_format_template: object` (see §5.1)

### 4.2 `scrape_source_urls_incremental`

- **Purpose**: Scrape each source ONLY if there is new content since last run.
- **Input**:
  - `tenant_id: string`
  - `sources: Source[]` (from previous step)
- **Output**:
  - `raw_items: RawItem[]`
    - `source_id: string`
    - `url: string`
    - `raw_html: string`
    - `fetched_at: ISO8601`
- **Logic** (implementation detail, not exposed to planner):
  - For each source:
    - Use `last_scraped_at` + optional ETag/Last‑Modified + previously seen URLs hashes to decide whether to call `scraper_mcp_tool`.
    - If no change, skip and log a “no‑op” ingestion entry.

### 4.3 `extract_and_normalize_articles`

- **Purpose**: Convert raw HTML pages into normalized article objects.
- **Input**:
  - `tenant_id: string`
  - `raw_items: RawItem[]`
- **Output**:
  - `articles: Article[]`
    - `id: string`
    - `source_id: string`
    - `url: string`
    - `canonical_url?: string`
    - `title: string`
    - `author?: string`
    - `published_at?: ISO8601`
    - `text: string`
    - `created_at: ISO8601`

### 4.4 `tag_content_item` (reused)

- **Purpose**: Assign up to 6 tags from the tenant’s tag taxonomy.
- **Input**:
  - `tenant_id: string`
  - `article_id: string`
  - `article_text: string`
  - `tag_taxonomy: string[]`
- **Output**:
  - `tags: string[]` (≤ 6)
  - `tag_confidences: Record<string, number>`

### 4.5 `apply_article_format_template`

- **Purpose**: Map normalized article fields into the tenant’s configured article/feed format.
- **Input**:
  - `tenant_id: string`
  - `article_id: string`
  - `normalized_article: Article`
  - `tags: string[]`
  - `article_format_template: object`
- **Output**:
  - `formatted_article: object` (JSON matching template)
- **Notes**:
  - Template is defined at onboarding (seeded for PoC).
  - For example, template might specify fields like:
    - `headline`, `summary`, `body`, `primary_tag`, `secondary_tags`, etc.

### 4.6 `record_ingestion_log_entry`

- **Purpose**: Persist an ingestion log row per source and per article for full traceability + cost.
- **Input**:
  - `tenant_id: string`
  - `source_id?: string`
  - `article_id?: string`
  - `execution_id: string`
  - `step_name: string`
  - `status: "SUCCESS" | "NO_CHANGE" | "ERROR"`
  - `details: object` (e.g., counts, reasons)
  - `cost_metrics: { tokens_in?: number; tokens_out?: number; usd_estimate?: number; duration_ms?: number }`
- **Output**:
  - `log_entry_id: string`

---

## 5. DB schema extensions for PoC

Add/extend these tables in Postgres (names suggestive, not final):

### 5.1 Tenant config: sources, tags, format

- `tenant_sources`
  - `id UUID PK`
  - `tenant_id UUID`
  - `url TEXT`
  - `type TEXT` (`"rss"` | `"html"`)
  - `active BOOLEAN`
  - `last_scraped_at TIMESTAMPTZ NULL`
  - `last_etag TEXT NULL`
  - `last_content_hash TEXT NULL`
  - `created_at TIMESTAMPTZ`
  - Unique `(tenant_id, url)`.

- `tenant_tags`
  - `id UUID PK`
  - `tenant_id UUID`
  - `name TEXT`
  - `description TEXT`
  - `created_at TIMESTAMPTZ`
  - Unique `(tenant_id, name)`.

- `tenant_article_format_templates`
  - `id UUID PK`
  - `tenant_id UUID`
  - `name TEXT`
  - `template_json JSONB` // describes mapping/fields
  - `is_default BOOLEAN`
  - `created_at TIMESTAMPTZ`.

### 5.2 Articles & tags

- `articles`
  - `id UUID PK`
  - `tenant_id UUID`
  - `source_id UUID`
  - `url TEXT`
  - `canonical_url TEXT`
  - `title TEXT`
  - `author TEXT`
  - `published_at TIMESTAMPTZ`
  - `text TEXT`
  - `created_at TIMESTAMPTZ`
  - Unique `(tenant_id, url)`.

- `article_tags`
  - `article_id UUID`
  - `tag_id UUID`
  - `confidence NUMERIC`
  - PK `(article_id, tag_id)`.

- `formatted_articles`
  - `article_id UUID PK`
  - `tenant_id UUID`
  - `format_template_id UUID`
  - `formatted_json JSONB`
  - `created_at TIMESTAMPTZ`.

### 5.3 Ingestion logs & cost

- `ingestion_executions`
  - `execution_id UUID PK`
  - `tenant_id UUID`
  - `started_at TIMESTAMPTZ`
  - `finished_at TIMESTAMPTZ`
  - `status TEXT` (`"SUCCESS"|"PARTIAL"|"ERROR"`)
  - `summary_json JSONB`.

- `ingestion_log_entries`
  - `id UUID PK`
  - `execution_id UUID`
  - `tenant_id UUID`
  - `source_id UUID NULL`
  - `article_id UUID NULL`
  - `step_name TEXT`
  - `status TEXT`
  - `details_json JSONB`
  - `tokens_in INT`
  - `tokens_out INT`
  - `cost_usd NUMERIC`
  - `duration_ms INT`
  - `created_at TIMESTAMPTZ`.

You can also have `article_embeddings` in the vector DB, but it’s optional for this first PoC.

---

## 6. Scraper MCP design & libraries

### 6.1 `scraper_mcp_tool`

Implement an MCP server wrapping a robust scraper. Reasonable options:

- `crawl4ai` (user‑suggested; supports modern JS sites and structured extraction).
- `Playwright`‑based headless scraping for JS‑heavy pages.
- `requests` + `trafilatura`/`readability-lxml` for simpler static sites.

For PoC, start with:

- `crawl4ai` or `Playwright` for generality.
- `trafilatura` or similar for HTML → text inside the agent (or separate `html_to_text_tool`).

MCP operations:

- `fetch_page({ url, last_etag?, last_modified?, last_content_hash? })`:
  - Returns:
    - `raw_html`
    - `status_code`
    - `etag?`
    - `last_modified?`
    - `content_hash` (e.g., hash of cleaned HTML).

In `scrape_source_urls_incremental`, use these fields plus DB data to detect “no change”.

- Use `scraper_mcp_tool.fetch_page(url)` to fetch a page and extract outgoing links.
- Build a BFS/DFS up to `max_depth`, tracking `visited_urls` to avoid loops.
- Filter outgoing links by:
  - `same_domain_only` and
  - `include_patterns` (if provided).
- Before fetching a URL, check:
  - if `url` already exists in `articles`, or
  - if its `content_hash` matches `last_content_hash` → skip and log a `NO_CHANGE` entry.

---

## 7. LangGraph flow for the ingestion agent

### 7.1 Graph nodes

In `content_ingestion_agent`:

1. **`load_tenant_config`** (implements `fetch_tenant_sources`)
   - Read from Postgres:
     - active `tenant_sources` for `tenant_id`.
     - `tenant_tags` names into `tag_taxonomy`.
     - default `tenant_article_format_templates.template_json`.

2. **`scrape_sources_incremental`** (implements `scrape_source_urls_incremental`)
   - For each source:
     - Check `last_scraped_at` and/or `last_content_hash`.
     - Call `scraper_mcp_tool.fetch_page(...)` **only if** not “unchanged”.
     - Update `tenant_sources.last_scraped_at`, `last_etag`, `last_content_hash`.
   - Emit `raw_items` list.
   - For each source, call `record_ingestion_log_entry` with:
     - `status = "SUCCESS"` or `"NO_CHANGE"`.
     - `duration_ms`, `cost_usd` (scraping time, if you choose to cost it).

3. **`normalize_articles`** (implements `extract_and_normalize_articles`)
   - For each `raw_item`:
     - Run `html_to_text_tool` → title/text.
     - Create/insert into `articles` table (guard against duplicates with `ON CONFLICT`).
   - Log counts.

4. **`tag_and_format_articles`** (combines `tag_content_item` + `apply_article_format_template`)
   - For each new article:
     - Call `tagging_model_tool` with text + `tag_taxonomy`.
     - Take top N tags, truncate to max 6, insert into `article_tags`.
     - Build `formatted_article` from normalized fields + tags using `article_format_template`.
     - Insert into `formatted_articles`.
     - Log tagging + formatting, including tokens and cost.

5. **`summarize_execution`**
   - Aggregate metrics:
     - number of sources scraped, skipped.
     - number of new articles.
     - tokens_in/out, cost.
   - Write a row into `ingestion_executions`.

### 7.2 Logging and cost tracking

- Each node uses a small helper (or `cost_tracker_tool`) to:
  - Capture LLM usage (tokens, model, cost).
  - Measure duration.
  - Insert `ingestion_log_entries` rows.

- OTel:
  - Each execution has a root span:
    - Attributes: `tenant_id`, `execution_id`, `job_type="content_ingestion"`.
  - Each node call is a child span with:
    - `step_name`, `source_id?`, `article_id?`.

---

## 8. Seed script for multi‑tenant testing

Implement a Python seed script (e.g., `scripts/seed_content_tenants.py`) that:

- Inserts multiple test tenants:
  - `tenant_a` (e.g., “AI/ML news”).
  - `tenant_b` (e.g., “Legal tech news”).
- For each tenant:
  - Inserts some `tenant_sources`:
    - `https://example-ai-blog.com`, `https://example-news.com/ai`, etc.
  - Inserts `tenant_tags`:
    - For `tenant_a`: `["llm", "ai_safety", "ml_ops", "vector_db", "agentic_ai", "llm_in_prod"]`.
    - For `tenant_b`: legal/industry tags, etc.
  - Inserts a default `tenant_article_format_templates`:
    - Example template JSON:
      ```json
      {
        "headlineField": "title",
        "summaryField": "auto_generated_summary",
        "bodyField": "text",
        "primaryTagField": "primary_tag",
        "secondaryTagsField": "secondary_tags",
        "includeScore": true
      }
      ```

You can then run the ingestion job for both tenants and compare behavior.

---

## 9. How to implement this in Cursor

Recommended workflow in Cursor:

1. **Add this file**
   - Save as `docs/CONTENT_INGESTION_JOB.md`.
   - Commit it so Cursor can index it.

2. **Update rules**
   - Ensure `.cursor/rules/agentic-architecture.mdc` and `commands-and-workflow.mdc` exist and mention:
     - Python/FastAPI/LangGraph.
     - Postgres/vector DB.
     - This doc as the canonical spec for ingestion.

3. **Drive implementation in stages** (each time referencing this doc with `@file`):
   - **Stage 1 – DB layer**:
     - Prompt:  
       “Using `@file docs/CONTENT_INGESTION_JOB.md`, create SQLAlchemy/Pydantic models and Alembic migration for `tenant_sources`, `tenant_tags`, `tenant_article_format_templates`, `articles`, `article_tags`, `formatted_articles`, `ingestion_executions`, `ingestion_log_entries`.”
   - **Stage 2 – Scraper MCP**:
     - Prompt:  
       “Using the same doc, scaffold a Python MCP server `scraper_mcp_tool` that wraps `crawl4ai` (or requests+trafilatura) with a `fetch_page` method returning `raw_html`, `etag`, `last_modified`, `content_hash`.”
   - **Stage 3 – LangGraph ingestion agent**:
     - Prompt:  
       “Using `@file docs/CONTENT_INGESTION_JOB.md`, implement a `content_ingestion_agent` LangGraph graph with nodes: `load_tenant_config`, `scrape_sources_incremental`, `normalize_articles`, `tag_and_format_articles`, `summarize_execution`. Use Pydantic models and call the MCP scraper + tagging LLM. Add OTel spans and DB writes.”
   - **Stage 4 – Scheduled job wrapper**:
     - Prompt:  
       “Implement a FastAPI/CLI entrypoint `run_ingestion_for_all_tenants()` that:
       - loads all active tenants,
       - starts one `execution_id` per tenant,
       - runs the LangGraph ingestion graph,
       - logs to `ingestion_executions`.”

4. **Iterate with tests**
   - Ask Cursor to generate pytest tests for:
     - Incremental scraping logic (skipping when `content_hash` unchanged).
     - Tag truncation (max 6).
     - Basic DB writes and logging.

## Storage & Multi-Tenant Databases

We use a **control plane DB** (product base DB) and **one data plane DB per tenant**.[web:276][web:277][web:271][web:272][web:280]

### Control plane DB (shared)

Single Postgres database per environment that stores platform-level metadata:

- `tenants`:
  - `tenant_id`, `name`, `status`, `plan`, `region`, `created_at`
- `tenant_db_connections`:
  - `tenant_id`
  - `db_host`, `db_port`, `db_name`, `db_user` (or secret reference)
  - `db_schema` (usually `public`)
- Global registries:
  - Skill Registry (skills table)
  - Agent Registry (agents + supported_skills join)
  - Tool/MCP Registry
- Global policy templates, feature flags, and scheduler metadata.

No tenant business content (articles, tags, HR data, etc.) lives here.

### Tenant DBs (one per tenant)

Each tenant has its own Postgres DB, all sharing the **same logical schema**.[web:271][web:272][web:197][web:280]

For the content ingestion PoC, each tenant DB contains:

- Ingestion config:
  - `tenant_sources`
  - `tenant_tags`
  - `tenant_article_format_templates`
- Content:
  - `articles`
  - `article_tags`
  - `formatted_articles`
- Ingestion runs:
  - `ingestion_executions`
  - `ingestion_log_entries`
- Optional:
  - `article_embeddings` (pgvector) for RAG and similarity.

Because each database is tenant‑scoped, **tables in tenant DBs do not need `tenant_id` columns**. Isolation is achieved at the database level. Deleting a tenant can be implemented as dropping that tenant’s DB.

The orchestrator:

1. Reads the control plane DB to resolve `tenant_id → tenant_db_connection`.
2. Connects to the tenant DB to run the ingestion graph for that tenant only.
3. Writes high-level job status back to the control plane DB if needed.
