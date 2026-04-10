# Agentic Platform Architecture (Multi‑Tenant, Generic‑First)

This document defines the core architecture for our agentic AI platform.

Guiding ideas:

- **Personas & skills are domain‑specific** (business semantics).
- **Agents & tools are generic and reusable**, with clear boundaries.
- Start with **generic agents** composed from shared skills/tools, then evolve to **domain‑specific agents** as patterns stabilize.
- Strong separation of:
  - Control plane vs data plane.
  - Skills vs tools.
  - Planner/orchestrator vs execution engine.
  - Control DB vs tenant DBs.

We use:

- **FastAPI** for API surface.
- **LangGraph** + **LangChain** for agent graphs & tools.
- **PostgreSQL** for control plane & tenant DBs.
- **Vector DB** (pgvector or external) for memory.
- **OpenTelemetry** + **Langfuse** for observability.[web:151][web:343]
- **DeepEval** (and similar) for offline eval/benchmarks.[web:343]
- Other OSS frameworks as references:
  - FastAPI + LangGraph templates.[web:159][web:338][web:344]
  - Claw Code harness ideas (tool system, query engine).[web:320]
  - Microsoft Agent Framework patterns for orchestration & observability.[web:347]

---

## 1. Personas

**What they are**

- A persona defines _who_ the AI is acting as: role, goals, tone, constraints.
- Examples:
  - `content_ingestion_daemon`
  - `b2b_demandgen_coworker`
  - `hr_employee_self_service`
  - `patent_risk_analyst`

**Responsibilities**

- Shape:
  - Which skills should be preferred.
  - Tone/style for outputs.
  - Risk tolerance (conservative vs exploratory).
- Provide domain hints to planner:
  - Target audience, verticals, KPIs.

**Boundaries (what personas MUST NOT do)**

- No business logic or procedural steps (that’s skills).
- No direct access to tools or data.
- No per‑tenant configuration (sources, product mappings, etc.) beyond high‑level preferences.

**Storage**

- Control plane DB table `personas` keyed by `(tenant_id, persona_id)` or global + overrides.
- Referenced by orchestrator at the start of each request.

---

## 2. Skills

**What they are**

- **Domain‑specific, reusable units of work** (abstract workflows), NOT tools.[web:74][web:79][web:80][web:82]
- Express _what_ to do, not _how_ to call HTTP endpoints.
- Examples:
  - `scrape_tenant_sources`
  - `extract_candidate_pages`
  - `score_article_for_b2b_exec_relevance`
  - `match_products_to_article`
  - `triage_hr_request`
  - `analyze_npe_campaign_risk`

**Responsibilities**

- Define:
  - Name + description.
  - Input/output JSON Schemas.
  - Hints/steps (e.g. SKILL.md or doc).
  - Tags (domain, risk, read‑only vs write).

**Boundaries**

- MUST NOT embed:
  - Tool endpoints, credentials, or low‑level HTTP logic.
  - Tenant‑hardcoded values (URLs, thresholds).
- MUST be stable contracts the planner & policies can reference.
- Extension is **via config** (per‑tenant weights, examples, prompts), not schema churn for every tenant.

---

## 3. Skill Registry

**What it holds**

- A global catalog of skills with schemas and metadata:
  - `skills`:
    - `id`, `name`, `description`
    - `input_schema_json`, `output_schema_json`
    - `tags[]`, `safety_level`, `created_at`
- Optionally:
  - Example IO pairs.
  - Reference docs.

**Responsibilities**

- Single source of truth for which skills exist.
- Planner’s “action vocabulary”.
- Policy engine’s “capability list”.

**Boundaries**

- Registry **does not** know:
  - Which agents implement a skill (that’s Agent Registry).
  - Tenant policies or usage limits (policy service).
- No domain execution code here; it’s metadata only.

---

## 4. Agents

**What they are**

- **Runtime containers** that execute skills using tools, within a LangGraph graph.
- Bind together:
  - Persona context.
  - Skill implementations.
  - A tool set.
  - A control loop (ReAct, graphs, multi‑agent workflows).

We’ll start with **generic agents** (e.g. ingestion, recommender, summarizer) and later add **domain‑specialized instances** (HR, litigation, etc.), using the same underlying templates.

**Examples**

- Generic‑first agents:
  - `generic_ingestion_agent`:
    - Implements `scrape_tenant_sources`, `normalize_page_to_internal_article`.
  - `generic_recommendation_agent`:
    - Implements `match_items_to_context` (can be reused for products, content).
  - `generic_scraper_agent`:
    - A highly capable, multi‑tool scraping agent (see §4.1 below).
  - `generic_content_matcher_agent`:
    - Matches scraped content to tenant entities (products, services, topics) via
      vector search or DB lookup (see §4.2 below).
- Later, domain instances:
  - `b2b_content_ingestion_agent` (instance of ingestion template).
  - `hr_kb_ingestion_agent`.
  - `litigation_docket_ingestion_agent`.

### 4.1 Generic Scraper Agent

The generic scraper agent is a **reusable, highly capable web acquisition worker**.
It is not tied to any domain; any skill that requires fetching content from the
web routes through it.

**Capabilities**

- Web search (via search tool: SerpAPI, Brave Search, etc.).
- Deep crawl with configurable depth, link budget, and domain rules (enforced by
  the tenant’s `scraping_limits` policy — see §12.1).
- Content extraction:
  - Structured text, Markdown, raw HTML.
  - Images, video embeds, and hyperlinks.
  - Metadata (title, author, published date, canonical URL).
