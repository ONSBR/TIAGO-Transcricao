"""Métricas operacionais de chamadas LLM (tokens, modelo, duração, fallback).

Não converte token → USD. Preço e projeção de custo ficam na camada de
comparação (Lambda de métricas / scripts de análise), com catálogo único lá.
"""
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LLMCallMetrics:
    step: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_s: float
    fallback: bool = False
    fallback_reason: str | None = None
    original_model: str | None = None


def extract_usage(response) -> tuple[int, int]:
    """Extrai tokens de entrada e saída da resposta do BedrockConverse (LlamaIndex).

    Returns:
        (input_tokens, output_tokens) — zeros se não disponível.
    """
    try:
        raw = getattr(response, "raw", None) or {}
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        input_tokens = usage.get("inputTokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("outputTokens", usage.get("output_tokens", 0))
        return int(input_tokens or 0), int(output_tokens or 0)
    except Exception as exc:
        logger.warning("Não foi possível extrair usage da resposta LLM: %s", exc)
        return 0, 0


def log_llm_call(metrics: LLMCallMetrics) -> None:
    """Log estruturado (CloudWatch-friendly) de uma chamada LLM."""
    record: dict = {
        "event": "llm_call",
        "step": metrics.step,
        "model": metrics.model,
        "input_tokens": metrics.input_tokens,
        "output_tokens": metrics.output_tokens,
        "duration_s": round(metrics.duration_s, 3),
    }
    if metrics.fallback:
        record["fallback"] = True
        record["fallback_reason"] = metrics.fallback_reason
        record["original_model"] = metrics.original_model
    logger.info(json.dumps(record, ensure_ascii=False))


def log_llm_fallback(step: str, original_model: str, fallback_model: str, reason: str) -> None:
    """Log explícito de fallback de modelo."""
    logger.warning(json.dumps({
        "event": "llm_fallback",
        "step": step,
        "original_model": original_model,
        "fallback_model": fallback_model,
        "reason": reason,
    }, ensure_ascii=False))


@dataclass
class JobLlmUsageAccumulator:
    """Acumula métricas de uso LLM de todas as etapas de um job (sem USD)."""

    _steps: dict[str, LLMCallMetrics] = field(default_factory=dict)
    _fallback_steps: list[str] = field(default_factory=list)

    def record(self, metrics: LLMCallMetrics) -> None:
        self._steps[metrics.step] = metrics
        if metrics.fallback and metrics.step not in self._fallback_steps:
            self._fallback_steps.append(metrics.step)

    def to_dict(self) -> dict:
        result: dict = {}
        total_in = 0
        total_out = 0
        for step, m in self._steps.items():
            result[step] = {
                "model": m.model,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "duration_s": round(m.duration_s, 3),
            }
            if m.fallback:
                result[step]["fallback"] = True
                if m.fallback_reason:
                    result[step]["fallback_reason"] = m.fallback_reason
                if m.original_model:
                    result[step]["original_model"] = m.original_model
            total_in += m.input_tokens
            total_out += m.output_tokens
        result["total_input_tokens"] = total_in
        result["total_output_tokens"] = total_out
        result["fallback_used"] = bool(self._fallback_steps)
        result["fallback_steps"] = ",".join(self._fallback_steps) if self._fallback_steps else ""
        return result


# Alias legado do nome da branch de estudo (evita quebrar imports em diff/review).
JobCostAccumulator = JobLlmUsageAccumulator
