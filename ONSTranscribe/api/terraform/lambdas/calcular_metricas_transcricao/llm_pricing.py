"""Catálogo de preços Bedrock e conversão token → USD estimado.

SSOT de preço para a Lambda de métricas de qualidade / comparação de modelos.
Atualizar aqui quando a tabela oficial da AWS mudar:
  https://aws.amazon.com/bedrock/pricing/

Preços em USD por 1.000 tokens (on-demand, us-east-1, referência 2026).
Valores são estimativa de laboratório — a fatura real é Cost Explorer / CUR.
"""
from __future__ import annotations

from typing import Any, Mapping

# model_id (ou sufixo reconhecível) → {input, output} USD / 1k tokens
BEDROCK_PRICING_PER_1K: dict[str, dict[str, float]] = {
    # Claude Sonnet
    "us.anthropic.claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "anthropic.claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "us.anthropic.claude-sonnet-4-5": {"input": 0.003, "output": 0.015},
    "anthropic.claude-sonnet-4-5": {"input": 0.003, "output": 0.015},
    "anthropic.claude-3-5-sonnet-20240620-v1:0": {"input": 0.003, "output": 0.015},
    # Claude Haiku
    "us.anthropic.claude-haiku-4-5": {"input": 0.001, "output": 0.005},
    "anthropic.claude-3-5-haiku-20241022-v1:0": {"input": 0.0008, "output": 0.004},
    "anthropic.claude-3-haiku-20240307-v1:0": {"input": 0.00025, "output": 0.00125},
    # Amazon Nova 2 / 1
    "amazon.nova-2-lite-v1:0": {"input": 0.0003, "output": 0.0025},
    "amazon.nova-lite-v1:0": {"input": 0.00006, "output": 0.00024},
    "amazon.nova-micro-v1:0": {"input": 0.000035, "output": 0.00014},
    "amazon.nova-pro-v1:0": {"input": 0.0008, "output": 0.0032},
}

_LLM_STEPS = ("beautify", "entities", "subject", "summary")


def pricing_for(model_id: str) -> dict[str, float] | None:
    """Resolve preço por model_id exato ou por sufixo conhecido."""
    if not model_id:
        return None
    if model_id in BEDROCK_PRICING_PER_1K:
        return BEDROCK_PRICING_PER_1K[model_id]
    # inference profile: us.anthropic... / eu....
    bare = model_id.split(".", 1)[-1] if model_id.startswith(("us.", "eu.", "ap.", "global.")) else model_id
    for key, pricing in BEDROCK_PRICING_PER_1K.items():
        if key == bare or key.endswith(bare) or bare in key or key in model_id:
            return pricing
    return None


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Custo estimado em USD; None se o modelo não está no catálogo."""
    pricing = pricing_for(model_id)
    if not pricing:
        return None
    return (input_tokens / 1000.0) * pricing["input"] + (output_tokens / 1000.0) * pricing["output"]


def extract_llm_usage(transcricao: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Lê llm_usage_metrics (novo) ou llm_cost_metrics (legado Matheus) do JSON da API."""
    if not isinstance(transcricao, dict):
        return None
    ta = transcricao.get("transcricaoAudio") or {}
    if not isinstance(ta, dict):
        return None
    usage = ta.get("llm_usage_metrics")
    if isinstance(usage, dict) and usage:
        return usage
    legacy = ta.get("llm_cost_metrics")
    if isinstance(legacy, dict) and legacy:
        return legacy
    return None


def enrich_usage_with_cost(usage: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Acrescenta cost_usd estimado por etapa e total a partir do catálogo.

    Retorna None se não houver usage. Modelos sem preço: cost_usd da etapa = None;
    total só soma etapas com preço conhecido (e marca pricing_incomplete).
    """
    if not usage:
        return None

    steps_out: dict[str, Any] = {}
    total_in = 0
    total_out = 0
    total_cost = 0.0
    has_cost = False
    pricing_incomplete = False

    for step in _LLM_STEPS:
        step_data = usage.get(step)
        if not isinstance(step_data, dict):
            continue
        model = str(step_data.get("model") or "")
        in_tok = int(step_data.get("input_tokens") or 0)
        out_tok = int(step_data.get("output_tokens") or 0)
        cost = estimate_cost_usd(model, in_tok, out_tok)
        entry = {
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "duration_s": step_data.get("duration_s"),
            "cost_usd": round(cost, 8) if cost is not None else None,
        }
        if step_data.get("fallback"):
            entry["fallback"] = True
            if step_data.get("fallback_reason"):
                entry["fallback_reason"] = step_data["fallback_reason"]
            if step_data.get("original_model"):
                entry["original_model"] = step_data["original_model"]
        if cost is None and model:
            pricing_incomplete = True
        if cost is not None:
            total_cost += cost
            has_cost = True
        total_in += in_tok
        total_out += out_tok
        steps_out[step] = entry

    # Totais do payload da API, se existirem e steps vazios
    if not steps_out:
        total_in = int(usage.get("total_input_tokens") or 0)
        total_out = int(usage.get("total_output_tokens") or 0)

    result: dict[str, Any] = {
        **steps_out,
        "total_input_tokens": int(usage.get("total_input_tokens") or total_in),
        "total_output_tokens": int(usage.get("total_output_tokens") or total_out),
        "total_cost_usd": round(total_cost, 8) if has_cost else None,
        "pricing_incomplete": pricing_incomplete,
        "fallback_used": bool(usage.get("fallback_used")),
        "fallback_steps": usage.get("fallback_steps") or "",
        "pricing_catalog": "bedrock_ondemand_per_1k_tokens_v1",
    }
    return result


def flat_llm_cost_fields(enriched: Mapping[str, Any] | None) -> dict[str, Any]:
    """Campos planos para registro Athena / por_audio."""
    if not enriched:
        return {
            "llm_total_input_tokens": None,
            "llm_total_output_tokens": None,
            "llm_estimated_cost_usd": None,
            "llm_pricing_incomplete": None,
            "llm_fallback_used": None,
        }
    return {
        "llm_total_input_tokens": enriched.get("total_input_tokens"),
        "llm_total_output_tokens": enriched.get("total_output_tokens"),
        "llm_estimated_cost_usd": enriched.get("total_cost_usd"),
        "llm_pricing_incomplete": enriched.get("pricing_incomplete"),
        "llm_fallback_used": enriched.get("fallback_used"),
    }
