# Agentic AI Platform Architecture

This document describes our **agentic, multi-tenant AI coworker architecture**.  
It is the reference for how we design personas, skills, agents, tools, policies, and how they interact.

This is aligned with current industry patterns around multi-agent systems, Agent Skills, MCP, and multi-tenant agent architectures.[web:35][web:73][web:91][web:25]

---

## 1. Overview

We are building an “AI coworkers” platform that can host N coworkers across domains like:

- Digital marketing analyst
- HR executive
- Customer support
- Scrum master
- Project manager

Core properties we care about:

- Multi-tenant
- Modular and decoupled
- Observable and auditable
- Policy- and safety-driven
- Model- and vendor-agnostic

We split the system into:

- **Experience & Ingress**
  - UI / API Gateway / BFF
- **Control Plane**
  - Persona Store
  - Skill / Capability Registry
  - Agent Registry
  - Tool / MCP Registry
  - Policy Engine
  - Orchestrator
  - Planner
  - Execution Engine
  - Guardrails & Evaluator
  - Observability & Cost
- **Data Plane**
  - Domain Agents (microservices / MCP servers)
  - Tools & integrations
  - Memory & data stores:
    - PostgreSQL for relational / transactional data (registries, policies, execution metadata)
    - Vector DB (pgvector-in-Postgres or external) for RAG and long-term memory

---

## 2. Core concepts

### 2.1 Persona – “who”

Persona defines **who** the AI is acting as for a given interaction:

- Example: “Project Manager Coworker – calm, structured, risk-aware.”
- Includes:
  - Role description
  - Tone and style
  - Goals and constraints
  - Default skills to favor
  - Guardrail profile

Stored in a **Persona Store** keyed by `(tenant_id, persona_id)`.

Used by:

- Orchestrator (to decide which planner/skills to consider).
- Planner (to choose skills and structure plans).
- Agents (to adapt tone/format).

Persona does not execute; it is configuration.

---

### 2.2 Skill / Capability – “what”

A **Skill** is a reusable, implementation-agnostic **unit of work**:

- Encodes:
  - What to do (name + description).
  - Input/output schemas (JSON Schema).
  - High-level procedure / workflow (e.g., SKILL.md).
- Does **not** bind to:
  - A specific agent.
  - A specific tool or vendor.

Skills are stored in a **Skill Registry** and are meant to be reused:

- Across multiple agents (different implementations).
- Across multiple personas.
- Across tenants (subject to policy).[web:73][web:80]

Example Skill:

- `summarize_project_status(projectId, period)`  
  Produces:
  - overall status (on_track / at_risk / off_track)
  - summary
  - metrics
  - blockers list

---

### 2.3 Agent – “worker”

An **Agent** is a runtime **worker** (service) that implements one or more skills using tools and models.

- Deployed as a microservice or MCP server.
- Has:
  - System prompt, domain rules.
  - List of supported skills (by `skill_id`).
  - Access to tools (via MCP or HTTP).
  - Observability and scaling configuration.

Each agent registers a manifest in the **Agent Registry**:

- `agent_id`
- `display_name`
- `description`
- `version`
- `endpoint`, `protocol`
- `supported_skills` (list of `skill_id` + metadata)
- `collections` (tenant/env scopes)
- `security_schemes`
- `health_status`, latency/cost estimates

Key relationships:

- One skill → many agents.
- One agent → many skills.
- Agent Registry is the only source of truth for which agents implement which skills.[web:47][web:21]

---

### 2.4 Tool – “how”

A **Tool** is a concrete integration / primitive operation:

- Wrapping:
  - External APIs (Jira, CRM, HRIS, CI/CD, etc.).
  - Databases & data warehouses.
  - Filesystems.
  - Internal services.
- Usually exposed via **MCP** or a similar schema-centric protocol.[web:16][web:23]

Tool entries in the **Tool/MCP Registry** include:

- `tool_id`, `name`, `description`
- `endpoint`, `protocol`
- Operations and their schemas
- Risk level (read-only, write, destructive)
- Tenant scopes and auth info

Agents use a **ToolRegistry / MCP client** to discover and invoke tools at runtime.

---

## 3. Control Plane services and flow

### 3.1 API Gateway & Ingress

Responsibilities:

- Auth (OIDC/JWT/API key).
- Determine:
  - `tenant_id`
  - `user_id`
  - optional `persona_id`
- Apply rate limits and quotas.
- Attach `request_id` / `trace_id`.

Passes requests to the **Orchestrator**.

---

### 3.2 Orchestrator

Responsibilities:

- Generate `execution_id` for each logical job.
- Load persona from Persona Store.
- Load short-term context from Memory service.
- Fetch policies (tenant + persona) from Policy Engine.
- Fetch candidate skills from Skill Registry, then filter them via policy → `allowedSkills`.
- Compose a **planning context**:
  - `tenant_id`, `execution_id`
  - `persona`
  - goal (user request)
  - `allowedSkills`
  - short-term context
  - key policy flags
- Call the **Planner** with this planning context.
- After planning, call **Execution Engine** with:
  - `execution_id`, `tenant_id`
  - persona summary
  - effective policy snapshot (optional)
  - plan graph

