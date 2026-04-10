1. # High-level Architecture

We are building an "agentic infrastructure" that provides N AI coworkers across multiple domains (e.g., digital marketing analyst, HR exec, customer support, scrum master, project manager).

We split the system into:

1. Experience & Ingress Layer
   - API Gateway / BFF / UI.
   - Handles auth, tenant resolution, rate limiting.

2. Control Plane (stateless or mostly stateless)
   - Persona Store
   - Skill / Capability Registry
   - Agent Registry
   - Tool / MCP Registry (control-plane-as-a-tool)
   - Orchestrator
   - Planner
   - Execution Engine
   - Guardrails & Evaluator
   - Observability & Cost

3. Data Plane (stateful / domain-specific)
   - Domain Agents (microservices / MCP servers)
   - Tools (MCP servers, APIs, DBs, queues)
   - Memory (vector DB + document store + relational DBs)

Key principle:

- Control Plane = discovery, planning, routing, policy, observability.
- Data Plane = doing actual domain work via agents + tools.
- All runtime wiring happens via registries and metadata, not hardcoded lists.

# ======================================== 2. Core Concepts and Relationships

We distinguish 4 main abstractions:

- Persona = WHO the AI is acting as.
- Skill/Capability = WHAT can be done (abstract, reusable know-how).
- Agent = WHO executes skills at runtime (a worker/microservice).
- Tool = HOW concrete actions happen (APIs, DBs, MCP servers).

These have registry-like stores:

- Persona Store → catalog of personas.
- Skill Registry → catalog of abstract skills, independent of agents.
- Agent Registry → catalog of agents that implement one or more skills.
- Tool/MCP Registry → catalog of tools, mostly MCP servers.

Keep these concerns separated at all times.

=== 2.1 Persona ===

Persona defines identity and high-level behavior:

- Fields (conceptually):
  - persona_id
  - tenant_id
  - display_name
  - role_description (who this coworker is)
  - tone_style (analytical, friendly, etc.)
  - goals (what it tries to optimize)
  - constraints (safety, compliance, domain limits)
  - default_skills (suggested skills to prefer)
  - guardrail_profile (which guardrails to apply)

Usage:

- Orchestrator loads persona at the start of each execution.
- Planner uses persona to bias which skills to pick and how to structure plans.
- Agents may use persona summary to tune tone/formatting, but persona is not tied to any single agent implementation.

Persona is stored in a Persona Store service (backed by DB). It is read-only at runtime.

=== 2.2 Skill / Capability ===

Skill is a reusable, implementation-agnostic "verb" + know-how package:

- Fields (conceptually):
  - skill_id (globally unique)
  - name, description
  - input_schema (JSON Schema)
  - output_schema (JSON Schema)
  - examples (optional few-shot / test cases)
  - tags (domain, read-only vs write, risk category)
  - safety_level
  - documentation_url

Important:

- Skills define WHAT to do and HOW in terms of steps / workflow / IO schemas.
- Skills do NOT specify WHICH agent or WHICH tools must be used.
- Skills are reusable across agents, personas, and tenants.

Skill Registry:

- Read-only service at runtime:
  - GET /skills, GET /skills/{skill_id}
- Planner uses this registry to plan in terms of skills, not concrete agents.

=== 2.3 Agent ===

Agent is a runtime worker (microservice / MCP-based service) that implements one or more skills using tools and models.

An Agent Registry entry includes:

- agent_id
- display_name
- description
- version
- endpoint (MCP / HTTP / gRPC base URL)
- protocol (e.g., "mcp")
- supported_skills: list of (skill_id, quality_score?, cost_profile?)
- collections / scopes: which tenants or environments can see this agent
- security_schemes: how to authenticate
- owner_ids / managed_by: for governance & approvals
- health_status: OK / degraded / offline
- latency_estimate, cost_estimate

Key relationships:

- ONE skill can be implemented by MANY agents (cheap vs premium, tenant-specific, vendor-specific).
- ONE agent can implement MANY skills (cohesive domain).
- We do NOT enforce 1 skill = 1 agent.

Agents themselves:

- Are deployed as services (e.g., FastAPI + MCP server).
- Have their own observability and scaling.
- Use a Tool/MCP client to talk to tools.

=== 2.4 Tool ===

Tool is a concrete integration or primitive operation:

