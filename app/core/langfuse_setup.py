"""
Langfuse observability for LangChain / LangGraph.

- Initialize the Langfuse client when API keys are configured.
- Use `langfuse_trace` around graph runs and planner LLM calls to attach
  callbacks and propagate tenant / execution metadata.
- Inside LangGraph nodes, pass `config=graph_runnable_config()` into `llm.ainvoke`
  so nested generations inherit the same trace.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import settings

logger = logging.getLogger(__name__)

_client_initialized = False


def init_langfuse() -> None:
    """Register the Langfuse singleton when keys are present. Idempotent."""
    global _client_initialized
    if _client_initialized or not settings.langfuse_enabled:
        return
    from langfuse import Langfuse

    kwargs: dict[str, Any] = {
        "public_key": settings.langfuse_public_key,
        "secret_key": settings.langfuse_secret_key,
        "base_url": settings.langfuse_base_url or None,
    }
    if settings.langfuse_environment:
        kwargs["environment"] = settings.langfuse_environment
    Langfuse(**kwargs)
    _client_initialized = True
    logger.info("Langfuse client initialized (tracing enabled)")


def flush_langfuse() -> None:
    """Best-effort flush of pending Langfuse spans (call on shutdown)."""
    if not settings.langfuse_enabled:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:
        logger.debug("Langfuse flush skipped: %s", exc)


def graph_runnable_config() -> dict[str, Any]:
    """RunnableConfig from the current LangGraph run, for nested LLM calls."""
    from langgraph.config import get_config

    try:
        return get_config()
    except RuntimeError:
        return {}


@contextmanager
def langfuse_trace(
    *,
    tenant_id: uuid.UUID | str,
    execution_id: uuid.UUID | str,
    service: str,
    skill_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Context manager that yields a LangChain RunnableConfig with Langfuse callbacks
    and trace-level attributes (tenant, execution, optional skill).

    When Langfuse is disabled, yields an empty dict.
    """
    if not settings.langfuse_enabled:
        yield {}
        return
    if not str(execution_id).strip():
        yield {}
        return

    init_langfuse()
    from langfuse import get_client, propagate_attributes
    from langfuse.langchain import CallbackHandler

    handler = CallbackHandler()
    meta: dict[str, str] = {
        "tenant_id": str(tenant_id),
        "execution_id": str(execution_id),
    }
    if skill_id:
        meta["skill_id"] = skill_id
    meta = {k: v[:200] for k, v in meta.items()}

    tags = [service]
    trace_name = f"{service}:{skill_id}" if skill_id else service
    root_name = trace_name[:200]

    lf = get_client()
    # Root OTel span so LangChain callbacks and propagate_attributes export as a trace.
    with lf.start_as_current_observation(name=root_name, as_type="chain"):
        with propagate_attributes(
            user_id=str(tenant_id),
            session_id=str(execution_id),
            tags=tags,
            trace_name=root_name,
            metadata=meta,
        ):
            try:
                yield {"callbacks": [handler]}
            finally:
                flush_langfuse()
