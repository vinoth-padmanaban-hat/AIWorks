"""
Output guardrails — run after LLM calls and before persisting/returning results.

Functions:
  check_output_schema(output, schema)          — validate LLM output structure
  check_unsafe_content(text)                   — keyword/regex safety filter
  redact_sensitive_fields(output, keys)        — strip secrets before logging
  check_output_length(text, max_chars)         — length sanity check
"""

from __future__ import annotations

import re
from typing import Any

from app.guardrails.audit import log_guardrail_result
from app.guardrails.exceptions import GuardrailSeverity, GuardrailViolation
from app.guardrails.input_filters import validate_json_schema

# ── Unsafe content patterns ───────────────────────────────────────────────────
# Conservative list — add domain-specific patterns as needed.
_UNSAFE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(how\s+to\s+(make|build|create)\s+(a\s+)?(bomb|weapon|explosive))\b", re.I),
    re.compile(r"\b(self[\s\-]harm|suicide\s+method)\b", re.I),
    re.compile(r"\b(child\s+(sexual|pornograph))\b", re.I),
]

# Fields that should never appear in logs or API responses
_DEFAULT_SENSITIVE_KEYS = {
    "password", "secret", "api_key", "apikey", "token", "private_key",
    "access_token", "refresh_token", "client_secret", "auth",
}


def check_output_schema(output: Any, schema: dict[str, Any]) -> None:
    """
    Validate LLM-generated output against a JSON schema.
    Delegates to input_filters.validate_json_schema (same logic, different context).
    Raises GuardrailViolation(HIGH) on failure.
    """
    validate_json_schema(output, schema, guard_type="output_schema")


def check_unsafe_content(text: str) -> None:
    """
    Scan text for obviously unsafe content patterns.
    Raises GuardrailViolation(HIGH) on detection.

    This is a lightweight keyword/regex filter — not a classifier.
    For production, augment with a classifier LLM or external safety API.
    """
    if not text:
        return
    for pattern in _UNSAFE_PATTERNS:
        match = pattern.search(text)
        if match:
            reason = f"Unsafe content pattern detected: '{match.group()}'"
            log_guardrail_result(
                "unsafe_content",
                passed=False,
                reason=reason,
                extra={"pattern": pattern.pattern[:80]},
            )
            raise GuardrailViolation(
                guard_type="unsafe_content",
                reason=reason,
                severity=GuardrailSeverity.HIGH,
                input_summary=text[:200],
            )
    log_guardrail_result("unsafe_content", passed=True)


def redact_sensitive_fields(
    output: dict[str, Any],
    sensitive_keys: set[str] | None = None,
) -> dict[str, Any]:
    """
    Return a copy of `output` with sensitive field values replaced by "[REDACTED]".
    Does NOT raise — always returns a safe copy.

    Use before logging or returning data to external callers.
    """
    keys = (sensitive_keys or set()) | _DEFAULT_SENSITIVE_KEYS
    return {
        k: "[REDACTED]" if k.lower() in keys else v
        for k, v in output.items()
    }


def check_output_length(text: str, max_chars: int = 50_000) -> None:
    """
    Sanity-check that LLM output is not unreasonably long.
    Raises GuardrailViolation(MEDIUM) if exceeded — caller should truncate.
    """
    if len(text) > max_chars:
        reason = f"Output length {len(text)} exceeds limit {max_chars}."
        log_guardrail_result(
            "output_length",
            passed=False,
            reason=reason,
            extra={"length": len(text), "max_chars": max_chars},
        )
        raise GuardrailViolation(
            guard_type="output_length",
            reason=reason,
            severity=GuardrailSeverity.MEDIUM,
            input_summary=text[:200],
        )
    log_guardrail_result(
        "output_length",
        passed=True,
        extra={"length": len(text), "max_chars": max_chars},
    )
