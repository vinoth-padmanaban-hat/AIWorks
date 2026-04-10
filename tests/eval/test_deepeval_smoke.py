"""DeepEval offline / LLM evaluation smoke tests.

LLM-backed tests are skipped unless OPENAI_API_KEY is set. Exact-match tests
validate the DeepEval + pytest wiring without calling external models.
"""

from __future__ import annotations

import os

import pytest
from deepeval import assert_test
from deepeval.metrics import ExactMatchMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams


def test_deepeval_exact_match_smoke() -> None:
    """No API keys required — verifies assert_test + metrics run in CI."""
    metric = ExactMatchMetric(threshold=1.0)
    test_case = LLMTestCase(
        input="ignored for exact match",
        actual_output="expected text",
        expected_output="expected text",
    )
    assert_test(test_case, [metric])


def _run_llm_deepeval() -> bool:
    return os.getenv("RUN_DEEPEVAL_LLM", "").lower() in ("1", "true", "yes")


@pytest.mark.llm
@pytest.mark.skipif(
    not _run_llm_deepeval() or not os.getenv("OPENAI_API_KEY"),
    reason="Set RUN_DEEPEVAL_LLM=1 and OPENAI_API_KEY to run LLM-backed DeepEval tests",
)
def test_deepeval_geval_smoke() -> None:
    """Opt-in: G-Eval against the default OpenAI model (requires network + API key)."""
    metric = GEval(
        name="Correctness",
        criteria=(
            "Determine whether the actual output agrees with the expected output "
            "for the given user input."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        threshold=0.5,
    )
    test_case = LLMTestCase(
        input="What is the refund window?",
        actual_output="You may return items within 30 days for a full refund.",
        expected_output="30-day refund policy.",
    )
    assert_test(test_case, [metric])