- Schema‑driven normalisation: the caller passes a target JSON schema and the
  agent normalises each scraped page to that schema using an LLM extraction step.
- Deduplication: skips URLs already present in the tenant’s article store.

**Implementation**

- Backed by a **Scraper MCP server** (`tools/scraper_mcp/`) built with **FastMCP**.
- The MCP server exposes N tools, each wrapping a `crawl4ai` capability:
  - `crawl_url(url, config)` — single‑page deep crawl.
  - `search_and_crawl(query, max_results, config)` — web search + crawl top N.
  - `extract_links(url)` — extract all links from a page.
  - `extract_media(url)` — extract images and video embeds.
  - `normalize_to_schema(raw_content, target_schema)` — LLM‑based extraction.
- The agent graph (`agents/templates/scraper_graph.py`) wraps these MCP tools
  in a LangGraph `StateGraph`:
  - `plan_crawl` → `execute_crawl` (parallel per URL) → `extract_media` →
    `normalize_output` → `deduplicate` → `emit_results`.
- Scraping limits from `EffectivePolicy.scraping_limits` are injected into every
  MCP tool call as part of the invocation config.

**Reuse**

Any skill that needs web content calls this agent via the Agent Registry. Domain
agents (content curation, HR KB ingestion, litigation docket scraping) compose
this agent as a sub‑graph or call it via the Execution Engine.

### 4.2 Generic Content Matcher Agent

The generic content matcher agent **links scraped content to tenant entities**
(products, services, topics, people, cases, etc.) without being domain‑specific.

**Capabilities**

- **Vector search**: embed the content and query the tenant’s vector store for
  semantically similar entities.
- **DB lookup**: structured search against tenant DB tables (products, KB articles,
  cases, etc.) using keyword or metadata filters.
- **LLM re‑ranking**: use an LLM to score and select the best matches from
  candidates returned by vector/DB search.
- **Configurable match schema**: the caller specifies what entity type to match
  against and what fields to return.

**Implementation**

- Agent graph (`agents/templates/matcher_graph.py`):
  - `embed_content` → `vector_search` → `db_lookup` → `rerank_candidates` →
    `emit_matches`.
- Uses:
  - Memory service (`app/domain/memory/`) for vector search.
  - Tenant DB connection (resolved by Orchestrator) for structured lookup.
  - LLM (via LangChain) for re‑ranking.
- Match results are typed Pydantic models; the schema is passed in the skill’s
  `input_spec` so the same agent can match products, HR policies, or legal cases.

**Reuse**

- `content_curator` agent uses it to match articles to tenant products.
- Future HR agent uses it to match employee queries to KB articles.
- Future litigation agent uses it to match news to active cases.

**Responsibilities**

- Execute plans at the _task_ level:
  - For one `execution_id`, run the graph for the requested skills.
- Read tenant config from tenant DB (sources, tags, thresholds).
- Call tools (scraper, DB, vector store, etc.) with correct scopes.
- Emit telemetry & cost data.

**Boundaries**

- MUST NOT:
  - Implement their own registries or policy logic.
  - Decide arbitrarily which new skills exist (that’s registry + ops).
- Should be **template‑driven**:
  - Reuse same graph structure across multiple logical agents with different configs.

---

## 5. Agent Registry

**What it holds**

- Metadata about each logical agent:
  - `agents`:
    - `id`
    - `display_name`
    - `description`
    - `endpoint`/invocation method (local module, MCP server, HTTP, etc.)
    - `protocol` (e.g. `"local_langgraph"`, `"mcp"`, `"http_json"`)
    - `collections` / tags / domain (`"ingestion"`, `"hr"`, `"litigation"`)
    - `health_status`, `latency_estimate`, `cost_profile`

  - `agent_supported_skills`:
    - `(agent_id, skill_id, quality_score, cost_tier, created_at)`

**Responsibilities**

- Let orchestrator + planner answer:
  - “Which agents can implement this skill for this tenant?”
- Support multiple agents per skill (cheap vs premium, tenant‑local, experimental etc.).

**Boundaries**

- No business rules (policy engine decides which agents are allowed).
- No tool details (Tool Registry handles that).
- Health data is shallow (OK/degraded/offline), detailed logs come from observability stack.

---

## 6. Tools

**What they are**

- **Execution primitives**: HTTP clients, DB clients, vector search, web scraper, HRIS client, Jira client, etc.[web:82][web:331]
- Typically exposed as:
  - MCP servers, or
  - LangChain tools.

**Examples**

- `web_scraper_tool`
- `web_search_tool`
- `html_normalizer_tool`
- `product_catalog_tool`
- `hris_read_tool`
- `patent_litigation_db_tool`

**Responsibilities**

- Do one concrete thing with clear IO and side‑effect semantics.
- Provide JSON schema for arguments and result.
- Attach risk metadata:
  - read‑only / write / destructive.
  - scope / data tags (e.g. `PUBLIC_WEB_CONTENT`, `CLIENT_CONFIDENTIAL`).

**Boundaries**

- Tools do **not**:
  - understand personas,
  - implement domain scoring,
  - manage tenants or policies.
- Tools are **generic** and reused across domains.

Tool metadata lives in a **Tool Registry** in the control plane.

---

## 7. Planner

**What it does**

- Given:
  - `goal` (user/task request),
  - `persona`,
  - `allowedSkills` (filtered by policy),
- Produces a **plan graph**:
  - Steps:
    - `step_id`
    - `skill_id`
    - `input_spec`
    - dependencies (`depends_on`)
    - parallelization hints.

