"""Resolução de modelo Bedrock por etapa do pipeline LLM.

Prioridade (por etapa):
  1. Parameter Store (JSON em LLM_STEP_MODELS_SSM_PARAMETER) — muda on-the-fly
  2. Variável de ambiente LLM_MODEL_<STEP>
  3. TRANSCRIBE_BEDROCK_MODEL (baseline único)

SSM cacheia com TTL curto (default 60s) para permitir put-parameter sem redeploy
sem martelar a API a cada token.

Formato do parâmetro SSM (String JSON):
  {
    "beautify": "amazon.nova-2-lite-v1:0",
    "entities": "amazon.nova-2-lite-v1:0",
    "subject": "amazon.nova-2-lite-v1:0",
    "summary": "amazon.nova-2-lite-v1:0",
    "fallback": "us.anthropic.claude-sonnet-4-6"
  }
Chaves ausentes ou vazias caem no env/default. Objeto vazio {} = só env/default.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Mapping

import boto3

logger = logging.getLogger(__name__)

STEPS = ("beautify", "entities", "subject", "summary", "fallback")

_ENV_KEYS: dict[str, str] = {
    "beautify": "LLM_MODEL_BEAUTIFY",
    "entities": "LLM_MODEL_ENTITIES",
    "subject": "LLM_MODEL_SUBJECT",
    "summary": "LLM_MODEL_SUMMARY",
    "fallback": "LLM_FALLBACK_MODEL",
}

_MAX_TOKENS_ENV: dict[str, str] = {
    "beautify": "LLM_MAX_TOKENS_BEAUTIFY",
    "entities": "LLM_MAX_TOKENS_ENTITIES",
    "subject": "LLM_MAX_TOKENS_SUBJECT",
    "summary": "LLM_MAX_TOKENS_SUMMARY",
}

_DEFAULT_MAX_TOKENS: dict[str, int] = {
    # Maverick/Converse: teto prático ≤8000; 16000 quebrava ValidationException.
    "beautify": 8000,
    "entities": 4000,
    "subject": 2000,
    "summary": 4000,
}

_DEFAULT_BASELINE = "us.anthropic.claude-sonnet-4-6"

_ssm_cache: dict = {"map": None, "loaded_at": 0.0, "param_name": None}
_ssm_lock = threading.Lock()


def baseline_model() -> str:
    """Modelo único de fallback final (TRANSCRIBE_BEDROCK_MODEL)."""
    return (os.getenv("TRANSCRIBE_BEDROCK_MODEL") or _DEFAULT_BASELINE).strip()


def ssm_param_name() -> str | None:
    name = (os.getenv("LLM_STEP_MODELS_SSM_PARAMETER") or "").strip()
    return name or None


def ssm_cache_ttl_s() -> float:
    try:
        return float(os.getenv("LLM_STEP_MODELS_SSM_TTL_S", "60"))
    except ValueError:
        return 60.0


def _parse_ssm_json(raw: str) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("SSM llm_step_models deve ser um objeto JSON")
    out: dict[str, str] = {}
    for key, value in data.items():
        step = str(key).strip().lower()
        if step not in STEPS:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out[step] = text
    return out


def _fetch_ssm_map(param_name: str) -> dict[str, str]:
    client = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-east-1"))
    resp = client.get_parameter(Name=param_name)
    return _parse_ssm_json(resp["Parameter"]["Value"])


def get_ssm_step_models(*, force_refresh: bool = False) -> dict[str, str]:
    """Mapa etapa→model_id do SSM, com cache TTL. Falha → {} (fail-open)."""
    param_name = ssm_param_name()
    if not param_name:
        return {}

    now = time.time()
    ttl = ssm_cache_ttl_s()
    with _ssm_lock:
        cached_ok = (
            not force_refresh
            and _ssm_cache["map"] is not None
            and _ssm_cache["param_name"] == param_name
            and (now - float(_ssm_cache["loaded_at"])) < ttl
        )
        if cached_ok:
            return dict(_ssm_cache["map"])

        try:
            mapping = _fetch_ssm_map(param_name)
        except Exception as exc:
            # Fail-open: qualquer falha SSM (rede, IAM, JSON inválido) → env/default.
            logger.warning(
                "Falha ao ler SSM %s para modelos por etapa: %s. Usando env/default.",
                param_name,
                exc,
            )
            # Mantém cache anterior se houver
            if _ssm_cache["map"] is not None and _ssm_cache["param_name"] == param_name:
                return dict(_ssm_cache["map"])
            return {}

        _ssm_cache["map"] = mapping
        _ssm_cache["loaded_at"] = now
        _ssm_cache["param_name"] = param_name
        return dict(mapping)


def clear_ssm_cache() -> None:
    """Limpa cache (testes / force refresh operacional)."""
    with _ssm_lock:
        _ssm_cache["map"] = None
        _ssm_cache["loaded_at"] = 0.0
        _ssm_cache["param_name"] = None


def resolve_step_model(
    step: str,
    *,
    ssm_map: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve o model_id Bedrock para a etapa.

    Args:
        step: beautify | entities | subject | summary | fallback
        ssm_map: override do mapa SSM (testes); None = lê cache/SSM real
        env: override do environ (testes); None = os.environ
    """
    step = step.strip().lower()
    if step not in STEPS:
        raise ValueError(f"Etapa LLM desconhecida: {step}")

    environ = env if env is not None else os.environ
    mapping = dict(ssm_map) if ssm_map is not None else get_ssm_step_models()

    from_ssm = (mapping.get(step) or "").strip()
    if from_ssm:
        return from_ssm

    env_key = _ENV_KEYS[step]
    from_env = (environ.get(env_key) or "").strip()
    if from_env:
        return from_env

    # fallback step sem config própria → baseline
    baseline = (environ.get("TRANSCRIBE_BEDROCK_MODEL") or _DEFAULT_BASELINE).strip()
    return baseline


def resolve_max_tokens(step: str, *, env: Mapping[str, str] | None = None) -> int:
    """max_tokens por etapa (só env; default sensato)."""
    step = step.strip().lower()
    if step not in _DEFAULT_MAX_TOKENS:
        raise ValueError(f"Etapa LLM desconhecida para max_tokens: {step}")
    environ = env if env is not None else os.environ
    raw = (environ.get(_MAX_TOKENS_ENV[step]) or "").strip()
    if raw:
        return int(raw)
    return _DEFAULT_MAX_TOKENS[step]


def resolved_models_snapshot() -> dict[str, str]:
    """Snapshot atual (SSM+env) para log/diagnóstico."""
    return {step: resolve_step_model(step) for step in STEPS}