---

### 3.3 Planner

Planner is an LLM-based component that converts **goal + persona + allowed skills** into an **executable skill-level plan**, using patterns from LLM planning research.[web:108][web:106]

Input:

- `goal` (user request, possibly structured)
- `persona`
- `allowedSkills` (skills already filtered by policy)
- short-term context (history, memory summaries)

Output:

- A **plan graph**:
  - Steps:
    - `step_id`
    - `skill_id`
    - `input`
    - `depends_on`
    - `parallelizable` flag
    - optional reflection points

Constraints:

- Planner MUST only reference skills from `allowedSkills`.
- Planner MUST NOT invent new skill names or capabilities.
- If the goal cannot be satisfied with allowedSkills:
  - It should either:
    - Return a “capability not available” outcome, or
    - Fall back to a clearly labeled “generic text-only answer” with no side effects.

---

### 3.4 Execution Engine

The Execution Engine is responsible for **running the plan**:

For each runnable step:

1. Validate step.skillId against current policy.
2. Query Agent Registry:
   - “Which agents support this skill for this tenant/collection?”
3. Filter candidates:
   - By policy (allowed/blocked agents).
   - By health, latency, cost.
4. Build an `AgentInvocationContext` containing:
   - `execution_id`, `step_id`
   - `tenant_id`, `user_id`
   - persona summary
   - `skill_id`, `skill_input`
   - short-term context, long-term memory references
   - trace info
5. Call the selected agent’s endpoint (usually an MCP-based or HTTP endpoint).
6. Validate response against the skill’s `output_schema`.
7. Update execution state, persist outputs, emit telemetry.
8. Continue until the plan is complete.

Execution Engine is the **only component** that knows about which agents to call for which skills.

---

## 4. Agent microservice internals

Each domain agent (e.g., `project_status_agent`) uses a common internal pattern:

- **Startup / scaffolding**
  - Load config:
    - Supported skill_ids.
    - Model configuration.
    - Allowed tools.
  - Resolve tools via Tool/MCP Registry and prepare their schemas.
  - Register itself in Agent Registry with its supportedSkills and metadata.
  - Initialize logging and tracing (OpenTelemetry).

- **Request handling**
  - Accept an `AgentInvocationContext`.
  - Reject requests where `skill_id` is not supported.
  - Optionally, fetch the full Skill definition from the Skill Registry.
  - Build LLM context:
    - System prompt: domain role, Do/Don’t rules.
    - Persona summary: style, level of detail.
    - Skill instructions: from skill description / SKILL.md.
    - Tool schemas: only those allowed for this skill.
    - Retrieved docs/memory: via tools or Memory service.
  - Run a controlled reasoning + tool-usage loop.
  - Return a structured JSON result matching the skill’s output schema.

---

## 5. Policy model (tenant and persona)

We use a dedicated **Policy Engine** that exposes TenantPolicy and optional PersonaPolicyOverride objects.

Conceptual TenantPolicy:

```ts
type CapabilityId = string; // usually Skill.id or a capability-group ID
type PlatformId = string;   // e.g. "azure_openai", "aws_bedrock", "local_ollama"
type ToolId = string;
type AgentId = string;

interface TenantPolicy {
  tenantId: string;

  capabilities: {
    allowed: CapabilityId[];
    blocked: CapabilityId[];
    allowedTags?: string[];
    blockedTags?: string[];
    defaultAllow?: boolean;      // usually false
  };

  platforms: {
    allowed: PlatformId[];
    blocked: PlatformId[];
    preferred?: PlatformId[];
  };

  tools: {
    allowedTools?: ToolId[];
    blockedTools?: ToolId[];
  };

  agents?: {
    allowedAgents?: AgentId[];
    blockedAgents?: AgentId[];
  };

  budget?: {
    monthlyUsdLimit?: number;
    perExecutionUsdLimit?: number;
    maxTokensPerExecution?: number;
    softLimitNotifications?: boolean;
  };

  security: {
    allowExternalApiCalls: boolean;
    allowWebScraping: boolean;
    allowSensitiveDataAccess: boolean;
    allowedDataTags?: string[];
    blockedDataTags?: string[];
  };

  governance: {
    requireHumanApprovalForCapabilities: CapabilityId[];
    requireHumanApprovalForTools?: ToolId[];
    approverRoles?: string[];
  };

  evaluation?: {
    autoEvaluateCapabilities?: CapabilityId[];
    minQualityScore?: number;
    logAllExecutionsForCapabilities?: CapabilityId[];
  };
}

PersonaPolicyOverride:

Same shape but partial, keyed by (tenantId, personaId).

EffectivePolicy:

merge(systemDefaults, tenantPolicy, personaPolicyOverride) with precedence:

systemDefaults < tenantPolicy < personaOverride.

6. Where policy is enforced
Policy enforcement happens at multiple stages:

Pre-planning (Orchestrator + Policy Engine)

Skill Registry returns candidate skills by domain/tags.

Policy filters to allowedSkills.

Only allowedSkills are passed into Planner.

Planning (Planner)

Planner prompt explicitly states:

“These are the only allowed skills. Only plan with them.”

Plan must reference only those skill_ids.

Pre-execution (Execution Engine)

Before each step:

Confirm that step.skillId is still allowed by the latest policy.

When selecting agents:

Filter Agent Registry results according to policy and collections.

Tool invocation (Agent or Execution)

Before calling tools/MCP:

Check tool_id and data tags against policy.security and policy.tools.

If a capability/tool is marked as requiring approval:

Insert an approval workflow (human-in-the-loop) before continuing.

Guardrails and Evaluation

Guardrails: check content and safety on inputs and outputs.

Evaluation: run regular offline tests for critical skills, use policy to decide which skills need mandatory eval.

7. How to work with this architecture as a dev
When you add or change behavior:

Define skills first

Add or update Skill entries in the Skill Registry.

Make sure they have clear input/output schemas and descriptions.

Attach skills to agents

In Agent manifests, reference skills by skill_id in supportedSkills.

Avoid hardcoding skill logic in orchestrators or planners.

Update policy

Ensure TenantPolicy and PersonaPolicyOverride can:

Enable / disable these skills.

Require approvals if needed.

Restrict tools/platforms/agents appropriately.

Update planner prompts

Expose new skills to planner through allowedSkills and updated descriptions.

Keep planner constrained to allowedSkills only.

Observe and iterate

Use observability (traces/logs/metrics) to understand how skills and agents are used.

Use evaluation rules to improve skill implementations and routing over time.

Rule of thumb:

Skills: reusable units of work.

Agents: workers that implement skills.

Tools: concrete primitives behind MCP/APIs.

Policy: guardrails and permissions.

Planner/Execution: glue everything together, never bypassing policy or registries.
```

