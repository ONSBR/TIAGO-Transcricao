"""Catálogo de preços e enriquecimento de llm_usage (Lambda de métricas)."""
import sys
from pathlib import Path

import pytest

# Importa o módulo ao lado de lambda_function (sem empacotar o Lambda inteiro).
_SYS_PATH = Path(__file__).resolve().parents[1] / "calcular_metricas_transcricao"
sys.path.insert(0, str(_SYS_PATH))

from llm_pricing import (  # noqa: E402
    enrich_usage_with_cost,
    estimate_cost_usd,
    extract_llm_usage,
    flat_llm_cost_fields,
    pricing_for,
)


def test_sonnet_priced_higher_than_nova_2_lite():
    sonnet = estimate_cost_usd("us.anthropic.claude-sonnet-4-6", 3000, 3000)
    nova = estimate_cost_usd("amazon.nova-2-lite-v1:0", 3000, 3000)
    assert sonnet is not None and nova is not None
    assert nova < sonnet


def test_unknown_model_returns_none_cost():
    assert estimate_cost_usd("modelo.fantasma-v0", 1000, 1000) is None
    assert pricing_for("modelo.fantasma-v0") is None


def test_extract_prefers_llm_usage_metrics():
    payload = {
        "transcricaoAudio": {
            "llm_usage_metrics": {"beautify": {"model": "a", "input_tokens": 1, "output_tokens": 2}},
            "llm_cost_metrics": {"beautify": {"model": "legacy"}},
        }
    }
    usage = extract_llm_usage(payload)
    assert usage["beautify"]["model"] == "a"


def test_extract_falls_back_to_legacy_llm_cost_metrics():
    payload = {
        "transcricaoAudio": {
            "llm_cost_metrics": {
                "beautify": {"model": "us.anthropic.claude-sonnet-4-6", "input_tokens": 1000, "output_tokens": 1000}
            }
        }
    }
    assert extract_llm_usage(payload)["beautify"]["model"].endswith("sonnet-4-6")


def test_extract_missing_returns_none():
    assert extract_llm_usage({}) is None
    assert extract_llm_usage({"transcricaoAudio": {}}) is None


def test_enrich_usage_with_cost_totals():
    usage = {
        "beautify": {
            "model": "us.anthropic.claude-sonnet-4-6",
            "input_tokens": 1000,
            "output_tokens": 1000,
            "duration_s": 1.0,
        },
        "entities": {
            "model": "amazon.nova-2-lite-v1:0",
            "input_tokens": 1000,
            "output_tokens": 1000,
            "duration_s": 0.5,
        },
        "fallback_used": False,
        "fallback_steps": "",
    }
    enriched = enrich_usage_with_cost(usage)
    assert enriched is not None
    # Sonnet 0.018 + Nova (0.0003+0.0025)=0.0028 → 0.0208
    assert enriched["total_cost_usd"] == pytest.approx(0.0208, rel=1e-6)
    assert enriched["beautify"]["cost_usd"] == pytest.approx(0.018)
    assert enriched["entities"]["cost_usd"] == pytest.approx(0.0028)
    assert enriched["pricing_incomplete"] is False
    assert enriched["pricing_catalog"] == "bedrock_ondemand_per_1k_tokens_v1"


def test_enrich_unknown_model_marks_incomplete():
    usage = {
        "summary": {"model": "desconhecido", "input_tokens": 10, "output_tokens": 10},
    }
    enriched = enrich_usage_with_cost(usage)
    assert enriched["summary"]["cost_usd"] is None
    assert enriched["total_cost_usd"] is None
    assert enriched["pricing_incomplete"] is True


def test_flat_fields_null_when_no_usage():
    flat = flat_llm_cost_fields(None)
    assert flat["llm_estimated_cost_usd"] is None
    assert flat["llm_total_input_tokens"] is None