**Responsibilities**

- Decide **which skills** to use and in what order (not which tools or DBs).
- Respect:
  - persona preferences (e.g. “prefer B2B exec‑relevant content”).
  - policy constraints (no write skills when disabled).
- Generate:
  - small, composable DAGs suitable for LangGraph.

**Boundaries**

- Planner:
  - Does NOT call tools or agents directly.
  - Does NOT know tenant DB topology.
- It is a reasoning layer only; execution is delegated to the Execution Engine.

We can implement this with a “planner agent” using LangGraph or LangChain’s planning patterns.

---

## 8. Guardrails (“Guard”)

**What they do**

- Provide **safety & compliance enforcement** around:
  - Input content (prompt injection, PII, banned topics).
  - Tool calls (destructive operations).
  - Output content (toxicity, hallucination, brand compliance).

**Responsibilities**

- Pre‑processing:
  - Validate user input & retrieved content against policies.
- In‑flight:
  - Validate tool decisions (e.g. forbid calling `hris_write_tool` outside approved flows).
- Post‑processing:
  - Redact or block outputs that violate safety/compliance.

**Boundaries**

- Guards **don’t plan**; they veto or transform.
- Domain‑specific safety rules (e.g., “never give legal advice”) belong in:
  - guardrail config + skills, not in every agent.

We can integrate:

- Model‑based guardrails (e.g. classifier LLM tool).
- Pattern‑based guardrails (regex, allowlists).
- External guardrail services.

### 8.1 Guardrails deployment model

Guardrails are implemented as a **shared library first**, with the option of a
central guardrail service for heavier or organization‑wide checks.

#### In‑process guardrails (library)

We maintain a Python package under `app/guardrails/` that every service
(control plane, agents, tools) imports. It includes:

- `input_filters.py`
  - JSON schema validation.
  - Prompt injection heuristics.
  - Simple PII/secret redaction checks.
- `tool_policies.py`
  - Per‑tool and per‑skill allow/deny rules.
  - Enforcement of TenantPolicy / PersonaPolicy (e.g., no write tools for
    read‑only personas).
- `output_filters.py`
  - Length/format checks.
  - Basic unsafe content filters (regex/keyword‑based).
  - Redaction of sensitive fields before logging.

These checks run **inline** in each service, before/after LLM calls and tool
invocations. They are cheap, deterministic, and executed close to the code
that owns the credentials and semantics.

#### Optional central guardrail service

For heavier, cross‑cutting policies (e.g., org‑wide safety classifiers,
regulatory policies, or multi‑modal checks), we can introduce a
`guardrails-service` microservice.

- Services use a small client (e.g., `app/guardrails/client.py`) to:
  - Submit candidate inputs/outputs for review.
  - Receive allow/deny + rationale.
- Typical use cases:
  - Advanced toxicity / self‑harm / harassment classifiers.
  - Regulatory rules that must be managed centrally.
  - Cross‑tenant policy checks.

The call pattern is:

1. Run **local guardrails** first (cheap filters).
2. For configured flows, optionally call the central guardrail service.
3. If either denies, the agent/orchestrator blocks, transforms, or escalates
   to human review.

All modules (control plane, agents, tools) reuse the same guardrail library and
optionally the same central service instead of each implementing their own ad‑hoc
checks.

---

## 9. Evaluation (Eval)

**What it does**

- Quantitatively evaluate agents/skills on offline or online datasets.[web:343][web:348]

**Responsibilities**

- Offline:
  - Use **DeepEval** (or similar) to define metrics:
    - relevance, faithfulness, toxicity, task success.
  - Attach tests per skill/agent (Pytest integration).
- Online:
  - Use **Langfuse** (or similar) to:
    - log traces,
    - attach labels,
    - run light‑weight online checks / A/Bs.[web:343][web:348]
- Store results:
  - link eval scores back to:
    - skill versions,
    - agent configs,
    - tenant configs.

**Boundaries**

- Eval framework:
  - No business logic, no orchestration.
- It’s **used by ops/devs**, not by runtime agents themselves (except maybe for self‑reflection in advanced flows).

### 9.1 Evaluation deployment model

Evaluation is treated as a **data product** that drives improvement, not as a
blocking dependency for every call.

We use a hybrid of inline checks, trace‑based evaluation, and offline
benchmarking.

#### Inline runtime checks (lightweight)

Each agent service can import `app/eval/runtime_checks.py` to run **cheap
per‑request checks**, for example:

- JSON / schema correctness.
- Simple business rules (e.g., article has ≤ 3 product recommendations, required
  fields are present).
- Optional single LLM‑as‑judge call for very high‑stakes responses, within
  strict latency and budget constraints.

These checks run in‑process and can trigger:

- Local reflection / repair (extra graph step).
- Human review requirement.
- Hard failure for obviously invalid outputs.

They are **not** meant to perform full quality benchmarking.

#### Trace‑based evaluation (external)

All services emit rich traces and spans to the observability stack
(Langfuse + OTel). Evaluation pipelines consume those traces:

- A background **eval job or microservice**:
  - Reads traces from Langfuse / telemetry store.
  - Runs LLM‑as‑judge metrics and other checks (via DeepEval or similar) on a
    sample of executions.
  - Persists scores and labels back into:
    - Langfuse (for exploration and dashboards).
    - An eval/quality table (for aggregation per agent/skill/version/tenant).

This path is **asynchronous** and does not block user requests. It provides
signal for:

- Quality dashboards and alerts.
- Comparing agent/skill versions.
- Deciding when to roll out or roll back changes.