## 8. Storage: Postgres and Vector DB

We use **PostgreSQL** as the primary relational store and a **vector DB** (pgvector or external) for semantic memory.

### 8.1 PostgreSQL (relational)

Postgres stores:

- Tenants, users, and personas.
- Skill Registry:
  - `skills` table with `id`, `name`, `description`, `input_schema`, `output_schema`, `tags`, etc.
- Agent Registry:
  - `agents` table with metadata (id, endpoint, protocol, collections, etc.).
  - `agent_supported_skills` join table: `(agent_id, skill_id, quality_score, cost_tier)`.
- Tool/MCP Registry:
  - `tools` table and related operation metadata.
- Policy:
  - `tenant_policies` and optional `persona_policies` tables.
- Execution metadata:
  - `executions` table keyed by `execution_id` and `tenant_id`.
  - Optional `execution_steps` and `audit_events` tables.

Multi-tenancy strategy:

- Default: shared schema + `tenant_id` column; all queries must filter on `tenant_id`.
- Optionally use Row-Level Security for extra protection.
- If needed in the future, we can move heavy tenants to schema-per-tenant or DB-per-tenant patterns.

### 8.2 Vector database (memory)

Vector memory stores:

- Embeddings for documents and artifacts:
  - Marketing docs, HR policies, support KBs, project docs, code, etc.
- Summaries of executions (e.g., compressed conversation history, task summaries).
- Domain-specific items (tickets, campaigns, incidents) for semantic retrieval.

Implementation options:

- **pgvector inside Postgres**:
  - Single logical database for relational + vector data.
  - Simpler ops; good for most use cases.
- **External vector DB**:
  - For very large-scale or specialized retrieval needs.

Access pattern:

- Agents and orchestrator call a **Memory service** rather than the vector DB directly.
- Memory service:
  - Applies tenant and persona policy.
  - Controls which collections/indexes can be queried.
  - Encapsulates retrieval patterns (filters, rerankers, hybrid search, etc.).

### 8.3 Short-term vs long-term memory

- **Short-term memory** (per execution):
  - Likely stored in Postgres or a cache keyed by `execution_id`.
  - Contains compressed conversation, recent steps, and working data.

- **Long-term memory**:
  - Implemented via vector DB + document storage.
  - Used by Planner and Agents when they need to look beyond the current execution (e.g., historical campaign performance, recurring issues).

Design rule:

- Transactional and configuration data → **Postgres**.
- Semantic search / RAG / long-lived context → **Memory service + vector DB**.
- Never let ad-hoc persistence leak into agent code; always go through well-defined domain repositories and memory APIs.

## Databases: Control Plane vs Tenant Databases

We use a **control plane database** and **per-tenant databases**.

- Control plane DB:
  - Stores tenant catalog and DB connection info.
  - Stores global registries (skills, agents, tools).
  - Optionally stores global policy templates and aggregated usage/cost.

- Tenant DBs:
  - One Postgres database per tenant.
  - Store tenant-specific domain data:
    - Content ingestion tables (sources, articles, tags, formatted_articles, logs).
    - HR data, litigation data, marketing data, etc., as we add domains.
  - Share the same logical schema so migrations can be applied uniformly.

The orchestrator always:

1. Resolves `tenant_id` using the control plane DB.
2. Opens a connection to that tenant’s DB for domain operations.
3. Writes platform-level metrics/usage back to the control plane if needed.

This split gives us strong tenant data isolation while preserving a single global “brain” for orchestration and governance.
