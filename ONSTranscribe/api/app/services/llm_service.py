"""Serviços de chamadas ao LLM via AWS Bedrock: beautify, entidades e resumo.

Cada etapa (beautify, entities, subject, summary) resolve o model_id em runtime
via `llm_model_config` (SSM → env → TRANSCRIBE_BEDROCK_MODEL). Fallback de
modelo robusto em falha de JSON (entities/summary) quando configurado distinto.
"""
import ast
import json
import logging
import re
import threading
import time
import unicodedata

import boto3
from llama_index.core.llms import ChatMessage
from llama_index.llms.bedrock_converse import BedrockConverse
from rapidfuzz import fuzz, process as fuzz_process

from app.config import config
from app.services.llm_cost_tracker import (
    JobLlmUsageAccumulator,
    LLMCallMetrics,
    extract_usage,
    log_llm_call,
    log_llm_fallback,
)
from app.services.llm_model_config import resolve_max_tokens, resolve_step_model

logger = logging.getLogger(__name__)

_prompt_client = boto3.client("bedrock-agent", region_name=config.aws.region)

# Cache de instâncias BedrockConverse por (model_id, max_tokens).
_llm_cache: dict[tuple[str, int], BedrockConverse] = {}
_llm_cache_lock = threading.Lock()


def _get_llm(model: str, max_tokens: int) -> BedrockConverse:
    """Retorna BedrockConverse cacheado para o modelo e max_tokens."""
    key = (model, max_tokens)
    if key not in _llm_cache:
        with _llm_cache_lock:
            if key not in _llm_cache:
                _llm_cache[key] = BedrockConverse(
                    model=model,
                    max_tokens=max_tokens,
                    region_name=config.aws.region,
                )
    return _llm_cache[key]


def _fetch_prompt(prompt_id: str | None, prompt_version: str | None) -> str:
    """Busca o texto de um prompt do Bedrock Agent.

    Args:
        prompt_id: Identificador do prompt no Bedrock.
        prompt_version: Versão do prompt.

    Returns:
        Texto do prompt.
    """
    return _prompt_client.get_prompt(
        promptIdentifier=prompt_id,
        promptVersion=prompt_version,
    )["variants"][0]["templateConfiguration"]["text"]["text"]


def _record_call(
    step: str,
    model: str,
    response,
    duration_s: float,
    usage_acc: JobLlmUsageAccumulator | None,
    *,
    fallback: bool = False,
    fallback_reason: str | None = None,
    original_model: str | None = None,
) -> LLMCallMetrics:
    input_tokens, output_tokens = extract_usage(response)
    metrics = LLMCallMetrics(
        step=step,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_s=duration_s,
        fallback=fallback,
        fallback_reason=fallback_reason,
        original_model=original_model,
    )
    log_llm_call(metrics)
    if usage_acc is not None:
        usage_acc.record(metrics)
    return metrics


# ---------------------------------------------------------------------------
# Normalização de instalações/usinas contra a lista válida de instalações
# ---------------------------------------------------------------------------

_s3_client = boto3.client("s3", region_name=config.aws.region)

_INSTALLATIONS_CACHE: dict = {"data": None, "loaded_at": 0.0}
_INSTALLATIONS_CACHE_TTL = 3600  # 1 hora

_GRAVADOR_UF_MAP = {
    "RIO": "RJ", "REC": "PE", "FLN": "SC", "BSB": "DF",
    "Rio de Janeiro": "RJ", "Recife": "PE", "Florianópolis": "SC", "Brasília": "DF",
}


def _extract_json_text(s: str) -> str:
    """Extrai conteúdo JSON removendo cercas markdown de respostas LLM.

    Aceita ```, ```json, ```python e outras tags de linguagem. Texto sem cerca
    (caminho feliz: JSON puro) permanece inalterado.
    """
    s = (s or "").strip()

    match = re.search(r"```[a-zA-Z0-9_+-]*\s*", s)
    if not match:
        return s

    content = s[match.end() :]

    last_fence = content.rfind("```")
    if last_fence != -1:
        content = content[:last_fence]

    return content.strip()


