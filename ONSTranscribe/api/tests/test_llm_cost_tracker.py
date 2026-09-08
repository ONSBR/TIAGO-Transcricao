"""Métricas de uso LLM (tokens/modelo/duração) — sem conversão para USD."""
from types import SimpleNamespace

import pytest

from app.services.llm_cost_tracker import (
    JobLlmUsageAccumulator,
    LLMCallMetrics,
    extract_usage,
)


@pytest.mark.unit
def test_extract_usage_camel_case():
    resp = SimpleNamespace(raw={"usage": {"inputTokens": 10, "outputTokens": 20}})
    assert extract_usage(resp) == (10, 20)


@pytest.mark.unit
def test_extract_usage_snake_case():
    resp = SimpleNamespace(raw={"usage": {"input_tokens": 3, "output_tokens": 7}})
    assert extract_usage(resp) == (3, 7)


@pytest.mark.unit
def test_job_accumulator_to_dict_without_usd():
    acc = JobLlmUsageAccumulator()
    acc.record(LLMCallMetrics("beautify", "m1", 100, 50, 1.2))
    acc.record(
        LLMCallMetrics(
            "summary",
            "m2",
            80,
            40,
            0.5,
            fallback=True,
            fallback_reason="json_parse_failure",
            original_model="m1",
        )
    )
    d = acc.to_dict()
    assert "cost_usd" not in d
    assert "total_cost_usd" not in d
    assert d["total_input_tokens"] == 180
    assert d["total_output_tokens"] == 90
    assert d["fallback_used"] is True
    assert d["fallback_steps"] == "summary"
    assert d["beautify"]["model"] == "m1"
    assert d["summary"]["fallback"] is True
    assert d["summary"]["original_model"] == "m1"
