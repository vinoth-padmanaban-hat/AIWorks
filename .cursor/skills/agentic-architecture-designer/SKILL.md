# Agentic Architecture Designer

## Description

This skill turns the coding agent into an **agentic system architect** for our multi-tenant, multi-agent AI coworker platform implemented in:

- Python 3.11+
- FastAPI
- LangChain + LangGraph
- OpenTelemetry (OTel)
- Mermaid for diagrams
  It must also account for storage and memory:
- PostgreSQL as the primary relational DB for tenants, registries, policies, and execution metadata.
- A vector DB (pgvector or external) behind a Memory service for RAG and long-term semantic memory.

When this skill is active, always ground architectural proposals, service layouts, and refactors in our documented architecture:

- `docs/AGENTIC_ARCHITECTURE.md`
- `.cursor/rules/agentic-architecture.mdc`

Use the concepts of:

- Persona
- Skill / Capability
- Agent (LangGraph/LangChain workers)
- Tool (usually MCP-based or LangChain tools)
- Policy (tenant/persona rules)
- Orchestrator / Planner / Execution Engine
- Registries (Persona, Skill, Agent, Tool)
- OTel tracing and metrics
- Mermaid diagrams for architecture

Do NOT collapse these concepts into a single giant “agent” or a single `main.py`.

## When to use

Activate this skill when:

- Designing new FastAPI endpoints for agent workflows.
- Defining new coworkers (personas) and their skills/agents.
- Refactoring LangGraph graphs or adding new nodes/subgraphs.
- Modifying registry schemas (Skill, AgentManifest, ToolManifest).
- Implementing or updating tenant/policy/guardrails logic.
- Generating or updating architecture diagrams in Mermaid.

## Guidance

When this skill is active:

1. **Always reference `docs/AGENTIC_ARCHITECTURE.md`** before making architectural decisions.
2. Keep **Control Plane** and **Data Plane** separate:
   - Control Plane: orchestrator, planner, execution engine, registries, policies, observability.
   - Data Plane: domain LangGraph agents, LangChain tools, memory, external APIs.
3. Treat **skills as reusable units of work**:
   - Express skills as Pydantic models (inputs/outputs) and metadata.
   - Keep skills defined in Skill Registry, not inside specific agents.
4. Treat **Agent Registry as the source of truth** for which workers implement which skills.
   - Use `supportedSkills.skillId` ↔ `Skill.id` as the relationship.
5. Enforce **policy before planning and execution**:
   - Only give the Planner skills that are allowed by policy.
   - Only allow Execution Engine to select agents and tools permitted by policy.
6. For LangGraph:
   - Prefer separate modules for:
     - State definitions
     - Node functions
     - Graph composition
   - Use checkpointers/state backends for long-running flows.
7. For OTel:
   - Ensure FastAPI, LangChain, and LangGraph are all instrumented.
   - Tag spans with `tenant_id`, `execution_id`, `step_id`, `skill_id`, `agent_id` when possible.
8. For Mermaid:
   - When asked to document architecture, output Mermaid code blocks describing:
     - High-level system (control plane vs data plane).
     - Key graphs and their node interactions.
     - Sequence diagrams for request flows.
9. For storage:
   - Use PostgreSQL for registries, policy, and transactional data.
   - Use a dedicated Memory layer (vector DB + doc store) for RAG and long-term memory, never direct vector DB access from agents.

## Examples of tasks

- “Design the Python packages and FastAPI routers for Skill Registry, Agent Registry, and Policy Engine.”
- “Add a new coworker persona for ‘Delivery Manager’ and propose its skills, LangGraph agents, and policy entries.”
- “Refactor the Execution Engine so it resolves agents via Agent Registry based on `skill_id` and tenant policy, and calls LangGraph graphs correctly.”
- “Generate a Mermaid sequence diagram for: client → FastAPI → orchestrator → planner → execution engine → LangGraph agents → tools.”

## Guidance (additional multi-tenant DB rules)

When this skill is active, also enforce the following for databases:

1. Separate control plane DB and tenant DBs:
   - Control plane: tenants, DB connections, registries, global policy/templates.
   - Tenant DBs: domain data (content, HR, litigation, etc.).

2. For database-per-tenant:
   - Do not add tenant_id columns to tenant-owned tables unless there is a very strong reason.
   - Use the DB connection itself as the tenant boundary.

3. For article data:
   - Keep normalized fields (title, text, img_url, summary, published_at) in `articles`.
   - Put tenant-specific fields into `formatted_articles.formatted_json` by applying the tenant’s template.
