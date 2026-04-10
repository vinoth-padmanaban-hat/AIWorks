"""
Input guardrails — run before LLM calls and tool invocations.

Functions:
  validate_json_schema(data, schema)  — structural validation
  check_prompt_injection(text)        — heuristic injection detection
  redact_pii(text)                    — regex-based PII scrubbing
"""

from __future__ import annotations

import re
from typing import Any

from app.guardrails.audit import log_guardrail_result
from app.guardrails.exceptions import GuardrailSeverity, GuardrailViolation

# ── Prompt injection heuristics ───────────────────────────────────────────────
# Patterns that strongly suggest an attempt to override system instructions.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"forget\s+(everything|all)\s+(you|i)\s+(were|was|have)", re.I),
    re.compile(r"you\s+are\s+now\s+(a\s+)?(?:dan|evil|jailbreak|unrestricted)", re.I),
    re.compile(r"act\s+as\s+(if\s+you\s+are\s+)?(?:an?\s+)?(?:evil|unrestricted|jailbreak)", re.I),
    re.compile(r"do\s+not\s+follow\s+(your\s+)?(guidelines|rules|policy)", re.I),
    re.compile(r"system\s*prompt\s*[:=]", re.I),
    re.compile(r"<\s*/?system\s*>", re.I),
    re.compile(r"\[INST\]|\[/INST\]", re.I),
]

# ── PII patterns ──────────────────────────────────────────────────────────────
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    # Phone numbers (loose: +1-800-555-1234, (800) 555-1234, 8005551234)
    (re.compile(r"(\+?\d[\d\s\-().]{7,}\d)"), "[PHONE]"),
    # Credit card numbers (4 groups of 4 digits)
    (re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "[CARD]"),
    # SSN (US)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    # API keys / tokens (long alphanumeric strings that look like secrets)
    (re.compile(r"\b(sk|pk|api|key|token|secret)[_\-]?[A-Za-z0-9]{20,}\b", re.I), "[SECRET]"),
]


def validate_json_schema(
    data: Any,
    schema: dict[str, Any],
    *,
    guard_type: str = "json_schema",
) -> None:
    """
    Validate `data` against a JSON Schema dict.
    Raises GuardrailViolation(HIGH) if validation fails.

    Uses jsonschema if available; falls back to a basic type check.
    """
    try:
        import jsonschema  # type: ignore[import]

        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            log_guardrail_result(
                guard_type,
                passed=False,
                reason=exc.message,
                extra={"validator": "jsonschema"},
            )
            raise GuardrailViolation(
                guard_type=guard_type,
                reason=exc.message,
                severity=GuardrailSeverity.HIGH,
                input_summary=str(data)[:200],
            ) from exc
        log_guardrail_result(guard_type, passed=True, extra={"validator": "jsonschema"})
    except ImportError:
        # Minimal fallback: check required fields if schema has them
        required = schema.get("required", [])
        if required and isinstance(data, dict):
            missing = [f for f in required if f not in data]
            if missing:
                msg = f"Missing required fields: {missing}"
                log_guardrail_result(
                    guard_type,
                    passed=False,
                    reason=msg,
                    extra={"validator": "fallback"},
                )
                raise GuardrailViolation(
                    guard_type=guard_type,
                    reason=msg,
                    severity=GuardrailSeverity.HIGH,
                    input_summary=str(list(data.keys()))[:200],
                )
        log_guardrail_result(guard_type, passed=True, extra={"validator": "fallback"})


def check_prompt_injection(text: str) -> None:
    """
    Heuristic check for prompt injection attempts.
    Raises GuardrailViolation(HIGH) on detection.

    Should be called on any user-supplied text before it is included in an LLM prompt.
    """
    if not text:
        return
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            reason = f"Possible prompt injection detected: '{match.group()}'"
            log_guardrail_result(
                "prompt_injection",
                passed=False,
                reason=reason,
                extra={"pattern": pattern.pattern[:80]},
            )
            raise GuardrailViolation(
                guard_type="prompt_injection",
                reason=reason,
                severity=GuardrailSeverity.HIGH,
                input_summary=text[:200],
            )
    log_guardrail_result("prompt_injection", passed=True)


def redact_pii(text: str) -> str:
    """
    Replace PII patterns with placeholder tokens.
    Returns the redacted string.  Does NOT raise — always returns a safe version.

    Use this before logging or storing user-supplied text.
    """
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_url(url: str) -> str:
    """
    Basic URL sanity check — reject non-http(s) schemes.
    Raises GuardrailViolation(HIGH) for file://, javascript:, data: etc.
    """
    stripped = url.strip().lower()
    if not stripped.startswith(("http://", "https://")):
        reason = f"Disallowed URL scheme in: {url[:100]}"
        log_guardrail_result("url_scheme", passed=False, reason=reason)
        raise GuardrailViolation(
            guard_type="url_scheme",
            reason=reason,
            severity=GuardrailSeverity.HIGH,
            input_summary=url[:200],
        )
    log_guardrail_result("url_scheme", passed=True, extra={"url_host": url[:80]})
    return url.strip()