#### Offline benchmarks and CI

We maintain `tests/eval/` with DeepEval or similar suites:

- Golden datasets with expected behaviors for each skill/agent.
- Metrics for:
  - relevance, faithfulness, policy adherence, task success, latency.

These run in CI and scheduled jobs. Failing metrics can **block deploys** or
flag regressions before they hit production.

#### Using eval to improve the system

Evaluation results are used to improve agents and orchestration via
**configuration and versioning**, not via ad‑hoc runtime mutation:

- A separate “quality controller” process (scripts or service) aggregates eval
  scores and:
  - updates prompts and skill configs,
  - adjusts routing weights in the Agent Registry (which agents to prefer),
  - toggles feature flags (enabling/disabling certain agents or skills),
  - updates TenantPolicy defaults when needed.

- The orchestrator and planner **read these updated configs** on subsequent
  requests. They do not call evaluation services directly on every run.

This keeps the feedback loop:

1. Agents → traces + metrics + eval scores.
2. Eval pipeline → aggregated quality signals.
3. Quality controller → versioned config changes (prompts, skill weights,
   routing, policies).
4. Orchestrator/planner → new behavior via updated registries/policies.

Agents never self‑modify their own prompts/policies at runtime based solely on
raw eval scores; all improvements go through a governed, versioned process.

---

## 10. Observability

**What it covers**

- Traces, metrics, logs, cost for:
  - Orchestrator.
  - Planner.
  - Execution Engine.
  - Agents & tools.

**Stack**

- **OpenTelemetry** instrumentation (FastAPI, LangGraph, LangChain).[web:347][web:337]
- Export to:
  - Tracing backend (Jaeger/Tempo).
  - Metrics backend (Prometheus/Grafana).
  - **Langfuse** for LLM‑specific traces and spans.[web:151][web:343]

**Responsibilities**

- For each `execution_id`:
  - Root span:
    - `tenant_id`, `persona_id`, `agent_id`, `skills[]`.
  - Child spans:
    - Planner run.
    - Each agent step.
    - Each tool call.
- Metrics:
  - Latency per agent/skill.
  - Error rates.
  - Token & cost metrics per tenant and per skill.
- Logs:
  - Structured logs with correlation IDs.

**Boundaries**

- Observability stack is read‑only; it **never changes behavior**.
- No PII/plain secrets in logs (guardrails/filters in place).

### 10.1 Verbose Structured Logging

Every service emits **structured JSON logs** at multiple verbosity levels so
that the full agent process can be read and demoed without a tracing UI.

**Log levels and what they capture**

| Level | Audience | Content |
|---|---|---|
| `INFO` | Ops / demo | High‑level step transitions: `[scraper] crawling https://...`, `[matcher] matched 3 products`. |
| `DEBUG` | Dev / debug | Full inputs/outputs for each node, tool arguments, LLM prompts (truncated), policy decisions. |
| `TRACE` | Deep debug | Raw HTTP requests/responses, token counts, full LLM completions, vector search scores. |

**Implementation rules**

1. Every LangGraph node emits an `INFO` log at entry and exit with:
   - `execution_id`, `tenant_id`, `step_id`, `node_name`, `elapsed_ms`.
2. Every tool call emits a `DEBUG` log with:
   - tool name, arguments (PII‑scrubbed), result summary.
3. Every LLM call emits a `DEBUG` log with:
   - model, prompt token count, completion token count, latency.
4. Policy decisions (allow/deny) emit `INFO` logs with:
   - `policy_check`, `skill_id`/`tool_id`, `result`, `reason`.
5. Guardrail checks emit `INFO` logs with:
   - `guard_type`, `input_summary`, `verdict`, `reason`.
6. All logs include a `correlation_id` (= `execution_id`) for easy filtering.

**Log format (JSON)**

```json
{
  "timestamp": "2026-04-08T10:00:00Z",
  "level": "INFO",
  "service": "content_curator",
  "execution_id": "exec-abc123",
  "tenant_id": "t001",
  "step_id": "step-2",
  "node": "match_products",
  "event": "node_complete",
  "elapsed_ms": 342,
  "summary": "Matched 2 products to article \"AI in Supply Chain\""
}
```

**Configuration**

- Log level is set per service via env var `LOG_LEVEL` (default `INFO`).
- For demos, set `LOG_LEVEL=DEBUG` to see full step‑by‑step agent reasoning.
- Log output goes to stdout (structured JSON) and is forwarded to the
  observability stack (OTel log exporter).
- PII fields (email, names, raw article text) are redacted at `INFO` level;
  full content is only emitted at `TRACE` level with explicit opt‑in.

---

## 11. Cost

**What we track**

- For each LLM/tool call:
  - `tokens_in`, `tokens_out`, `estimated_cost_usd`.
- Summed per:
  - `execution_id`, `tenant_id`, `agent_id`, `skill_id`.

**Implementation**

- Central **query engine** (like Claw Code’s) wraps all LLM calls:[web:320]
  - Model abstraction (OpenAI, Anthropic, etc.).
  - Cost calculation (based on provider prices).
  - Limits:
    - per request,
    - per tenant,
    - per persona/skill.

- Persist cost snapshots:
  - Control plane:
    - aggregated usage per tenant.
  - Tenant DB:
    - detailed per‑execution cost in `ingestion_log_entries` or similar.

**Boundaries**

- Cost engine doesn't know domain semantics, only:
  - model prices,
  - call counts,
  - tokens.
- Policy decides what to do when budgets are exceeded.

---

## 12. Policies / Rules

**What they define**