- Usually exposed via MCP servers or HTTP/gRPC APIs.
- Represented in a Tool/MCP Registry with:
  - tool_id, name, description
  - endpoint, protocol ("mcp", "http_json", etc.)
  - operations (names + JSON Schemas)
  - risk_level (read-only / write / destructive)
  - auth model (service account, user OAuth, etc.)
  - tenant_scopes
  - logging and rate-limit policies

Agents discover and use tools at runtime through a ToolRegistry / MCP client.

# ======================================== 3. Control Plane: Services and Context Flow

Main control-plane services:

- Orchestrator
- Planner
- Execution Engine
- Persona Store
- Skill Registry
- Agent Registry
- Tool/MCP Registry
- Policy Engine
- Guardrails & Evaluator

=== 3.1 API Gateway → Orchestrator (Ingress) ===

Gateway responsibilities:

- Authenticate (OIDC/JWT/API key).
- Resolve tenant_id, user_id, optional persona_id.
- Apply rate limiting and quotas.
- Attach request_id / trace_id.

Orchestrator responsibilities:

- Generate execution_id for each logical job.
- Load persona from Persona Store.
- Load short-term context from Memory (conversation, previous executions).
- Load tenant/persona policy from Policy Engine.
- Query Skill Registry for candidate skills and filter them with policy.
- Create a planning context and call Planner.

Context passed to Planner:

- tenant_id
- execution_id
- persona
- goal (user input, possibly structured)
- allowedSkills (already policy-filtered)
- short_term_context (summaries, recent actions)
- high-level policy flags (read-only vs write, etc.)

=== 3.2 Planner ===

Input:

- planning_context as above.

Responsibilities:

- Interpret user goal + persona.
- Pick which skills to use (from allowedSkills).
- Build an executable plan graph:
  - Steps with step_id, skill_id, inputs, dependencies, parallelization hints, optional reflection nodes.
- Apply patterns from LLM planning literature:
  - Single-path vs multi-path plans.
  - One-shot vs incremental planning.
  - Optional self/cross/human reflection.

Output:

- PLAN that references ONLY skill_ids from allowedSkills.
- No agents or tools are referenced in the plan.

=== 3.3 Orchestrator → Execution Engine ===

Orchestrator sends to Execution Engine:

- execution_id
- tenant_id
- persona summary (or pointer)
- effective policy snapshot (optional)
- plan (graph of skill-based steps)

=== 3.4 Execution Engine ===

For each runnable step:

1. Take step.skillId.
2. Re-check policy to ensure the skill is still allowed.
3. Query Agent Registry:
   - GET /agents?skill_id=...&tenant_id=...
4. Filter agents by:
   - Collections / scopes
   - Effective policy (allowedAgents/blockedAgents)
   - health_status
5. Pick the best agent (quality, cost, latency, etc.).
6. Build AgentInvocationContext:

   {
   execution_id,
   step_id,
   tenant_id,
   user_id,
   persona_summary,
   skill_id,
   skill_input,
   short_term_context,
   long_term_context_refs,
   trace_id
   }

7. Invoke the agent’s endpoint (often MCP fronted by HTTP/gRPC).
8. Validate the response against the skill.output_schema.
9. Persist step output, update execution graph, emit telemetry.
10. When plan is complete, assemble final output and return to Orchestrator or client.

# ======================================== 4. Agent Microservice Design

Each domain agent is a microservice (or MCP server) with internal structure like a modern coding agent:

- Startup / scaffolding:
  - Load config: supported skill_ids, model, allowed tools.
  - Register itself into Agent Registry with supportedSkills metadata.
  - Resolve and register tools via MCP / Tool Registry.
  - Prepare system prompts and skill-specific prompts.
  - Set up logging and metrics (OpenTelemetry).

- Runtime per request (via /invoke or MCP call):
  - Accept AgentInvocationContext.
  - Reject if ctx.skill_id not in supported skill list.
  - Fetch skill definition from Skill Registry as needed (instructions, IO schemas).
  - Build LLM context:
    - System instructions: domain role, safety rules.
    - Persona summary: shape tone / format.
    - Skill instructions: from skill metadata / SKILL.md.
    - Tool schemas: only allowed tools for this agent & skill.
    - Relevant memory/docs via MCP tools or Memory service.
  - Run a controlled loop (e.g., ReAct):
    - Think → choose tool → call via MCP → observe → iterate.
    - Respect policy and guardrails at every tool call.
  - Return structured JSON that matches skill.output_schema.