def _parse_llm_structured(raw: str):
    """Parseia JSON estruturado do LLM com fallback para literal Python.

    Alguns modelos (ex.: Llama Maverick) devolvem dict/list com aspas simples
    (`{'resumo': '...'}`). `json.loads` rejeita; `ast.literal_eval` aceita.
    """
    text = _extract_json_text(raw)
    # Prefixo ocasional do adapter / logs: "assistant: {...}"
    if text.lower().startswith("assistant:"):
        text = text.split(":", 1)[1].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_exc:
        try:
            value = ast.literal_eval(text)
        except (SyntaxError, ValueError) as lit_exc:
            raise first_exc from lit_exc
        if not isinstance(value, (dict, list)):
            raise first_exc
        return value


def _normalize_nomelongo(value: str) -> str:
    """Colapsa espaços internos e remove espaços nas extremidades."""
    return re.sub(r'\s+', ' ', value).strip()


def _strip_accents_upper(value: str) -> str:
    """Remove acentos e coloca em maiúsculas (a lista de instalações é sem acentos)."""
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return s.upper()


def get_installations_list() -> list[dict]:
    """Carrega lista de instalações válidas do S3 com cache in-memory de 1h."""
    now = time.time()
    if _INSTALLATIONS_CACHE["data"] is not None and (now - _INSTALLATIONS_CACHE["loaded_at"]) < _INSTALLATIONS_CACHE_TTL:
        return _INSTALLATIONS_CACHE["data"]

    bucket = config.aws.s3_bucket
    key = config.installations.list_s3_key

    if not bucket or not key:
        logger.warning(
            "TRANSCRIBE_S3_NAME ou INSTALLATIONS_LIST_S3_KEY não configurados. "
            "Normalização de instalações desativada."
        )
        return []

    try:
        response = _s3_client.get_object(Bucket=bucket, Key=key)
        installations = json.loads(response["Body"].read().decode("utf-8"))
        _INSTALLATIONS_CACHE["data"] = installations
        _INSTALLATIONS_CACHE["loaded_at"] = now
        logger.info("Lista de instalações carregada: %d registros.", len(installations))
        return installations
    except Exception as exc:
        logger.error("Erro ao carregar lista de instalações do S3: %s", exc)
        return []


def fuzzy_match_installation(
    mention: str, installations: list[dict], threshold_low: int = 60, limit: int = 5
) -> list[dict]:
    """Retorna até `limit` instalações com score >= threshold_low, ordenadas por score desc.

    Cada item retornado inclui todos os campos do registro mais o score (`_score`).
    """
    if not installations:
        return []

    names = [_strip_accents_upper(inst["nomelongo"]) for inst in installations]
    results = fuzz_process.extract(
        _strip_accents_upper(mention),
        names,
        scorer=fuzz.token_set_ratio,
        score_cutoff=threshold_low,
        limit=limit,
    )

    candidates = []
    for _matched_name, score, idx in results:
        entry = dict(installations[idx])
        entry["_score"] = score
        candidates.append(entry)

    candidates.sort(key=lambda x: x["_score"], reverse=True)
    return candidates


