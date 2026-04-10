"""Extract a text representation of agent output for LLM-judge metrics."""

from __future__ import annotations

import json
from typing import Any


def extract_output_text_for_eval(output: dict[str, Any]) -> str:
    """
    Best-effort string for DeepEval / judge prompts.

    Tries common keys used by agents, then shallow nested dicts, then JSON.
    """
    if not output:
        return ""

    for key in ("message", "text", "content", "summary", "answer", "body"):
        v = output.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    for outer in ("result", "data", "output"):
        inner = output.get(outer)
        if isinstance(inner, dict):
            nested = extract_output_text_for_eval(inner)
            if nested:
                return nested
        if isinstance(inner, str) and inner.strip():
            return inner.strip()

    try:
        return json.dumps(output, ensure_ascii=False, default=str)[:12000]
    except Exception:
        return str(output)[:12000]
