"""Guardrail exception types."""

from __future__ import annotations

from enum import Enum


class GuardrailSeverity(str, Enum):
    LOW = "low"        # log and continue
    MEDIUM = "medium"  # log, redact, continue
    HIGH = "high"      # log and block


class GuardrailViolation(Exception):
    """
    Raised when a guardrail check fails with severity HIGH (blocking).

    For MEDIUM severity, use redaction helpers instead of raising.
    """

    def __init__(
        self,
        guard_type: str,
        reason: str,
        severity: GuardrailSeverity = GuardrailSeverity.HIGH,
        input_summary: str = "",
    ) -> None:
        self.guard_type = guard_type
        self.reason = reason
        self.severity = severity
        self.input_summary = input_summary
        super().__init__(f"[{guard_type}] {reason}")

    def to_dict(self) -> dict[str, str]:
        return {
            "guard_type": self.guard_type,
            "reason": self.reason,
            "severity": self.severity.value,
            "input_summary": self.input_summary,
        }