def disambiguate_with_claude(
    mention: str,
    candidates: list[dict],
    context_clues: dict,
    transcript_chunks: list[dict],
    gravador: str = "",
) -> str | None:
    """Chama o LLM (Bedrock) com o prompt de desambiguação para escolher entre candidatos."""
    try:
        disambiguation_prompt = _fetch_prompt(
            config.prompts.installation_disambiguation_id,
            config.prompts.installation_disambiguation_version,
        )

        gravador_uf = _GRAVADOR_UF_MAP.get(gravador, "")

        user_content = json.dumps({
            "mencao_original": mention,
            "tema": context_clues.get("tema", "null"),
            "lt_detectada": context_clues.get("lt_detectada", False),
            "gravador_uf": gravador_uf,
            "uf_hint": context_clues.get("uf_hint"),
            "candidatos": [
                {k: v for k, v in c.items() if k != "_score"}
                for c in candidates
            ],
            "trecho_transcricao": [s.get("text", "") for s in transcript_chunks[:6]],
        }, ensure_ascii=False)

        messages = [
            ChatMessage(role="system", content=disambiguation_prompt),
            ChatMessage(role="user", content=user_content),
        ]
        # Desambiguação usa o modelo de entities (mesma complexidade de JSON estruturado).
        model = resolve_step_model("entities")
        max_tokens = resolve_max_tokens("entities")
        response = _get_llm(model, max_tokens).chat(messages)
        raw = response if isinstance(response, str) else response.message.content
        result = _parse_llm_structured(raw)
        selected = result.get("nomelongo_selecionado")
        if selected and selected.lower() != "null":
            return _normalize_nomelongo(selected)
        return None
    except Exception as exc:
        logger.warning("Erro na desambiguação para '%s': %s", mention, exc)
        return None


def normalize_installations(
    raw_mentions: list[str],
    context_clues: dict,
    transcript_chunks: list[dict],
    gravador: str = "",
) -> list[str]:
    """Normaliza as menções brutas de instalação contra a lista válida de instalações.

    Retorna lista de nomelongo válidos e normalizados (pode ser vazia).
    """
    threshold_high = config.installations.match_threshold_high
    threshold_low = config.installations.match_threshold_low

    installations = get_installations_list()
    if not installations:
        return []

    # Para LTs, usa os terminais extraídos em vez das menções brutas
    if context_clues.get("lt_detectada") and context_clues.get("lt_terminais"):
        mentions_to_process = context_clues["lt_terminais"]
    else:
        mentions_to_process = raw_mentions

    normalized = []
    for mention in mentions_to_process:
        if not mention or mention.lower() == "null":
            continue

        candidates = fuzzy_match_installation(mention, installations, threshold_low=threshold_low)

        if not candidates:
            logger.info("Nenhum candidato encontrado para '%s' (abaixo do threshold). Campo em branco.", mention)
            continue

        top_score = candidates[0]["_score"]

        if top_score >= threshold_high and (len(candidates) == 1 or candidates[1]["_score"] < threshold_high - 5):
            selected = _normalize_nomelongo(candidates[0]["nomelongo"])
            logger.info("'%s' → '%s' (score=%s, auto-aceito)", mention, selected, top_score)
            normalized.append(selected)
        else:
            selected = disambiguate_with_claude(mention, candidates, context_clues, transcript_chunks, gravador)
            if selected:
                logger.info("'%s' → '%s' (via LLM)", mention, selected)
                normalized.append(selected)
            else:
                logger.info("'%s' → campo em branco (LLM não identificou com confiança)", mention)

    # Remove duplicatas mantendo ordem
    seen = set()
    result = []
    for item in normalized:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def get_prompt(transcricao: str, filename: str) -> str:
    """Monta o prompt de beautify com a transcrição e nome do arquivo.

    Args:
        transcricao: Texto da transcrição.
        filename: Nome do arquivo de áudio.

    Returns:
        Prompt completo pronto para envio ao LLM.
    """
    prompt_text = _fetch_prompt(config.prompts.beautify_id, config.prompts.beautify_version)
    return f"{prompt_text}: \nAudio Name: {filename}\n{transcricao}"


