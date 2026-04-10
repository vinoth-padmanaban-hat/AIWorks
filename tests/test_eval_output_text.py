"""Unit tests for eval helpers (no LLM calls)."""

from app.eval.output_text import extract_output_text_for_eval


def test_extract_prefers_message() -> None:
    assert extract_output_text_for_eval({"message": " hello "}) == "hello"


def test_extract_nested_result() -> None:
    out = extract_output_text_for_eval({"result": {"text": "nested"}})
    assert "nested" in out


def test_extract_json_fallback() -> None:
    t = extract_output_text_for_eval({"a": 1, "b": [2]})
    assert '"a"' in t
