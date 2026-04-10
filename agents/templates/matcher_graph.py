"""
Generic Content Matcher Agent — LangGraph StateGraph.

Nodes
-----
  embed_content      — embed the input content using OpenAI embeddings
  vector_search      — query tenant vector store for similar entities
  db_lookup          — structured keyword search against tenant DB table
  rerank_candidates  — LLM scores and selects best matches from candidates
  emit_matches       — build final MatcherAgentOutput

This agent is domain-agnostic.  Pass entity_type="product" for product matching,
entity_type="kb_article" for HR KB matching, etc.

Call chain:
  domain agent node → Execution Engine → generic_content_matcher_agent (port 8005)
                                       → tenant DB + vector store
"""

from __future__ import annotations

import json
import time
from typing import Any, TypedDict

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import END, StateGraph
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.core.langfuse_setup import graph_runnable_config, langfuse_trace
from app.core.logging import (
    get_logger,
    log_llm_call,
    log_node_entry,
    log_node_error,
    log_node_exit,
    log_tool_call,
)
from agents.templates.matcher_models import (
    MatcherAgentInput,
    MatcherAgentOutput,
    MatchResult,
)

logger = get_logger("generic_content_matcher_agent")


# ── Graph state ───────────────────────────────────────────────────────────────

class MatcherState(TypedDict):
    input: MatcherAgentInput
    embedding: list[float]          # content embedding vector
    vector_candidates: list[dict[str, Any]]
    db_candidates: list[dict[str, Any]]
    all_candidates: list[dict[str, Any]]
    matches: list[MatchResult]
    errors: list[str]
    execution_id: str
    tenant_id: str
    t0: float


# ── DB engine cache (per tenant_db_url) ───────────────────────────────────────
_engine_cache: dict[str, AsyncEngine] = {}


def _get_engine(db_url: str) -> AsyncEngine:
    if db_url not in _engine_cache:
        _engine_cache[db_url] = create_async_engine(db_url, pool_size=2, max_overflow=2)
    return _engine_cache[db_url]


async def _get_tenant_db_url(tenant_id: str) -> str | None:
    """Look up the tenant's DB URL from the control plane."""
    try:
        from app.core.db import get_control_db_engine
        engine = get_control_db_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT db_url FROM tenant_db_connections "
                    "WHERE tenant_id = :tid LIMIT 1"
                ),
                {"tid": tenant_id},
            )
            row = result.fetchone()
            return row.db_url if row else None
    except Exception as exc:
        logger.warning("[matcher] could not resolve tenant DB for %s: %s", tenant_id, exc)
        return None


# ── Node 1: embed_content ─────────────────────────────────────────────────────

async def embed_content(state: MatcherState) -> MatcherState:
    inp = state["input"]
    t0 = log_node_entry(
        logger, node="embed_content",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    embedding: list[float] = []
    errors = list(state.get("errors", []))

    if inp.use_vector_search and inp.content:
        try:
            embedder = OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=settings.openai_api_key,
            )
            text_to_embed = inp.content[:8000]
            embedding = await embedder.aembed_query(text_to_embed)
            logger.debug(
                "[embed_content] embedded %d chars → %d dims",
                len(text_to_embed), len(embedding),
            )
        except Exception as exc:
            errors.append(f"embed_content error: {exc}")
            log_node_error(logger, node="embed_content", error=exc, t0=t0,
                           execution_id=state["execution_id"], tenant_id=state["tenant_id"])

    log_node_exit(
        logger, node="embed_content", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"dims={len(embedding)}",
    )
    return {**state, "embedding": embedding, "errors": errors}


# ── Node 2: vector_search ─────────────────────────────────────────────────────