def run_beautify(
    prompt: str,
    usage_acc: JobLlmUsageAccumulator | None = None,
    is_retry: bool = False,
) -> str:
    """Envia a transcrição ao LLM para correção e formatação.

    Args:
        prompt: Prompt com a transcrição a ser corrigida.
        usage_acc: Acumulador de uso LLM do job.
        is_retry: True se esta é a segunda tentativa.

    Returns:
        Transcrição corrigida pelo LLM.
    """
    model = resolve_step_model("beautify")
    max_tokens = resolve_max_tokens("beautify")
    system_prompt = _fetch_prompt(config.prompts.beautify_system_id, config.prompts.beautify_system_version)
    glossary = _fetch_prompt(config.prompts.glossary_id, config.prompts.glossary_version)

    messages = [
        ChatMessage(role="system", content=system_prompt + f"\nGlossário técnico: {glossary}"),
        ChatMessage(role="user", content=prompt),
    ]
    t0 = time.perf_counter()
    response = _get_llm(model, max_tokens).chat(messages)
    duration_s = time.perf_counter() - t0
    logger.info("[run_beautify] model=%s raw response: %s", model, response)
    _record_call(
        "beautify",
        model,
        response,
        duration_s,
        usage_acc,
        fallback=is_retry,
        fallback_reason="retry_after_exception" if is_retry else None,
        original_model=model if is_retry else None,
    )
    return response if isinstance(response, str) else response.message.content


def run_pipeline_beautify(
    transcricao: str,
    filename: str,
    usage_acc: JobLlmUsageAccumulator | None = None,
) -> str:
    """Executa o pipeline de beautify com retry em caso de falha.

    Args:
        transcricao: Texto da transcrição com marcadores %$%.
        filename: Nome do arquivo de áudio.
        usage_acc: Acumulador de uso LLM do job.

    Returns:
        Transcrição corrigida ou o texto original se exceder o limite de tokens.
    """
    def _try_beautify(is_retry: bool = False) -> str:
        prompt = get_prompt(transcricao, filename)
        n_tokens = len(prompt.split())
        logger.info("Tokens estimados para beautify: %d", n_tokens)
        if n_tokens > config.transcription.max_prompt_tokens:
            return transcricao
        return run_beautify(prompt, usage_acc=usage_acc, is_retry=is_retry)

    try:
        return _try_beautify()
    except Exception as exc:
        log_llm_fallback(
            step="beautify",
            original_model=resolve_step_model("beautify"),
            fallback_model=resolve_step_model("beautify"),
            reason=str(exc),
        )
        logger.warning("Falha no primeiro beautify; aguardando 5s e tentando novamente.")
        time.sleep(5)
        return _try_beautify(is_retry=True)


def _apply_installation_normalization(
    key_entities,
    prompt: str | list,
    gravador: str = "",
):
    """Normaliza instalacao_ou_usina_envolvida se key_entities for dict."""
    if not isinstance(key_entities, dict):
        return key_entities
    raw_mentions = key_entities.get("instalacao_ou_usina_envolvida", [])
    if isinstance(raw_mentions, str):
        raw_mentions = [raw_mentions] if raw_mentions and raw_mentions.lower() != "null" else []
    context_clues = key_entities.pop("_instalacao_contexto", {}) or {}
    transcript_chunks = prompt if isinstance(prompt, list) else []
    normalized = normalize_installations(raw_mentions, context_clues, transcript_chunks, gravador)
    key_entities["instalacao_ou_usina_envolvida"] = normalized
    return key_entities