- Which skills, tools, and agents a tenant/persona can use.
- Budget constraints, rate limits, dangerous capabilities.
- Governance (human approval) for certain operations.
- **Scraping limits** per tenant (see §12.1 below).

**Structure**

- **TenantPolicy**:
  - `capabilities`: allowed/blocked skill IDs, tags.
  - `tools`: allowed/blocked tool IDs.
  - `agents`: allowed/blocked agent IDs.
  - `budget`: per‑month/per‑execution cost & token limits.
  - `security`: web scraping allowed, external APIs, sensitive data tags.
  - `governance`: which skills/tools require human approval.
  - `scraping_limits`: per‑tenant scraping quotas and crawl constraints (see §12.1).

- **PersonaPolicyOverride**:
  - Per persona adjustments; merged into an **EffectivePolicy**.

**Boundaries**

- Policies are **control‑plane data**, not prompts.
- Enforcement happens:
  - before planning (skills list),
  - before agent selection (allowedAgents),
  - before/during tool calls (allowedTools & security),
  - before final side‑effects (approval gates).

### 12.1 Scraping Limits (per‑tenant)

Each tenant has a `scraping_limits` block inside their `TenantPolicy`. These
limits are enforced by the Scraper MCP server and the generic ingestion agent
**before** any crawl begins. Exceeding a limit causes the tool call to be
rejected with a policy violation error (logged and traced).

```json
{
  "scraping_limits": {
    "max_depth": 3,
    "max_links_per_page": 20,
    "max_total_links": 200,
    "allow_external_domains": false,
    "allow_subdomains": true,
    "allowed_domains": [],
    "blocked_domains": [],
    "max_concurrent_requests": 5,
    "request_delay_ms": 500
  }
}
```

| Field | Description |
|---|---|
| `max_depth` | Maximum crawl depth from the seed URL. |
| `max_links_per_page` | Maximum links to follow from a single page. |
| `max_total_links` | Hard cap on total URLs scraped per execution. |
| `allow_external_domains` | Whether the crawler may follow links to other domains. |
| `allow_subdomains` | Whether subdomains of the seed domain are permitted. |
| `allowed_domains` | Explicit allowlist (overrides external domain check). |
| `blocked_domains` | Domains that must never be visited. |
| `max_concurrent_requests` | Parallelism cap for the crawler. |
| `request_delay_ms` | Minimum delay between requests to the same host. |

These fields live in the control plane DB (`tenant_policies.scraping_limits_json`)
and are loaded by the Orchestrator when it builds the `EffectivePolicy` for
a scraping‑related execution. The Scraper MCP tool receives them as part of its
invocation context and enforces them locally.

---

## 13. Orchestrator

**Role**

- “Conductor” between client, planner, policy, registries, and execution engine.[web:337][web:347]

**Responsibilities**

- Entry point from FastAPI or background jobs.
- For each request/job:
  - Resolve `tenant_id`, `persona_id`.
  - Load Persona & EffectivePolicy.
  - Fetch candidate skills from Skill Registry, filter by policy.
  - Call Planner → get plan graph (skill DAG).
  - Select agents via Agent Registry for each skill step.
  - Pass plan + context to Execution Engine.
- Handle:
  - top‑level error handling,
  - trace and execution_id creation.

**Boundaries**

- Orchestrator:
  - Does NOT implement domain logic or call tools directly.
  - Is stateless in business terms; state is in DBs and memory services.

---

## 14. Execution Engine

**Role**

- Run plan graphs (from Planner) using LangGraph workflows.

**Responsibilities**

- For each plan:
  - Build a LangGraph graph (or load from template) wired to agents and tools.
  - Provide execution context (tenant DB connections, memory, config).
  - Run nodes with concurrency where allowed.
- Manage:
  - retries, timeouts, idempotency.
  - partial failures and fallbacks (circuit breakers).
- Write:
  - step outputs,
  - execution status,
  - logs.

**Boundaries**

- Engine doesn’t decide _which_ skills to run (planner) or which agents are allowed (policy).
- It is purely runtime orchestration and doesn’t own business rules.

---

## 15. Context Engineering

Context engineering is critical for reliable behavior. It’s the discipline of
deciding _what_ information to send to the model (and in what structure) so
that agents behave predictably across domains, tenants, and tools.

We treat context as a first‑class design concern, not an afterthought.

### 15.1 Layers of context

We distinguish several layers:

1. **Global system context**
   - Infrastructure rules, safety constraints, logging expectations.
   - E.g., “Never execute shell commands without using the approved tools and
     logging the action”, “Always obey policy engine decisions, do not work
     around them.”
   - Encoded in shared system prompts/templates and guardrail configs.

2. **Persona & domain context**
   - Who the agent is acting as (HR coworker, B2B demand‑gen coworker,
     litigation analyst).
   - Domain vocabulary, target audiences, and goals.
   - Provided as:
     - Persona record (role, goals, tone, examples).
     - Domain‑specific instructions documents (like `AGENT.md` / `CLAUDE.md`).

3. **Tenant context**
   - Tenant‑specific configuration:
     - Sources and scraping rules.
     - Product catalogs.
     - Article schema templates.
     - Policies and thresholds.
   - Read from control plane DB (for identity & routing) and tenant DBs
     (for domain data and configs), never hard‑coded into prompts.

4. **Task / query context**
   - The specific goal for the current execution:
     - “Ingest today’s B2B articles and propose 3 product‑anchored posts.”
     - “Summarize HR policy changes for employees in APAC.”
   - Combined with:
     - small slice of conversation history,
     - current plan step,
     - most relevant retrieved documents.

