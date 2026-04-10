"""
Structured JSON logging for all AIWorks services.

Usage
-----
    from app.core.logging import get_logger, log_node_entry, log_node_exit, ...

    logger = get_logger("content_curator")

    log_node_entry(logger, node="scrape_sources", execution_id="exec-123",
                   tenant_id="t001", step_id="step-1")

    log_node_exit(logger, node="scrape_sources", execution_id="exec-123",
                  tenant_id="t001", step_id="step-1", elapsed_ms=342,
                  summary="scraped 5 pages")

Log levels
----------
  INFO  — step transitions, policy decisions, guardrail verdicts (default)
  DEBUG — full tool args/results, LLM prompt tokens, node state diffs
  TRACE — raw HTTP bodies, full LLM completions (set LOG_LEVEL=TRACE)

All records include: service, execution_id, tenant_id, step_id (when available).
PII / raw article text is truncated to 200 chars at INFO level.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

# ── TRACE level (below DEBUG=10) ──────────────────────────────────────────────
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)  # type: ignore[attr-defined]


logging.Logger.trace = _trace  # type: ignore[attr-defined]


# ── JSON formatter ─────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """
    Emits one JSON object per log record.
    Extra fields passed via `extra={}` are merged into the top-level object.
    """

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f"),
            "level": record.levelname,
            "service": getattr(record, "service", record.name),
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields injected via logger.info(..., extra={...})
        skip = {
            "args", "created", "exc_info", "exc_text", "filename", "funcName",
            "levelname", "levelno", "lineno", "message", "module", "msecs",
            "msg", "name", "pathname", "process", "processName", "relativeCreated",
            "stack_info", "thread", "threadName",
        }
        for key, val in record.__dict__.items():
            if key not in skip and not key.startswith("_"):
                base[key] = val

        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)

        return json.dumps(base, default=str)


# ── Logger factory ─────────────────────────────────────────────────────────────

def get_logger(service: str) -> logging.Logger:
    """
    Return a logger configured for structured JSON output.

    The log level is read from the LOG_LEVEL env var (default INFO).
    Setting LOG_LEVEL=DEBUG enables full tool/LLM details.
    Setting LOG_LEVEL=TRACE enables raw HTTP bodies and full LLM completions.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = TRACE if level_name == "TRACE" else getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(service)
    if logger.handlers:
        # Already configured — just update level in case env changed.
        logger.setLevel(level)
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.setLevel(level)

    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


# ── PII scrubbing helper ───────────────────────────────────────────────────────

def _truncate(value: Any, max_len: int = 200) -> str:
    """Truncate long strings to avoid leaking PII / raw article text at INFO level."""
    s = str(value)
    return s if len(s) <= max_len else s[:max_len] + "…"


def _safe(value: Any, level: int, logger: logging.Logger) -> Any:
    """Return full value at DEBUG+, truncated at INFO."""
    if logger.isEnabledFor(logging.DEBUG):
        return value
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    return value


# ── Structured log helpers ─────────────────────────────────────────────────────

def log_node_entry(
    logger: logging.Logger,
    *,
    node: str,
    execution_id: str = "",
    tenant_id: str = "",
    step_id: str = "",
    extra: dict[str, Any] | None = None,
) -> float:
    """
    Log entry into a LangGraph node.  Returns a start timestamp for elapsed_ms.

    Example output (INFO):
      {"level":"INFO","service":"content_curator","event":"node_entry",
       "node":"scrape_sources","execution_id":"exec-123","tenant_id":"t001"}
    """
    t0 = time.monotonic()
    logger.info(
        "[%s] → enter",
        node,
        extra={
            "event": "node_entry",
            "node": node,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
            "step_id": step_id,
            **(extra or {}),
        },
    )
    return t0