def get_key_entities(
    prompt: str | list,
    gravador: str = "",
    usage_acc: JobLlmUsageAccumulator | None = None,
) -> dict | str:
    """Extrai entidades-chave da transcrição usando o LLM.

    Após a extração, normaliza o campo `instalacao_ou_usina_envolvida` contra a
    lista válida de instalações (fuzzy match + desambiguação via LLM). Em falha de
    JSON e se o modelo fallback for distinto, retenta com o modelo robusto.

    Args:
        prompt: Transcrição ou lista de segmentos a analisar.
        gravador: Identificação do gravador (sinal geográfico para desambiguação).
        usage_acc: Acumulador de uso LLM do job.

    Returns:
        Dicionário de entidades ou string de fallback em caso de erro.
    """
    model = resolve_step_model("entities")
    max_tokens = resolve_max_tokens("entities")
    try:
        logger.info("Extraindo entidades chave (model=%s)...", model)
        system_prompt = _fetch_prompt(
            config.prompts.key_entities_id, config.prompts.key_entities_version
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=str(prompt)),
        ]
        t0 = time.perf_counter()
        response = _get_llm(model, max_tokens).chat(messages)
        duration_s = time.perf_counter() - t0
        logger.info("[get_key_entities] raw response: %s", response)
        raw = response if isinstance(response, str) else response.message.content

        try:
            key_entities = _parse_llm_structured(raw)
        except json.JSONDecodeError as exc:
            fallback_model = resolve_step_model("fallback")
            if fallback_model != model:
                log_llm_fallback(
                    step="entities",
                    original_model=model,
                    fallback_model=fallback_model,
                    reason="json_parse_failure",
                )
                return _get_key_entities_with_fallback(
                    messages,
                    model,
                    fallback_model,
                    max_tokens,
                    duration_s,
                    usage_acc,
                    prompt,
                    gravador,
                    response,
                )
            logger.warning(
                "Retorno de key_entities não é JSON válido; retornando string bruta. Erro: %s",
                exc,
            )
            _record_call("entities", model, response, duration_s, usage_acc)
            return raw

        _record_call("entities", model, response, duration_s, usage_acc)
        return _apply_installation_normalization(key_entities, prompt, gravador)

    except Exception as exc:
        return f"Não foi possível extraír as entidades-chave da transcrição. Motivo: {exc}"


def _get_key_entities_with_fallback(
    messages: list,
    original_model: str,
    fallback_model: str,
    max_tokens: int,
    primary_duration_s: float,
    usage_acc: JobLlmUsageAccumulator | None,
    prompt: str | list,
    gravador: str,
    primary_response,
) -> dict | str:
    """Retenta entities com modelo fallback e soma tokens das duas tentativas."""
    try:
        t0 = time.perf_counter()
        response = _get_llm(fallback_model, max_tokens).chat(messages)
        duration_s = time.perf_counter() - t0
        p_in, p_out = extract_usage(primary_response)
        f_in, f_out = extract_usage(response)
        metrics = LLMCallMetrics(
            step="entities",
            model=fallback_model,
            input_tokens=p_in + f_in,
            output_tokens=p_out + f_out,
            duration_s=primary_duration_s + duration_s,
            fallback=True,
            fallback_reason="json_parse_failure",
            original_model=original_model,
        )
        log_llm_call(metrics)
        if usage_acc is not None:
            usage_acc.record(metrics)
        raw = response if isinstance(response, str) else response.message.content
        try:
            result = _parse_llm_structured(raw)
        except json.JSONDecodeError:
            return raw
        return _apply_installation_normalization(result, prompt, gravador)
    except Exception as exc:
        return f"Não foi possível extraír as entidades-chave da transcrição. Motivo: {exc}"


def get_audio_subject(
    prompt: str | list,
    usage_acc: JobLlmUsageAccumulator | None = None,
) -> list | str:
    """Identifica os assuntos do áudio usando o LLM.

    Args:
        prompt: Transcrição ou lista de segmentos a analisar.
        usage_acc: Acumulador de uso LLM do job.

    Returns:
        Lista de assuntos identificados ou string de fallback.
    """
    model = resolve_step_model("subject")
    max_tokens = resolve_max_tokens("subject")
    try:
        logger.info("Identificando assunto do áudio (model=%s)...", model)
        system_prompt = _fetch_prompt(
            config.prompts.subject_id, config.prompts.subject_version
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=str(prompt)),
        ]
        t0 = time.perf_counter()
        response = _get_llm(model, max_tokens).chat(messages)
        duration_s = time.perf_counter() - t0
        logger.info("[get_audio_subject] raw response: %s", response)
        _record_call("subject", model, response, duration_s, usage_acc)
        raw = response if isinstance(response, str) else response.message.content

        try:
            return ast.literal_eval(raw)
        except Exception:
            return [raw]

    except Exception as exc:
        return f"Não foi possível identificar o assunto do áudio. Motivo: {exc}"