Agents do NOT:

- Decide which other agents to call (that’s Orchestrator / Execution Engine).
- Hardcode tenant-specific routing or policy decisions.
- Bypass Tool/MCP Registry for calling external systems.

# ======================================== 5. Registries and Multi-Tenancy

=== Persona Store ===

- Keyed by (tenant_id, persona_id).
- Orchestrator reads once per execution.

=== Skill Registry ===

- Global catalog, with optional tenant-specific overlays.
- Planner reads skills (definitions and IO schemas), filtered by tenant abilities and policy.

=== Agent Registry ===

- Stores AgentManifest with supportedSkills, endpoint, collections, health, etc.
- Inspired by Entra Agent metadata and multi-agent reference architectures.
- Execution Engine uses it to find agents that implement a given skill in a given tenant/collection.

=== Tool / MCP Registry ===

- Catalog of MCP servers and tools.
- Implements “control-plane-as-a-tool”:
  - Agents see one or a few high-level control plane tools.
  - Control plane resolves down to specific tools/MCP servers with governance and logging.

Multi-tenancy:

- tenant_id is threaded from Gateway → Orchestrator → Planner → Execution Engine → Agents → Tools.
- Collections/scope fields are used for tenant/env scoping in Agent/Tool registries.
- All resources (memory, data, tools) must be segregated by tenant and policy.

# ======================================== 7. Planning, Skills, and Policy (LLM + Planner + Policy)

High-level principles:

- Planner MUST plan only using skills that are:
  - Present in the Skill Registry, AND
  - Allowed by tenant/persona policy at that time.
- Skill = reusable unit of work: a portable capability definition (WHAT + HOW in abstract) with IO schemas.
- Tenant/Persona Policy = explicit rules that decide:
  - Which skills/capabilities are allowed/blocked.
  - Which tools/platforms/agents can be used.
  - Where human approval or extra evaluation is required.
- LLM (reasoning) + Planner (skill-level planning) + Policy (constraints) =>
  Safe, predictable, enterprise-ready behavior.

=== 7.1 Skills are reusable units of work ===

- Skills define WHAT to do and HOW in abstract terms (workflow, IO schemas).
- Skills do NOT bind to any specific agent or tool.
- Skills are reused across:
  - Multiple agents (different implementations).
  - Multiple personas.
  - Multiple tenants (subject to policy).

Agents simply declare:

- supportedSkills: [ { skillId, qualityScore?, costTier? }, ... ]

Execution Engine and Policy together decide **which agent** is used for a skill at runtime.

=== 7.2 Planner’s action space = allowed skills ===

The Planner’s allowed action set is exactly the `allowedSkills` list provided by Orchestrator after policy filtering.

Flow:

1. Orchestrator:
   - Loads tenant + persona policy.
   - Fetches candidate skills from Skill Registry (by tags/domain).
   - Filters them using policy → allowedSkills.

2. Planner:
   - Receives goal, persona, allowedSkills, context.
   - Is explicitly instructed:
     - "Here is the list of allowed skills. You MUST only use these skills in your plan. Do NOT invent new capabilities or tools."

3. Planner:
   - Produces a plan only referencing skill_ids from allowedSkills.
   - If it cannot satisfy the goal:
     - Returns a “capability not available” outcome, or
     - Falls back to a clearly-labeled text-only answer (no tools, no side effects).

=== 7.3 Tenant & Persona Policy model ===

Conceptual TenantPolicy schema:

- tenantId: string

- capabilities:
  allowed: [ capabilityId ] // typically Skill.id or capability-group IDs
  blocked: [ capabilityId ]
  allowedTags?: [ string ]
  blockedTags?: [ string ]
  defaultAllow?: boolean // usually false in enterprise

- platforms:
  allowed: [ platformId ] // e.g., "azure_openai", "aws_bedrock", "local_ollama"
  blocked: [ platformId ]
  preferred?: [ platformId ]

- tools:
  allowedTools?: [ toolId ]
  blockedTools?: [ toolId ]

- agents:
  allowedAgents?: [ agentId ]
  blockedAgents?: [ agentId ]

- budget:
  monthlyUsdLimit?: number
  perExecutionUsdLimit?: number
  maxTokensPerExecution?: number
  softLimitNotifications?: boolean