def log_node_exit(
    logger: logging.Logger,
    *,
    node: str,
    t0: float,
    execution_id: str = "",
    tenant_id: str = "",
    step_id: str = "",
    summary: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Log exit from a LangGraph node with elapsed time and a human summary.

    Example output (INFO):
      {"level":"INFO","event":"node_exit","node":"scrape_sources",
       "elapsed_ms":342,"summary":"scraped 5 pages"}
    """
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "[%s] ← done  %dms  %s",
        node,
        elapsed_ms,
        summary,
        extra={
            "event": "node_exit",
            "node": node,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
            "step_id": step_id,
            "elapsed_ms": elapsed_ms,
            "summary": summary,
            **(extra or {}),
        },
    )


def log_node_error(
    logger: logging.Logger,
    *,
    node: str,
    error: Exception | str,
    t0: float | None = None,
    execution_id: str = "",
    tenant_id: str = "",
    step_id: str = "",
) -> None:
    elapsed_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else None
    logger.error(
        "[%s] ✗ error: %s",
        node,
        error,
        extra={
            "event": "node_error",
            "node": node,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
            "step_id": step_id,
            "error": str(error),
            **({"elapsed_ms": elapsed_ms} if elapsed_ms is not None else {}),
        },
    )


def log_tool_call(
    logger: logging.Logger,
    *,
    tool: str,
    args: dict[str, Any],
    result: Any = None,
    elapsed_ms: int | None = None,
    execution_id: str = "",
    tenant_id: str = "",
    error: str | None = None,
) -> None:
    """
    Log a tool invocation (MCP call, DB query, external API).

    At INFO: tool name, truncated args summary, elapsed_ms.
    At DEBUG: full args and result.
    """
    args_safe = _safe(args, logging.DEBUG, logger)
    result_safe = _safe(result, logging.DEBUG, logger) if result is not None else None

    if error:
        logger.warning(
            "[tool:%s] ✗ error=%s",
            tool,
            _truncate(error),
            extra={
                "event": "tool_call_error",
                "tool": tool,
                # "args" is reserved on LogRecord — use tool_args
                "tool_args": args_safe,
                "error": error,
                "elapsed_ms": elapsed_ms,
                "execution_id": execution_id,
                "tenant_id": tenant_id,
            },
        )
    else:
        logger.debug(
            "[tool:%s] ✓ %dms",
            tool,
            elapsed_ms or 0,
            extra={
                "event": "tool_call",
                "tool": tool,
                "tool_args": args_safe,
                "result_summary": result_safe,
                "elapsed_ms": elapsed_ms,
                "execution_id": execution_id,
                "tenant_id": tenant_id,
            },
        )


def log_llm_call(
    logger: logging.Logger,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    execution_id: str = "",
    tenant_id: str = "",
    step_id: str = "",
    purpose: str = "",
) -> None:
    """
    Log an LLM invocation with token counts and latency.

    Example output (DEBUG):
      {"event":"llm_call","model":"gpt-4o-mini","prompt_tokens":512,
       "completion_tokens":128,"latency_ms":1240,"purpose":"match_products"}
    """
    total = prompt_tokens + completion_tokens
    logger.debug(
        "[llm:%s] %s  in=%d out=%d total=%d  %dms",
        model,
        purpose,
        prompt_tokens,
        completion_tokens,
        total,
        latency_ms,
        extra={
            "event": "llm_call",
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
            "latency_ms": latency_ms,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
            "step_id": step_id,
            "purpose": purpose,
        },
    )


def log_policy_check(
    logger: logging.Logger,
    *,
    check_type: str,
    resource_id: str,
    result: str,
    reason: str = "",
    execution_id: str = "",
    tenant_id: str = "",
) -> None:
    """
    Log a policy check (skill allowed/blocked, tool allowed/blocked, budget check).

    result should be "ALLOWED" | "BLOCKED" | "REQUIRES_APPROVAL".
    """
    level = logging.INFO if result == "ALLOWED" else logging.WARNING
    logger.log(
        level,
        "[policy:%s] %s %s  %s",
        check_type,
        result,
        resource_id,
        reason,
        extra={
            "event": "policy_check",
            "check_type": check_type,
            "resource_id": resource_id,
            "result": result,
            "reason": reason,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
        },
    )


def log_guardrail_check(
    logger: logging.Logger,
    *,
    guard_type: str,
    input_summary: str,
    verdict: str,
    reason: str = "",
    execution_id: str = "",
    tenant_id: str = "",
) -> None:
    """
    Log a guardrail check result.

    verdict should be "PASS" | "BLOCK" | "REDACT".
    """
    level = logging.INFO if verdict == "PASS" else logging.WARNING
    logger.log(
        level,
        "[guard:%s] %s  %s",
        guard_type,
        verdict,
        reason,
        extra={
            "event": "guardrail_check",
            "guard_type": guard_type,
            "input_summary": _truncate(input_summary),
            "verdict": verdict,
            "reason": reason,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
        },
    )


def log_scraping_limit_check(
    logger: logging.Logger,
    *,
    url: str,
    current_depth: int,
    current_total: int,
    limits_max_depth: int,
    limits_max_total: int,
    verdict: str,
    execution_id: str = "",
    tenant_id: str = "",
) -> None:
    """
    Log a scraping limit enforcement check.

    verdict: "ALLOWED" | "BLOCKED_DEPTH" | "BLOCKED_TOTAL"
    """
    level = logging.DEBUG if verdict == "ALLOWED" else logging.WARNING
    logger.log(
        level,
        "[scraping_limit] %s  url=%s  depth=%d/%d  total=%d/%d",
        verdict,
        _truncate(url, 80),
        current_depth,
        limits_max_depth,
        current_total,
        limits_max_total,
        extra={
            "event": "scraping_limit_check",
            "url": _truncate(url, 80),
            "current_depth": current_depth,
            "current_total": current_total,
            "limits_max_depth": limits_max_depth,
            "limits_max_total": limits_max_total,
            "verdict": verdict,
            "execution_id": execution_id,
            "tenant_id": tenant_id,
        },
    )
