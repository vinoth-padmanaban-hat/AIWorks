"""
AIWorks Guardrails Library
==========================

Inline safety and compliance checks.  Import and call these in every service
before/after LLM calls and tool invocations.

Quick reference
---------------
    from app.guardrails import (
        GuardrailViolation,
        check_prompt_injection,
        redact_pii,
        check_tool_allowed,
        check_scraping_limits,
        check_domain_allowed,
        check_output_schema,
        check_unsafe_content,
        redact_sensitive_fields,
    )

All blocking checks raise GuardrailViolation.
Redaction helpers return a safe copy and never raise.

Deployment model
----------------
This package runs in-process (library-first).  For heavier org-wide checks
(toxicity classifiers, regulatory rules) use app.guardrails.client (future).
"""

from app.guardrails.audit import log_guardrail_result
from app.guardrails.exceptions import GuardrailSeverity, GuardrailViolation
from app.guardrails.input_filters import (
    check_prompt_injection,
    redact_pii,
    sanitize_url,
    validate_json_schema,
)
from app.guardrails.output_filters import (
    check_output_length,
    check_output_schema,
    check_unsafe_content,
    redact_sensitive_fields,
)
from app.guardrails.tool_policies import (
    check_domain_allowed,
    check_scraping_limits,
    check_tool_allowed,
)

__all__ = [
    # Exceptions
    "GuardrailViolation",
    "GuardrailSeverity",
    # Audit
    "log_guardrail_result",
    # Input
    "validate_json_schema",
    "check_prompt_injection",
    "redact_pii",
    "sanitize_url",
    # Tool policies
    "check_tool_allowed",
    "check_scraping_limits",
    "check_domain_allowed",
    # Output
    "check_output_schema",
    "check_unsafe_content",
    "redact_sensitive_fields",
    "check_output_length",
]