def generate_summary(
    prompt: str | list,
    usage_acc: JobLlmUsageAccumulator | None = None,
) -> dict | str:
    """Gera um resumo estruturado da transcrição usando o LLM.

    Em falha de parse JSON, re-tenta uma vez no mesmo modelo; se ainda falhar e
    o fallback for distinto, tenta o modelo robusto.

    Args:
        prompt: Transcrição ou lista de segmentos a resumir.
        usage_acc: Acumulador de uso LLM do job.

    Returns:
        Dicionário com o resumo ou string de erro legível se o parse falhar.
    """
    model = resolve_step_model("summary")
    max_tokens = resolve_max_tokens("summary")
    try:
        logger.info("Gerando resumo (model=%s)...", model)
        system_prompt = _fetch_prompt(
            config.prompts.summary_id, config.prompts.summary_version
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=str(prompt)),
        ]

        last_error: json.JSONDecodeError | None = None
        last_response = None
        last_duration = 0.0
        for attempt in range(1, 3):
            t0 = time.perf_counter()
            response = _get_llm(model, max_tokens).chat(messages)
            last_duration = time.perf_counter() - t0
            last_response = response
            logger.info(
                "[generate_summary] raw response (tentativa %s/2): %s", attempt, response
            )
            raw = response if isinstance(response, str) else response.message.content
            try:
                result = _parse_llm_structured(raw)
                _record_call("summary", model, response, last_duration, usage_acc)
                return result
            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "Retorno de summary não é JSON válido (tentativa %s/2). "
                    "Erro: %s; trecho: %r",
                    attempt,
                    exc,
                    (raw or "")[:120],
                )

        fallback_model = resolve_step_model("fallback")
        if fallback_model != model and last_response is not None:
            log_llm_fallback(
                step="summary",
                original_model=model,
                fallback_model=fallback_model,
                reason="json_parse_failure",
            )
            return _generate_summary_with_fallback(
                messages,
                model,
                fallback_model,
                max_tokens,
                last_duration,
                usage_acc,
                last_response,
            )

        if last_response is not None:
            _record_call("summary", model, last_response, last_duration, usage_acc)

        return (
            "Não foi possível gerar o resumo da transcrição. "
            f"Motivo: retorno do LLM não é JSON válido ({last_error})."
        )

    except Exception as exc:
        return f"Não foi possível gerar o resumo da transcrição. Motivo: {exc}"


def _generate_summary_with_fallback(
    messages: list,
    original_model: str,
    fallback_model: str,
    max_tokens: int,
    primary_duration_s: float,
    usage_acc: JobLlmUsageAccumulator | None,
    primary_response,
) -> dict | str:
    """Retenta summary com modelo fallback."""
    try:
        t0 = time.perf_counter()
        response = _get_llm(fallback_model, max_tokens).chat(messages)
        duration_s = time.perf_counter() - t0
        p_in, p_out = extract_usage(primary_response)
        f_in, f_out = extract_usage(response)
        metrics = LLMCallMetrics(
            step="summary",
            model=fallback_model,
            input_tokens=p_in + f_in,
            output_tokens=p_out + f_out,
            duration_s=primary_duration_s + duration_s,
            fallback=True,
            fallback_reason="json_parse_failure",
            original_model=original_model,
        )
        log_llm_call(metrics)
        if usage_acc is not None:
            usage_acc.record(metrics)
        raw = response if isinstance(response, str) else response.message.content
        try:
            return _parse_llm_structured(raw)
        except json.JSONDecodeError as exc:
            return (
                "Não foi possível gerar o resumo da transcrição. "
                f"Motivo: retorno do LLM não é JSON válido ({exc})."
            )
    except Exception as exc:
        return f"Não foi possível gerar o resumo da transcrição. Motivo: {exc}"