5. **Tool & state context**
   - Tool capability descriptions and schemas.
   - LangGraph state (what this node has already produced).
   - This is where we tell the LLM exactly how to call tools, and how to
     interpret their outputs.

### 15.2 Practices and patterns

We adopt several concrete practices drawn from Anthropic’s “effective context
engineering” guidance and open-source agent frameworks:

1. **Instruction files per agent / domain**
   - Each agent/domain can have a local document (e.g., `AGENT.md`,
     `HR_COWORKER.md`) in `docs/` that:
     - Describes its role and constraints.
     - Provides 2–3 worked examples.
     - Lists “Do” / “Don’t” rules.
   - Agents load these on startup and incorporate them into their system prompts
     instead of duplicating instructions across code.

2. **Structured state instead of ad‑hoc prose**
   - All internal state passed between nodes is strongly typed:
     - Pydantic models,
     - JSON objects matching skill IO schemas.
   - Nodes in LangGraph work against this typed state; prompt templates refer to
     specific fields (e.g., `article.headline`, `article.summary`) rather than
     dumping entire blobs of text into the prompt.

3. **Retrieval‑first, then compression**
   - For long histories or large document sets:
     - Retrieve top‑K relevant items from vector stores.
     - Optionally rerank.
     - Compress (map/reduce) before inserting into prompts, with explicit
       instructions on what to keep (facts, constraints) vs what to drop.

4. **Context budgets**
   - We treat context window and budget as constraints:
     - Define max tokens for:
       - system + persona,
       - retrieved context,
       - conversation history,
       - tool descriptions.
     - If too much context is available:
       - apply summarization or prioritization (e.g.,
         drop lowest‑relevance docs).
   - This is implemented as a shared “context manager” utility that all agents
     use, so decisions are consistent.

5. **Explicit tool and schema descriptions**
   - Tools are described in prompts with:
     - clear natural language summaries,
     - examples of correct/incorrect usage,
     - argument schemas and return shapes.
   - We avoid ambiguous tool descriptions so models know when _not_ to call a
     tool.

6. **Guardrails integrated into context**
   - Safety and compliance instructions are included in system prompts, and
     reinforced via guard tools:
     - “If asked about X, you must call `policy_guard_tool` first.”
   - Guardrail outputs are treated as context for later steps (e.g., “Policy
     check result: APPROVED with conditions A, B, C”).

7. **Eval‑driven prompt tuning**
   - Use DeepEval + Langfuse traces to:
     - capture failing examples (hallucinations, off‑policy actions),
     - iteratively refine prompts and context selection logic.
   - Keep prompt versions in source control and tie them to eval scores so we
     understand how context changes impact behavior.

### 15.3 Context engineering in code

Implementation details:

- We keep prompt templates and context assembly logic in dedicated modules under
  `app/domain/` (e.g., `app/domain/context/`):
  - `system_prompt_builder.py`
  - `persona_prompt_builder.py`
  - `retrieval_context_builder.py`
- Graph nodes call these builders rather than inlining large prompts.
- We use LangChain’s prompt templates and output parsers where helpful, but the
  core “what goes into context” decisions are ours, guided by data and eval.
- For more complex agents or “meta‑agents” that dynamically build graphs, we
  still:
  - constrain their context to a known set of skills/tools,
  - provide clear, structured state and tool descriptions,
  - and guard them with policies and eval tests.

The goal is that context construction is:

- **Explicit** (lives in code & docs, not scattered prompts),
- **Tested** (via DeepEval and Langfuse-filtered traces),
- **Evolvable** (we can change retrieval/summary/ordering without touching
  business logic or tools).

---

## 16. Multi‑Tenant

**DB layout**

- **Control plane DB**:
  - Tenants, DB connections, registries, global policies, scheduler.[web:276][web:277][web:280]
- **One DB per tenant**:
  - Same schema across tenant DBs.
  - Tenant data: sources, content, HR records, cases, etc.

**Runtime**

- Orchestrator:
  - Resolves `tenant_id → tenant_db_connection` from control plane.
- Execution:
  - Connects to tenant DB to run graphs.
- Isolation:
  - No `tenant_id` column in tenant DB tables (DB boundary is tenant boundary).
  - Per‑tenant vector stores or namespaces.

---

## 17. Memory

**Types**

- **Short‑term**:
  - Execution‑level context:
    - step outputs,
    - recent messages,
    - ephemeral state.
  - Stored in:
    - LangGraph checkpointing (Postgres),
    - `ingestion_executions` + step tables.

- **Long‑term**:
  - Semantic memory via vector DB (pgvector or external).
  - Objects:
    - docs, KBs, prior articles, cases, tickets, etc.
  - Namespaced per tenant (and per coworker if needed).[web:279]

**Memory Service**

- Single “Memory API” that agents call:
  - `retrieve(query, filters)`,
  - `store(document, metadata)`.
- Enforces:
  - tenant scopes,
  - data tags,
  - policy constraints.

---

## 18. Boundaries Summary

To avoid bloat and role confusion:

- **Persona**:
  - Domain identity, goals, tone.
  - No tools, no logic.
- **Skill**:
  - Domain behavior contracts (name, IO schema, description).
  - No tool endpoints, no tenant hardcoding.
- **Policy**:
  - Who can use which skills/agents/tools, when, and with what budgets.
  - No execution or tool calling.
- **Agent**:
  - Executes skills using tools, per template graph.
  - Reads config, but doesn’t own registries or policies.