- security:
  allowExternalApiCalls: boolean
  allowWebScraping: boolean
  allowSensitiveDataAccess: boolean
  allowedDataTags?: [ string ]
  blockedDataTags?: [ string ]

- governance:
  requireHumanApprovalForCapabilities: [ capabilityId ]
  requireHumanApprovalForTools?: [ toolId ]
  approverRoles?: [ string ]

- evaluation:
  autoEvaluateCapabilities?: [ capabilityId ]
  minQualityScore?: number
  logAllExecutionsForCapabilities?: [ capabilityId ]

PersonaPolicyOverride:

- Same shape but partial:
  - can override allowed/blocked capabilities, tools, agents, platforms.

EffectivePolicy:

- Merge(systemDefaults, tenantPolicy, personaPolicyOverride) with precedence:
  - systemDefaults < tenantPolicy < personaOverride

=== 7.4 Where Policy is enforced ===

Policy is enforced at:

1. Pre-planning:
   - Filter Skill Registry results using EffectivePolicy before calling Planner.
   - Planner only sees allowedSkills.

2. Planning:
   - Planner prompt explicitly restricts to allowedSkills.
   - Plan must reference ONLY these skill_ids.

3. Pre-execution:
   - Execution Engine re-checks each step.skillId against EffectivePolicy.
   - When selecting agents:
     - Filter Agent Registry results with EffectivePolicy.agents and collections.

4. Tool invocation:
   - Agents or Execution Engine consult EffectivePolicy.tools & security before calling a tool/MCP server.
   - If capability or tool requires human approval:
     - Insert approval workflow before executing.

5. Guardrails & Evaluation:
   - Guardrails for content/safety (PII, toxicity, etc.).
   - Evaluation rules for periodic testing and quality thresholds.

# ======================================== 8. Implementation Guidance for Devs

When generating code, designs, or docs:

- Keep abstractions clean:
  - Persona: identity & intent.
  - Skills: abstract, reusable units of work with schemas.
  - Agents: workers implementing skills using tools.
  - Tools: concrete side effects (MCP, APIs).
  - Orchestrator/Planner/Execution Engine: composition & routing, not domain logic.

- When defining new behavior:
  1. Define or update Skill(s) in the Skill Registry.
  2. Attach Skill(s) to one or more Agent manifests via supportedSkills.
  3. Make sure TenantPolicy and PersonaPolicy can enable/disable these skills.
  4. Only then update Planner prompts to consider the new Skill(s) when appropriate.

- Never:
  - Embed policy logic inside agent prompts.
  - Bypass registries (skills, agents, tools) with hardcoded lists.
  - Let LLMs invent new tool or skill names not present in registries.

- Always:
  - Enforce policy before planning and execution.
  - Log decisions with tenant_id, execution_id, skill_id, agent_id, tool_id.
  - Keep the system modular so skills and agents can evolve independently.

Top level layout
├─ app/ # FastAPI app + control plane
│ ├─ main.py # FastAPI entrypoint
│ ├─ api/ # Routers (HTTP APIs)
│ ├─ core/ # config, logging, OTel init
│ ├─ domain/
│ │ ├─ personas/
│ │ ├─ skills/ # Skill definitions & registry
│ │ ├─ agents_registry/ # Agent manifests & registry service
│ │ ├─ tools_registry/ # MCP/tool registry service
│ │ ├─ policy/ # Tenant/persona policy engine
│ │ └─ memory/ # Memory & RAG abstraction
│ ├─ orchestration/
│ │ ├─ orchestrator.py
│ │ ├─ planner.py
│ │ └─ execution_engine.py
│ └─ telemetry/ # OTel integration
│
├─ agents/ # Domain agents & LangGraph graphs
│ ├─ pm/
│ │ ├─ state.py
│ │ ├─ nodes.py
│ │ ├─ graph.py
│ │ └─ config.py # references supported skills, etc.
│ ├─ support/
│ ├─ marketing/
│ ├─ hr/
│ └─ scrum/
│
├─ docs/
│ ├─ AGENTIC_ARCHITECTURE.md
│ └─ diagrams/
│ ├─ system.mmd
│ └─ flows/...
│
├─ .cursor/
│ ├─ rules/
│ ├─ skills/
│ └─ environment.json
└─ pyproject.toml / uv.lock # Python project config