async def vector_search(state: MatcherState) -> MatcherState:
    inp = state["input"]
    t0 = log_node_entry(
        logger, node="vector_search",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    candidates: list[dict[str, Any]] = []
    errors = list(state.get("errors", []))

    if not inp.use_vector_search or not state["embedding"]:
        log_node_exit(logger, node="vector_search", t0=t0,
                      execution_id=state["execution_id"], tenant_id=state["tenant_id"],
                      summary="skipped (no embedding or disabled)")
        return {**state, "vector_candidates": candidates, "errors": errors}

    try:
        db_url = await _get_tenant_db_url(state["tenant_id"])
        if not db_url:
            raise RuntimeError(f"No DB URL for tenant {state['tenant_id']}")

        engine = _get_engine(db_url)
        embedding_str = "[" + ",".join(str(v) for v in state["embedding"]) + "]"
        fields = ", ".join(inp.match_fields[:10])

        # pgvector cosine similarity — requires the embedding column to exist.
        # Falls back gracefully if the table has no embedding column.
        query = text(f"""
            SELECT id::text AS entity_id,
                   {fields},
                   1 - (embedding <=> :embedding::vector) AS score
            FROM {inp.entity_table}
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> :embedding::vector
            LIMIT :top_k
        """)

        tool_t0 = time.monotonic()
        async with engine.connect() as conn:
            result = await conn.execute(
                query, {"embedding": embedding_str, "top_k": inp.top_k * 2}
            )
            rows = result.fetchall()

        log_tool_call(
            logger, tool="vector_search",
            args={"table": inp.entity_table, "top_k": inp.top_k},
            result={"candidates": len(rows)},
            elapsed_ms=int((time.monotonic() - tool_t0) * 1000),
            execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        )

        for row in rows:
            row_dict = dict(row._mapping)
            candidates.append({
                "entity_id": row_dict.get("entity_id", ""),
                "entity_type": inp.entity_type,
                "name": row_dict.get("name", row_dict.get(inp.match_fields[0], "")),
                "description": row_dict.get("description", row_dict.get(inp.match_fields[1] if len(inp.match_fields) > 1 else "description", "")),
                "score": float(row_dict.get("score", 0.0)),
                "source": "vector",
                "extra": {k: v for k, v in row_dict.items()
                          if k not in ("entity_id", "score", "embedding")},
            })

    except Exception as exc:
        errors.append(f"vector_search error: {exc}")
        logger.warning("[vector_search] error: %s", exc)

    log_node_exit(
        logger, node="vector_search", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"found {len(candidates)} candidates",
    )
    return {**state, "vector_candidates": candidates, "errors": errors}


# ── Node 3: db_lookup ─────────────────────────────────────────────────────────

async def db_lookup(state: MatcherState) -> MatcherState:
    inp = state["input"]
    t0 = log_node_entry(
        logger, node="db_lookup",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    candidates: list[dict[str, Any]] = []
    errors = list(state.get("errors", []))

    if not inp.use_db_search or not inp.content:
        log_node_exit(logger, node="db_lookup", t0=t0,
                      execution_id=state["execution_id"], tenant_id=state["tenant_id"],
                      summary="skipped")
        return {**state, "db_candidates": candidates, "errors": errors}

    try:
        db_url = await _get_tenant_db_url(state["tenant_id"])
        if not db_url:
            raise RuntimeError(f"No DB URL for tenant {state['tenant_id']}")

        engine = _get_engine(db_url)
        # Extract keywords from content (first 500 chars, split on whitespace)
        keywords = list({w.lower() for w in inp.content[:500].split() if len(w) > 3})[:10]
        search_term = " | ".join(keywords)

        fields = ", ".join(inp.match_fields[:10])
        # Full-text search using PostgreSQL tsvector
        query = text(f"""
            SELECT id::text AS entity_id, {fields}
            FROM {inp.entity_table}
            WHERE to_tsvector('english', {inp.match_fields[0]}) @@
                  to_tsquery('english', :search_term)
            LIMIT :top_k
        """)

        tool_t0 = time.monotonic()
        async with engine.connect() as conn:
            result = await conn.execute(
                query, {"search_term": search_term, "top_k": inp.top_k * 2}
            )
            rows = result.fetchall()

        log_tool_call(
            logger, tool="db_lookup",
            args={"table": inp.entity_table, "keywords": len(keywords)},
            result={"candidates": len(rows)},
            elapsed_ms=int((time.monotonic() - tool_t0) * 1000),
            execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        )

        for row in rows:
            row_dict = dict(row._mapping)
            candidates.append({
                "entity_id": row_dict.get("entity_id", ""),
                "entity_type": inp.entity_type,
                "name": row_dict.get("name", row_dict.get(inp.match_fields[0], "")),
                "description": row_dict.get("description", ""),
                "score": 0.5,
                "source": "db",
                "extra": {k: v for k, v in row_dict.items() if k != "entity_id"},
            })

    except Exception as exc:
        errors.append(f"db_lookup error: {exc}")
        logger.warning("[db_lookup] error: %s", exc)

    log_node_exit(
        logger, node="db_lookup", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"found {len(candidates)} candidates",
    )
    return {**state, "db_candidates": candidates, "errors": errors}


# ── Node 4: rerank_candidates ─────────────────────────────────────────────────

async def rerank_candidates(state: MatcherState) -> MatcherState:
    inp = state["input"]
    t0 = log_node_entry(
        logger, node="rerank_candidates",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )

    errors = list(state.get("errors", []))

    # Merge and deduplicate candidates from both sources
    seen_ids: set[str] = set()
    all_candidates: list[dict[str, Any]] = []
    for c in state["vector_candidates"] + state["db_candidates"]:
        if c["entity_id"] not in seen_ids:
            seen_ids.add(c["entity_id"])
            all_candidates.append(c)

    matches: list[MatchResult] = []

    if not all_candidates:
        log_node_exit(logger, node="rerank_candidates", t0=t0,
                      execution_id=state["execution_id"], tenant_id=state["tenant_id"],
                      summary="no candidates to rerank")
        return {**state, "all_candidates": all_candidates, "matches": matches, "errors": errors}

    if not inp.use_llm_rerank:
        # Sort by score, take top_k
        all_candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in all_candidates[: inp.top_k]:
            if c["score"] >= inp.min_score:
                matches.append(MatchResult(
                    entity_id=c["entity_id"],
                    entity_type=c["entity_type"],
                    name=c["name"],
                    description=c["description"],
                    score=c["score"],
                    match_reason=f"score={c['score']:.2f} source={c['source']}",
                    extra=c.get("extra", {}),
                ))
        log_node_exit(logger, node="rerank_candidates", t0=t0,
                      execution_id=state["execution_id"], tenant_id=state["tenant_id"],
                      summary=f"no-llm rerank: {len(matches)} matches")
        return {**state, "all_candidates": all_candidates, "matches": matches, "errors": errors}

    # LLM re-ranking
    try:
        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0,
        )

        candidates_text = "\n".join(
            f"{i+1}. [{c['entity_id']}] {c['name']}: {c['description'][:200]}"
            for i, c in enumerate(all_candidates[: inp.top_k * 3])
        )

        prompt = (
            f"You are matching content to {inp.entity_type} entities.\n\n"
            f"Content to match:\n{inp.content[:2000]}\n\n"
            f"Candidates:\n{candidates_text}\n\n"
            f"Return a JSON array of the top {inp.top_k} most relevant matches. "
            f"Each item: {{\"entity_id\": \"...\", \"score\": 0.0-1.0, \"reason\": \"...\"}}\n"
            f"Only include matches with score >= {inp.min_score}. "
            f"Return only the JSON array."
        )

        llm_t0 = time.monotonic()
        response = await llm.ainvoke(prompt, config=graph_runnable_config())
        llm_elapsed = int((time.monotonic() - llm_t0) * 1000)

        log_llm_call(
            logger, model=settings.openai_model,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(response.content) // 4,
            latency_ms=llm_elapsed,
            execution_id=state["execution_id"], tenant_id=state["tenant_id"],
            purpose="rerank_candidates",
        )

        # Parse LLM response
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        ranked = json.loads(raw)

        # Build a lookup from candidates
        cand_by_id = {c["entity_id"]: c for c in all_candidates}
        for item in ranked:
            eid = str(item.get("entity_id", ""))
            score = float(item.get("score", 0.0))
            if score < inp.min_score:
                continue
            cand = cand_by_id.get(eid, {})
            matches.append(MatchResult(
                entity_id=eid,
                entity_type=inp.entity_type,
                name=cand.get("name", eid),
                description=cand.get("description", ""),
                score=score,
                match_reason=item.get("reason", ""),
                extra=cand.get("extra", {}),
            ))

    except Exception as exc:
        errors.append(f"rerank_candidates LLM error: {exc}")
        logger.warning("[rerank_candidates] LLM error: %s", exc)
        # Fallback: return top-k by score
        all_candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in all_candidates[: inp.top_k]:
            matches.append(MatchResult(
                entity_id=c["entity_id"],
                entity_type=c["entity_type"],
                name=c["name"],
                description=c["description"],
                score=c["score"],
                match_reason="fallback (LLM rerank failed)",
                extra=c.get("extra", {}),
            ))

    log_node_exit(
        logger, node="rerank_candidates", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"candidates={len(all_candidates)} matches={len(matches)}",
    )
    return {**state, "all_candidates": all_candidates, "matches": matches, "errors": errors}


# ── Node 5: emit_matches ──────────────────────────────────────────────────────

async def emit_matches(state: MatcherState) -> MatcherState:
    t0 = log_node_entry(
        logger, node="emit_matches",
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
    )
    log_node_exit(
        logger, node="emit_matches", t0=t0,
        execution_id=state["execution_id"], tenant_id=state["tenant_id"],
        summary=f"matches={len(state['matches'])} errors={len(state.get('errors', []))}",
    )
    return state


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_matcher_graph() -> Any:
    graph = StateGraph(MatcherState)

    graph.add_node("embed_content", embed_content)
    graph.add_node("vector_search", vector_search)
    graph.add_node("db_lookup", db_lookup)
    graph.add_node("rerank_candidates", rerank_candidates)
    graph.add_node("emit_matches", emit_matches)

    graph.set_entry_point("embed_content")
    graph.add_edge("embed_content", "vector_search")
    graph.add_edge("vector_search", "db_lookup")
    graph.add_edge("db_lookup", "rerank_candidates")
    graph.add_edge("rerank_candidates", "emit_matches")
    graph.add_edge("emit_matches", END)

    return graph.compile()


# ── Public run function ───────────────────────────────────────────────────────

async def run_matcher_agent(agent_input: MatcherAgentInput) -> MatcherAgentOutput:
    t0 = time.monotonic()

    initial_state: MatcherState = {
        "input": agent_input,
        "embedding": [],
        "vector_candidates": [],
        "db_candidates": [],
        "all_candidates": [],
        "matches": [],
        "errors": [],
        "execution_id": agent_input.execution_id,
        "tenant_id": agent_input.tenant_id,
        "t0": t0,
    }

    graph = build_matcher_graph()
    with langfuse_trace(
        tenant_id=agent_input.tenant_id or "unknown",
        execution_id=agent_input.execution_id or "unknown",
        service="generic_matcher",
        skill_id="match_content_to_entities",
    ) as lf_cfg:
        final_state: MatcherState = await graph.ainvoke(initial_state, config=lf_cfg)

    return MatcherAgentOutput(
        matches=final_state["matches"],
        total_candidates=len(final_state["all_candidates"]),
        total_matches=len(final_state["matches"]),
        execution_id=agent_input.execution_id,
        duration_ms=int((time.monotonic() - t0) * 1000),
        errors=final_state.get("errors", []),
    )