- **Tool**:
  - Pure capability: “call this API / DB / scraper”.
  - No persona, no domain judgement.

---

## 19. Libraries and Project Structure

### Libraries

- **LangChain**:
  - Tool integration, LLM wrappers, RAG.[web:345][web:82]
- **LangGraph**:
  - Agent graphs, workflows, multi‑agent collaboration.[web:159][web:338][web:344][web:340][web:346]
- **FastAPI**:
  - HTTP API surface.
- **Langfuse**:
  - Tracing, metrics, lightweight eval.[web:151][web:343][web:348]
- **DeepEval**:
  - Offline eval/benchmarking integrated with Pytest & LangChain/Graph.[web:343]
- **OTel**:
  - General tracing & metrics.
- **pgvector**:
  - Vector storage in Postgres for memory.
- **Agent frameworks for inspiration**:
  - Claw Code (harness, tool system, query engine).[web:320]
  - Microsoft Agent Framework (multi‑agent orchestration, observability).[web:347]
  - OpenAI Swarm / Autogen / other multi‑agent frameworks for patterns.[web:342][web:336]

### Repo structure (inspired by FastAPI+LangGraph templates & Claw Code)[web:159][web:151][web:338][web:344][web:320][web:347]

Suggested monorepo layout:

```text
.
├─ app/                       # FastAPI + control plane
│  ├─ main.py                 # App entrypoint
│  ├─ api/                    # Routers (HTTP endpoints)
│  ├─ core/                   # settings, logging, OTel, query engine
│  ├─ domain/
│  │   ├─ personas/
│  │   ├─ skills/
│  │   ├─ policy/
│  │   ├─ registry/
│  │   │    ├─ skill_registry.py
│  │   │    ├─ agent_registry.py
│  │   │    └─ tool_registry.py
│  │   └─ memory/             # Memory service (vector + docs)
│  ├─ orchestration/
│  │   ├─ orchestrator.py
│  │   ├─ planner.py
│  │   └─ execution_engine.py # LangGraph integration
│  └─ telemetry/              # Langfuse + OTel wiring
│
├─ agents/                    # Agent graphs (data plane)
│  ├─ templates/              # Generic agent templates (ingestion, recommender, etc.)
│  │   ├─ ingestion_graph.py
│  │   ├─ recommender_graph.py
│  │   └─ ...
│  ├─ b2b/                    # Domain instances (later)
│  │   └─ content_ingestion.py
│  ├─ hr/
│  └─ litigation/
│
├─ tools/                     # MCP servers & tool wrappers
│  ├─ web_scraper_mcp/
│  ├─ product_catalog_mcp/
│  ├─ hr_kb_mcp/
│  └─ ...
│
├─ infra/
│  ├─ migrations/             # Alembic / SQL migrations for control plane
│  ├─ tenant_migrations/      # Schema migrations applied to each tenant DB
│  ├─ docker/
│  └─ k8s/
│
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ eval/                   # DeepEval tests, test datasets
│
├─ docs/
│  ├─ AGENTIC_PLATFORM_ARCHITECTURE.md
│  ├─ AGENTIC_ARCHITECTURE.md
│  └─ <domain_specs>.md
│
└─ .cursor/
   ├─ rules/
   ├─ skills/
   └─ environment.json
```

This borrows from:

- FastAPI + LangGraph production templates (clear `app/` + `agents/` separation).[web:159][web:338][web:344]
- Claw Code’s separation between core runtime, tools, and commands.[web:320]
- Microsoft Agent Framework’s split of orchestration, agents, and infra.[web:347]

---

## 20. Starting with Generic Agents

Phase 1:

- Implement **generic agents** using `agents/templates`:
  - `ingestion_graph.py`
  - `recommender_graph.py`
  - `summarizer_graph.py`
- Register them in Agent Registry as:
  - `generic_ingestion_agent`
  - `generic_recommender_agent`
- Use per‑tenant config (skills + policy + templates) to _specialize behavior_ without new code.

Phase 2:

- Once patterns for HR, litigation, B2B, etc. are clear:
  - Introduce domain instances:
    - `b2b_content_ingestion_agent` (wrapper around generic graph with fixed config).
  - Add domain‑specific skills while still reusing generic templates and tools.

This keeps initial implementation simple, maximizes reuse, and leaves a clear path to strong domain specialization later.

We use and reference:

- FastAPI + LangGraph templates:
  - https://github.com/luwhano/fastapi-langgraph-agent-production-ready-template
  - https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template
  - Reddit thread with architectural discussion:
    https://www.reddit.com/r/FastAPI/comments/1s7ynzw/built_a_productionready_fastapi_langgraph/

- Claw Code (clean-room, open-source coding agent framework) for harness, tool system, and query engine ideas:
  - https://claw-code.codes

- Microsoft Agent Framework for orchestration & observability patterns:
  - https://github.com/microsoft/agent-framework

- Agent skills vs tools, domain vs infra:
  - Agent Skills overview: https://agentskills.io/home

- Skills vs tools production guide:
  - https://www.arcade.dev/blog/what-are-agent-skills-and-tools/

- DataCamp “What Are Agent Skills?”:
  - https://www.datacamp.com/blog/agent-skills

- Agentic AI architecture & multi‑agent patterns:
  - Agentic AI architecture overview:
    - https://www.exabeam.com/explainers/agentic-ai/agentic-ai-architecture-types-components-best-practices/

  - Best multi‑agent frameworks roundup:
    - https://getstream.io/blog/multiagent-ai-frameworks/

- Project structure / LangChain / LangGraph:

- LangChain project structure article:
  https://blog.davideai.dev/the-ultimate-langchain-series-projects-structure

- Evaluation:
  DeepEval vs Langfuse comparison:
  https://deepeval.com/blog/deepeval-vs-langfuse
  Langfuse “Evaluating Agents” example (OpenAI cookbook):
  https://developers.openai.com/cookbook/examples/agents_sdk/evaluate_agents/

- Multi‑tenant control plane / AI:
  Azure multi‑tenant control planes:
  https://learn.microsoft.com/azure/architecture/guide/multitenant/approaches/control-planes
  AWS multi‑tenant generative AI environment:
  https://aws.amazon.com/blogs/machine-learning/build-a-multi-tenant-generative-ai-environment-for-your-enterprise-on-aws/

A. End‑to‑end FastAPI + LangGraph templates
These are closest to what you’re building (Python, FastAPI, LangGraph, vector memory, MCP).

luwhano/fastapi-langgraph-agent-production-ready-template
Modern, production‑oriented layout: FastAPI API layer, LangGraph workflows, Postgres checkpointing, Docker, basic observability wiring.

https://github.com/luwhano/fastapi-langgraph-agent-production-ready-template

wassim249/fastapi-langgraph-agent-production-ready-template
Very similar goal: execution layer in FastAPI, agent workflows in LangGraph, clear separation between agents, workflows, infra.

https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template

extrawest/fastapi-langgraph-chatbot-with-vector-store-memory-mcp-tools-and-voice-mode
Shows multi‑agent orchestration, vector‑store memory, and MCP tools in one project (good reference for memory + tool wiring).

https://github.com/extrawest/fastapi-langgraph-chatbot-with-vector-store-memory-mcp-tools-and-voice-mode

NicholasGoh/fastapi-mcp-langgraph-template
Template explicitly combining FastAPI, MCP, LangGraph, Supabase – good for seeing how to structure MCP servers + agent orchestration + DB/memory.

https://github.com/NicholasGoh/fastapi-mcp-langgraph-template

B. Multi‑agent frameworks / enterprise‑style architecture
These give patterns for skills, agents, personas, orchestration, and enterprise concerns.

microsoft/agent-framework
Microsoft’s OSS agent framework: clear split between orchestration, skills/tools, memory, telemetry, and multi‑agent collaboration patterns.

https://github.com/microsoft/agent-framework

SciSharp/BotSharp (C#/.NET, but very architectural)
Mature multi‑agent framework with: agent abstraction layer, multi‑agent cooperation, memory, RAG, plugins, planning strategies – great to mine for enterprise patterns even if you stay in Python.

https://github.com/scisharp/botsharp

GitHub blog – OWL multi‑agent framework (article, but points to repo)
Shows a CAMEL/OWL‑style multi‑agent setup (cooperating agents, tools, browsers, MCP). Good for thinking about agent cooperation and roles.

https://github.blog/open-source/maintainers/from-mcp-to-multi-agents-the-top-10-open-source-ai-projects-on-github-right-now-and-why-they-matter/ (see “OWL” section for repo link)

Claw Code – Open‑Source AI Coding Agent Framework
Clean‑room harness inspired by Claude Code: strong tool system, query engine, background modes, memory, MCP integration. Great reference for harness design, permissions, and cost tracking.

https://claw-code.codes

C. Skills, tools, and harness patterns
These are useful specifically for how they package “skills” and orchestrate tools.

LambdaTest/agent-skills
Large collection of “skills” (for test automation) packaged as reusable patterns. Good reference for how to document skills, structure examples, and separate skills from tools.

https://github.com/LambdaTest/agent-skills

agent-service-toolkit (LangGraph + FastAPI + Streamlit harness)
A “full toolkit for running an AI agent service” – shows how to wrap a LangGraph agent with FastAPI, UI, and service‑style harness concerns (config, logging, etc.).

https://github.com/JoshuaC215/agent-service-toolkit

MCP server examples (FastMCP, blog)
Not a single repo, but the FastMCP examples + tutorials show how to build MCP servers cleanly (good for your tool layer design).

https://dev.to/codecowboydotio/creating-an-mcp-server-with-anthropic-3m87

D. Memory & context‑engineering references
These are not all traditional repos, but they’re excellent for memory & context patterns.

GitHub Copilot “agentic memory system”
Deep dive into how Copilot models cross‑agent memory: entities, events, persona‑aware memory, and multi‑agent sharing. Great conceptual model for your memory service.

https://github.blog/ai-and-ml/github-copilot/building-an-agentic-memory-system-for-github-copilot/

LangChain project‑structure & context article
Very good for how to separate prompts, chains, tools, and context builder modules in a real codebase.

https://blog.davideai.dev/the-ultimate-langchain-series-projects-structure

Anthropic engineering blog – harness / context patterns
Their “harness design for long‑running application development” article (and “effective context engineering” pieces) show how to build harnesses and context management for long‑lived agents.

https://www.anthropic.com/engineering/harness-design-long-running-apps

E. Evaluation & observability
Not directly “agent repos”, but essential for your eval/telemetry stack.

Langfuse
Open‑source LLM observability platform; GitHub + docs show how to structure traces, spans, and evaluation hooks around agents.

https://langfuse.com/docs

DeepEval + litmus
Strong patterns for defining eval suites and integrating them into CI; good reference for how to organize eval code and datasets.

https://google.github.io/litmus/evaluate-deepeval
https://deepeval.com/blog/deepeval-vs-langfuse

NOTE: Should follow SOLID principles.
