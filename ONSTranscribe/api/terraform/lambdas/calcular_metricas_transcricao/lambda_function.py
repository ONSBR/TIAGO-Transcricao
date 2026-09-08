"""
Lambda para calcular métricas de qualidade de transcrição e metadados comparando com groundtruths no S3.

Operação em duas fases para suportar 215+ áudios dentro do limite de 15 min do Lambda:

  Fase "submit" (padrão, acionada externamente):
    1. Lista groundtruths e localiza áudios no S3
    2. Submete todos os jobs à API ONSTranscribe em paralelo (sem aguardar conclusão)
    3. Salva manifesto de execução em S3
    4. Invoca assincronamente a fase "aggregate"

  Fase "aggregate" (invocada pela fase submit):
    1. Carrega manifesto do S3
    2. Carrega planilha de groundtruth de metadados do S3 (xlsx)
    3. Aguarda cada transcrição verificando a existência do arquivo no S3 (sem polling da API)
    4. Calcula WER, MER, WIL, WIP, CER e métricas de metadados (P/R/F1 por campo) em paralelo
    5. Se o JSON da API trouxer llm_usage_metrics (ou legado llm_cost_metrics), estima
       custo USD via catálogo em llm_pricing.py (SSOT de preço; não vive no app)
    6. Salva JSON completo versionado e NDJSON particionado para Athena

Variáveis de ambiente obrigatórias:
  BUCKET_NAME                   - Bucket S3 de dados/métricas (groundtruths, transcrições, métricas)
  AUDIO_BUCKET_NAME             - Bucket S3 de origem dos áudios de qualidade (AUDIO_KEY_PATHS).
                                  Se ausente, usa BUCKET_NAME (compatibilidade).
  ONS_TRANS_API_URL             - URL base da API ONSTranscribe (ex: http://alb-dns)
  TRANSCRIPTIONS_KEY_PATH       - Prefixo S3 para transcrições geradas
  GROUNDTRUTH_KEY_PATH          - Prefixo S3 para groundtruths de transcrição (.txt)
  AUDIO_KEY_PATHS               - Prefixos de áudio separados por vírgula (ex.: LOC1/, LOC2/, LOC3/)
  METRICS_S3_PREFIX             - Prefixo S3 para JSONs completos de métricas versionados
  ATHENA_S3_PREFIX              - Prefixo S3 para dados particionados do Athena
  ATHENA_DATABASE               - Nome do banco Glue/Athena
  MODEL_VERSION                 - Versão do modelo (fallback; preferível passar no evento)
  TEST_ID                       - Identificador do teste (fallback; preferível passar no evento)

Variáveis de ambiente opcionais:
  METADATA_GROUNDTRUTH_KEY_PATH - Prefixo S3 do xlsx de groundtruth de metadados
                                  (ex: testes/base-verdade/metadados). Se ausente ou vazio,
                                  as métricas de metadados são omitidas sem erro.
  MAX_AUDIO_COUNT               - Limite de áudios processados por execução (padrão: 200).
                                  No modo integrado: amostragem estratificada da interseção GT.
                                  No modo independente: tamanho do Set B de metadados.
  PROCESSING_MODE               - Modo de processamento (padrão: integrated).
                                  integrated: métricas de metadados calculadas para todos os áudios com GT de
                                             transcrição E GT de metadados (interseção completa, sem amostragem).
                                             Se a interseção for vazia, faz fallback automático para 'independent'
                                             (motivo e modo efetivo registrados nos logs).
                                  independent: Set A (transcrição, GT = arquivo .txt) e Set B (amostra estratificada
                                             de metadados) processados juntos. No Set B a coluna 'transcription' da
                                             planilha é usada como GT de transcrição (WER/CER etc.). Registros do
                                             Set B são descartados quando a transcrição está vazia, é "Sem áudio",
                                             ou todas as colunas de metadados estão vazias.

Payload do evento:
  Fase submit:    { "model_version": "whisper-v2.1", "test_id": "sprint-16" }
  Fase aggregate: { "step": "aggregate", "run_id": "...", "manifest_key": "..." }

Métricas de metadados calculadas (campos do JSON key_entities vs groundtruth da planilha):
  Coluna xlsx            → Campo JSON
  nomeoperadorONS        → nome_do_operador_ONS
  nomeoperadorag         → nome_do_operador_do_agente
  nomeag                 → nome_do_agente
  instenvolv             → instalacao_ou_usina_envolvida
  equienvolv             → equipamento_envolvido
  nsgi                   → numero_do_SGI
  Assunto tratado        → assunto_do_audio  (comparação por conjunto)
"""
import io
import json
import logging
import os
import random
import re
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from jiwer import cer as calc_cer, mer as calc_mer, wer as calc_wer, wil as calc_wil, wip as calc_wip

from llm_pricing import enrich_usage_with_cost, extract_llm_usage, flat_llm_cost_fields

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_TABLE_POR_AUDIO = "metricas_por_audio"
_TABLE_AGREGADAS = "metricas_agregadas"
_SUBMIT_WORKERS = 3        # API suporta baixa concorrência; mais workers causam ReadTimeout
_SUBMIT_TIMEOUT_S = 60     # margem para o ACK quando a API está ocupada transcrevendo
_SUBMIT_MAX_RETRIES = 1    # tentativas extras em caso de erro retryable (backoff de 5s)
_SUBMIT_RESERVE_TIME_S = 60  # segundos reservados no budget para salvar manifesto e invocar aggregate
_AGGREGATE_WORKERS = 20
_S3_POLL_INTERVAL_S = 15
_S3_POLL_MAX_ATTEMPTS = 28  # 28 * 15s = 7 min máximo por áudio
_MAX_AUDIO_COUNT_DEFAULT = 200
_WER_MATCH_THRESHOLD = 0.3  # limiar WER/CER para matching aproximado de campos de metadados (variação ortográfica)

# Coluna da transcrição de groundtruth na planilha de metadados.
_TRANSCRIPTION_GT_COLUMN = "transcription"

# Mapeamento: coluna xlsx → campo em key_entities do JSON de transcrição
_METADATA_FIELD_MAP: List[Tuple[str, str]] = [
    ("nomeoperadorONS",  "nome_do_operador_ONS"),
    ("nomeoperadorag",   "nome_do_operador_do_agente"),
    ("nomeag",           "nome_do_agente"),
    ("instenvolv",       "instalacao_ou_usina_envolvida"),
    ("equienvolv",       "equipamento_envolvido"),
    ("nsgi",             "numero_do_SGI"),
    ("Assunto tratado",  "assunto_do_audio"),
]

_METADATA_LIST_FIELDS = {"assunto_do_audio"}

# Mapeamento: campo JSON → prefixo de coluna nos registros S3/Athena
_METADATA_COL_MAP: Dict[str, str] = {
    "nome_do_operador_ONS":          "nome_oper_ons",
    "nome_do_operador_do_agente":    "nome_oper_agente",
    "nome_do_agente":                "nome_agente",
    "instalacao_ou_usina_envolvida": "instalacao",
    "equipamento_envolvido":         "equipamento",
    "numero_do_SGI":                 "nsgi",
    "assunto_do_audio":              "assunto",
}


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Variável de ambiente obrigatória não definida: {name}")
    return value


def _normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^\w\s]", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = " ".join(w for w in texto.split() if len(w) > 1)
    return texto


def _sanitizar_assuntos(assuntos_raw: list) -> List[str]:
    """Remove markdown code blocks que o LLM pode embrulhar nos assuntos retornados."""
    resultado = []
    for item in assuntos_raw:
        if not isinstance(item, str):
            continue
        item_limpo = item.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", item_limpo)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, list):
                    resultado.extend(str(s) for s in parsed if s)
                else:
                    resultado.append(str(parsed))
            except json.JSONDecodeError:
                resultado.append(item_limpo)
        else:
            resultado.append(item_limpo)
    return resultado


def _listar_groundtruths(s3, bucket: str, prefix: str) -> List[str]:
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".txt"):
                keys.append(key)
    return keys


def _indexar_audios(s3, bucket: str, audio_prefixes: List[str]) -> dict:
    """Lista todos os prefixos de áudio em paralelo e retorna um índice {stem: key}."""
    indice: dict = {}

    def _listar_prefix(prefix: str) -> dict:
        paginator = s3.get_paginator("list_objects_v2")
        resultado: dict = {}
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                resultado[Path(obj["Key"]).stem] = obj["Key"]
        return resultado

    with ThreadPoolExecutor(max_workers=len(audio_prefixes)) as pool:
        for resultado in as_completed(pool.submit(_listar_prefix, p) for p in audio_prefixes):
            indice.update(resultado.result())

    return indice


def _baixar_texto_groundtruth(s3, bucket: str, gt_key: str) -> str:
    response = s3.get_object(Bucket=bucket, Key=gt_key)
    texto = response["Body"].read().decode("utf-8").strip()
    texto = texto.replace("#$#", " ")
    return re.sub(r"\s+", " ", texto).strip()


def _submeter_job(api_base_url: str, audio_bucket: str, dest_bucket: str,
                  audio_key: str, transcricao_key: str) -> str:
    """Submete um job à API e retorna o job_id. Não aguarda conclusão. Tenta até _SUBMIT_MAX_RETRIES+1 vezes.

    O áudio de origem vem de audio_bucket; a transcrição é gravada em dest_bucket.
    """
    payload = {
        "audio_source_link": f"https://{audio_bucket}.s3.amazonaws.com/{audio_key}",
        "destination_link": f"https://{dest_bucket}.s3.amazonaws.com/{transcricao_key}",
        "language": "pt",
    }
    url = f"{api_base_url}/api/v1/transcribe/"
    last_exc = None
    for tentativa in range(_SUBMIT_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=_SUBMIT_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json().get("job_id")
        except (requests.exceptions.Timeout, requests.exceptions.HTTPError) as exc:
            is_retryable = isinstance(exc, requests.exceptions.Timeout) or (
                isinstance(exc, requests.exceptions.HTTPError)
                and exc.response is not None
                and exc.response.status_code in (502, 503, 504)
            )
            if not is_retryable:
                raise
            last_exc = exc
            if tentativa < _SUBMIT_MAX_RETRIES:
                wait = 5 * (tentativa + 1)
                logger.warning("Erro retryable tentativa %d/%d para %s (%s). Aguardando %ds...",
                               tentativa + 1, _SUBMIT_MAX_RETRIES + 1, audio_key, exc, wait)
                time.sleep(wait)
    raise last_exc


def _aguardar_transcricao_no_s3(s3, bucket: str, transcricao_key: str) -> bool:
    """Aguarda a transcrição aparecer no S3. Retorna True se encontrada dentro do timeout."""
    for _ in range(_S3_POLL_MAX_ATTEMPTS):
        try:
            s3.head_object(Bucket=bucket, Key=transcricao_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                time.sleep(_S3_POLL_INTERVAL_S)
            else:
                raise
    return False


def _carregar_transcricao(s3, bucket: str, key: str) -> dict:
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def _extrair_texto(transcricao: dict) -> str:
    speakers = transcricao.get("transcricaoAudio", {}).get("speakers", [])
    return " ".join(s.get("text", "") for s in speakers)


def _extrair_assuntos(transcricao: dict) -> List[str]:
    key_entities = transcricao.get("transcricaoAudio", {}).get("key_entities", {})
    if isinstance(key_entities, dict):
        assuntos = key_entities.get("assunto_do_audio", [])
        if isinstance(assuntos, list):
            return _sanitizar_assuntos(assuntos)
    return []


def _calcular_metricas_texto(referencia: str, hipotese: str) -> dict:
    ref_norm = _normalizar_texto(referencia)
    hip_norm = _normalizar_texto(hipotese)
    if not ref_norm or not hip_norm:
        return {k: None for k in ["wer", "mer", "wil", "wip", "cer"]}
    return {
        "wer": round(calc_wer(ref_norm, hip_norm), 4),
        "mer": round(calc_mer(ref_norm, hip_norm), 4),
        "wil": round(calc_wil(ref_norm, hip_norm), 4),
        "wip": round(calc_wip(ref_norm, hip_norm), 4),
        "cer": round(calc_cer(ref_norm, hip_norm), 4),
    }


def _media(registros: list, campo: str) -> Optional[float]:
    vals = [r[campo] for r in registros if r.get(campo) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


# ---------------------------------------------------------------------------
# Métricas de metadados
# ---------------------------------------------------------------------------

def _carregar_gt_metadados(s3, bucket: str, prefix: str) -> Dict[str, dict]:
    """Carrega planilha xlsx de groundtruth de metadados do S3.

    Retorna {audio_stem: {json_key: valor}} usando _METADATA_FIELD_MAP.
    Retorna {} se o prefix estiver vazio, nenhum xlsx for encontrado,
    ou openpyxl não estiver disponível na camada Lambda.
    """
    if not prefix:
        return {}
    try:
        import openpyxl  # disponível via metricas_layer
    except ImportError:
        logger.warning("openpyxl não disponível na camada — métricas de metadados desabilitadas.")
        return {}

    paginator = s3.get_paginator("list_objects_v2")
    xlsx_key = None
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []):
            if obj["Key"].lower().endswith(".xlsx"):
                xlsx_key = obj["Key"]
                break
        if xlsx_key:
            break

    if not xlsx_key:
        logger.warning("Nenhum xlsx encontrado em s3://%s/%s — métricas de metadados desabilitadas.", bucket, prefix)
        return {}

    response = s3.get_object(Bucket=bucket, Key=xlsx_key)
    wb = openpyxl.load_workbook(io.BytesIO(response["Body"].read()), data_only=True)
    ws = wb.active

    headers = [cell.value for cell in ws[1]]
    gt_map: Dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        row_dict = dict(zip(headers, row))
        audio_val = row_dict.get("audio")
        if not audio_val:
            continue
        audio_stem = Path(str(audio_val)).stem
        if "-" in audio_stem:
            audio_stem = audio_stem.split("-", 1)[1]
        nomeag_val = row_dict.get("nomeag")
        transc_val = row_dict.get(_TRANSCRIPTION_GT_COLUMN)
        entry = {json_key: row_dict.get(xlsx_col) for xlsx_col, json_key in _METADATA_FIELD_MAP}
        entry["_nomeag"] = str(nomeag_val).strip() if nomeag_val else None
        entry["_transcription"] = str(transc_val).strip() if transc_val is not None else None
        gt_map[audio_stem] = entry

    logger.info("GT metadados carregado: %d registros de s3://%s/%s", len(gt_map), bucket, xlsx_key)
    return gt_map



def _selecionar_amostra_estratificada(stems_nomeag: Dict[str, Optional[str]], max_count: int) -> set:
    """Seleciona até max_count stems por amostragem aleatória estratificada por nomeag."""
    grupos: Dict[str, list] = defaultdict(list)
    for stem, nomeag in stems_nomeag.items():
        grupos[nomeag or ""].append(stem)

    total = sum(len(v) for v in grupos.values())
    if total == 0:
        return set()

    selecionados: List[str] = []
    for grupo_stems in grupos.values():
        cota = max(1, round(len(grupo_stems) / total * max_count))
        selecionados.extend(random.sample(grupo_stems, min(cota, len(grupo_stems))))

    if len(selecionados) > max_count:
        random.shuffle(selecionados)
        selecionados = selecionados[:max_count]

    logger.info(
        "Amostra estratificada: %d/%d stems selecionados de %d grupos (nomeag).",
        len(selecionados), total, len(grupos),
    )
    return set(selecionados)


def _registro_set_b_valido(entry: dict) -> bool:
    """Indica se um registro da planilha de metadados é elegível para o Set B (modo independent).

    Descarta (retorna False) quando:
      - a transcrição de GT (_transcription) está vazia;
      - a transcrição de GT é "Sem áudio" (normalizada → "sem audio");
      - todas as colunas de metadados (_METADATA_FIELD_MAP) estão vazias.
    """
    transc_norm = _normalizar_texto(str(entry.get("_transcription") or ""))
    if not transc_norm or transc_norm == "sem audio":
        return False
    tem_metadado = any(
        entry.get(json_key) is not None and str(entry.get(json_key)).strip()
        for _, json_key in _METADATA_FIELD_MAP
    )
    return tem_metadado


def _extrair_metadados_predicao(transcricao: dict) -> dict:
    """Extrai key_entities completo do JSON de transcrição."""
    return transcricao.get("transcricaoAudio", {}).get("key_entities", {}) or {}


def _prf_campo_simples(
    ref_val, pred_val
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Precision/Recall/F1 para campo de valor único com matching fuzzy WER/CER.

    Para tokens únicos usa CER (nível de caractere) para capturar variações
    ortográficas de nomes próprios (1 caractere divergente em 5 → CER=0.20 ≤ limiar).
    Para campos multi-palavra usa WER (nível de palavra). Match positivo quando error_rate ≤ _WER_MATCH_THRESHOLD.

    Casos especiais:
      - ref=vazio, pred=vazio → (None, None, None) — sem avaliação possível
      - ref=X,    pred=vazio  → (None, 0.0, 0.0)   — não detectado
      - ref=vazio, pred=X     → (0.0, None, 0.0)   — falso positivo
    """
    ref_norm = _normalizar_texto(str(ref_val)) if ref_val is not None else ""
    pred_norm = _normalizar_texto(str(pred_val)) if pred_val is not None else ""
    if not ref_norm and not pred_norm:
        return None, None, None
    if not ref_norm:
        return 0.0, None, 0.0
    if not pred_norm:
        return None, 0.0, 0.0
    if ref_norm == pred_norm:
        return 1.0, 1.0, 1.0
    error_rate = (
        calc_cer(ref_norm, pred_norm)
        if len(ref_norm.split()) == 1
        else calc_wer(ref_norm, pred_norm)
    )
    match = 1.0 if error_rate <= _WER_MATCH_THRESHOLD else 0.0
    return match, match, match


def _prf_campo_lista(
    ref_list, pred_list
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Precision/Recall/F1 com matching fuzzy WER/CER para campos com múltiplos valores.

    Cada elemento de pred é considerado TP se existir um elemento de ref com
    error_rate ≤ _WER_MATCH_THRESHOLD. Cada ref é contado no máximo uma vez.
    """
    if not isinstance(ref_list, list):
        ref_list = [ref_list] if ref_list else []
    if not isinstance(pred_list, list):
        pred_list = [pred_list] if pred_list else []
    ref_norms = [_normalizar_texto(str(v)) for v in ref_list if v and str(v).strip()]
    pred_norms = [_normalizar_texto(str(v)) for v in pred_list if v and str(v).strip()]
    if not ref_norms and not pred_norms:
        return None, None, None
    matched_ref: set = set()
    tp = 0
    for pred_elem in pred_norms:
        for i, ref_elem in enumerate(ref_norms):
            if i in matched_ref:
                continue
            if pred_elem == ref_elem:
                tp += 1
                matched_ref.add(i)
                break
            err = (
                calc_cer(ref_elem, pred_elem)
                if len(ref_elem.split()) == 1
                else calc_wer(ref_elem, pred_elem)
            )
            if err <= _WER_MATCH_THRESHOLD:
                tp += 1
                matched_ref.add(i)
                break
    fp = len(pred_norms) - tp
    fn = len(ref_norms) - tp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _calcular_metricas_metadados(gt_row: dict, predicao: dict) -> dict:
    """Calcula P/R/F1 por campo de metadado e macro-média geral.

    Retorna:
      {
        "por_campo": { json_key: {"ref": ..., "pred": ..., "precision": ..., "recall": ..., "f1": ...} },
        "precision": float|None,  # macro-média
        "recall":    float|None,
        "f1":        float|None,
      }
    """
    por_campo: Dict[str, dict] = {}
    for _, json_key in _METADATA_FIELD_MAP:
        ref_val = gt_row.get(json_key)
        pred_val = predicao.get(json_key)
        if json_key in _METADATA_LIST_FIELDS:
            ref_list = [ref_val] if (ref_val is not None and str(ref_val).strip()) else []
            pred_list = pred_val if isinstance(pred_val, list) else ([pred_val] if pred_val else [])
            p, r, f1 = _prf_campo_lista(ref_list, pred_list)
        else:
            p, r, f1 = _prf_campo_simples(ref_val, pred_val)
        por_campo[json_key] = {"ref": ref_val, "pred": pred_val, "precision": p, "recall": r, "f1": f1}

    p_vals = [v["precision"] for v in por_campo.values() if v["precision"] is not None]
    r_vals = [v["recall"] for v in por_campo.values() if v["recall"] is not None]
    f1_vals = [v["f1"] for v in por_campo.values() if v["f1"] is not None]
    return {
        "por_campo": por_campo,
        "precision": round(sum(p_vals) / len(p_vals), 4) if p_vals else None,
        "recall": round(sum(r_vals) / len(r_vals), 4) if r_vals else None,
        "f1": round(sum(f1_vals) / len(f1_vals), 4) if f1_vals else None,
    }


# ---------------------------------------------------------------------------
# Athena / Glue helpers
# ---------------------------------------------------------------------------

def _escrever_athena_ndjson(s3, bucket: str, athena_prefix: str, table: str,
                             model_version: str, test_id: str, run_id: str,
                             records: list) -> str:
    """Escreve records como NDJSON em path particionado para Athena. Retorna o prefixo da partição."""
    partition_prefix = f"{athena_prefix.rstrip('/')}/{table}/model_version={model_version}/test_id={test_id}"
    key = f"{partition_prefix}/{run_id}.json"
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")
    logger.info("Athena NDJSON: s3://%s/%s", bucket, key)
    return partition_prefix


def _registrar_particao_glue(glue, database: str, table: str, model_version: str,
                              test_id: str, bucket: str, s3_location: str) -> None:
    """Registra a partição no Glue Catalog para disponibilidade imediata no Athena."""
    try:
        glue.create_partition(
            DatabaseName=database,
            TableName=table,
            PartitionInput={
                "Values": [model_version, test_id],
                "StorageDescriptor": {
                    "Location": f"s3://{bucket}/{s3_location}/",
                    "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                    "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    "SerdeInfo": {
                        "SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                        "Parameters": {"serialization.format": "1"},
                    },
                    "Compressed": False,
                    "Columns": [],
                },
            },
        )
        logger.info("Partição criada: %s.%s [%s/%s]", database, table, model_version, test_id)
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            logger.info("Partição já existe: %s.%s [%s/%s]", database, table, model_version, test_id)
        else:
            raise

def _logar_audios_nao_encontrados(rotulo: str, stems: List[str], amostra: int = 10) -> None:
    """Loga um resumo único dos áudios não encontrados."""
    if not stems:
        return
    amostra_str = ", ".join(stems[:amostra])
    sufixo = "" if len(stems) <= amostra else f" (+{len(stems) - amostra} outros)"
    logger.warning("%s: %d áudio(s) não encontrado(s) no índice S3. Exemplos: %s%s",
                   rotulo, len(stems), amostra_str, sufixo)

# ---------------------------------------------------------------------------
# Fase 1 — Submit
# ---------------------------------------------------------------------------

def _fase_submeter(event: dict, context) -> dict:
    """Descobre áudios, submete todos os jobs em paralelo e aciona a fase aggregate."""
    bucket = _env("BUCKET_NAME")
    audio_bucket = os.environ.get("AUDIO_BUCKET_NAME", "").strip() or bucket
    api_base_url = _env("ONS_TRANS_API_URL").rstrip("/")
    transcriptions_prefix = _env("TRANSCRIPTIONS_KEY_PATH").rstrip("/") + "/"
    groundtruth_prefix = _env("GROUNDTRUTH_KEY_PATH").rstrip("/") + "/"
    audio_prefixes = [p.strip().rstrip("/") + "/" for p in _env("AUDIO_KEY_PATHS").split(",")]
    metrics_s3_prefix = _env("METRICS_S3_PREFIX").rstrip("/")
    metadata_gt_prefix = os.environ.get("METADATA_GROUNDTRUTH_KEY_PATH", "").strip()
    max_audio_count = int(os.environ.get("MAX_AUDIO_COUNT", str(_MAX_AUDIO_COUNT_DEFAULT)))
    processing_mode = os.environ.get("PROCESSING_MODE", "integrated").lower()
    if processing_mode not in ("integrated", "independent"):
        raise ValueError(
            f"PROCESSING_MODE inválido: {processing_mode!r}. Valores aceitos: 'integrated', 'independent'."
        )

    model_version = event.get("model_version") or _env("MODEL_VERSION")
    test_id = event.get("test_id") or _env("TEST_ID")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    execucao = datetime.now(timezone.utc).isoformat()

    s3 = boto3.client("s3", config=Config(max_pool_connections=len(audio_prefixes) + 5))

    logger.info("=== FASE SUBMIT | model_version=%s | test_id=%s | run_id=%s ===",
                model_version, test_id, run_id)

    gt_keys = _listar_groundtruths(s3, bucket, groundtruth_prefix)
    logger.info("Groundtruths encontrados: %d", len(gt_keys))

    audio_index = _indexar_audios(s3, audio_bucket, audio_prefixes)
    logger.info("Índice de áudios: %d arquivos indexados (bucket=%s)", len(audio_index), audio_bucket)

    jobs_pendentes = []
    erros_audio = []
    set_a_sem_audio: List[str] = []
    for gt_key in gt_keys:
        nome_sem_ext = Path(gt_key).stem
        audio_key = audio_index.get(nome_sem_ext)
        if audio_key is None:
            set_a_sem_audio.append(nome_sem_ext)
            erros_audio.append({
                "run_id": run_id, "model_version": model_version, "test_id": test_id,
                "arquivo": nome_sem_ext, "erro": "Áudio não encontrado no S3",
            })
            continue
        transcricao_key = transcriptions_prefix + nome_sem_ext + ".json"
        jobs_pendentes.append({
            "nome": nome_sem_ext,
            "gt_key": gt_key,
            "audio_key": audio_key,
            "transcricao_key": transcricao_key,
        })

    _logar_audios_nao_encontrados("Set A (transcrição)", set_a_sem_audio)
    logger.info("Set A (transcrição): %d jobs | Sem áudio: %d", len(jobs_pendentes), len(erros_audio))

    # Carrega o GT de metadados uma vez: necessário para o teste de interseção (integrated)
    # e para a seleção do Set B (independent).
    gt_meta_full = _carregar_gt_metadados(s3, bucket, metadata_gt_prefix) if metadata_gt_prefix else {}
    stems_set_a = {j["nome"] for j in jobs_pendentes}

    # Fallback do modo integrado: sem interseção transcrição ∩ metadados, alterna para independent.
    if processing_mode == "integrated":
        interseccao = stems_set_a & set(gt_meta_full)
        if interseccao:
            logger.info("Integrado: %d áudios na interseção (transcrição ∩ metadados).", len(interseccao))
        else:
            logger.warning(
                "Modo 'integrated' solicitado, mas a interseção entre GT de transcrição e GT de metadados "
                "está vazia (Set A=%d, GT metadados=%d, interseção=0). Alternando para o modo 'independent'.",
                len(stems_set_a), len(gt_meta_full),
            )
            processing_mode = "independent"

    if processing_mode == "independent":
        if not metadata_gt_prefix or not gt_meta_full:
            logger.warning("Modo 'independent' mas GT de metadados ausente/vazio — Set B ignorado.")
        else:
            candidatos_validos = {
                s: v for s, v in gt_meta_full.items()
                if s not in stems_set_a and _registro_set_b_valido(v)
            }
            n_descartados = len(gt_meta_full) - len(stems_set_a & set(gt_meta_full)) - len(candidatos_validos)
            logger.info(
                "Set B: %d registros candidatos válidos (%d descartados por transcrição vazia/'Sem áudio' "
                "ou metadados todos vazios).",
                len(candidatos_validos), max(n_descartados, 0),
            )
            stems_set_b = _selecionar_amostra_estratificada(
                {s: v.get("_nomeag") for s, v in candidatos_validos.items()}, max_audio_count
            )
            n_b_antes = len(jobs_pendentes)
            set_b_sem_audio: List[str] = []
            for stem in stems_set_b:
                audio_key = audio_index.get(stem)
                if audio_key is None:
                    set_b_sem_audio.append(stem)
                    erros_audio.append({
                        "run_id": run_id, "model_version": model_version, "test_id": test_id,
                        "arquivo": stem, "erro": "Set B: áudio não encontrado no S3",
                    })
                    continue
                jobs_pendentes.append({
                    "nome": stem,
                    "gt_key": None,
                    "gt_texto": candidatos_validos[stem].get("_transcription"),
                    "audio_key": audio_key,
                    "transcricao_key": transcriptions_prefix + stem + ".json",
                })
            _logar_audios_nao_encontrados("Set B (metadados)", set_b_sem_audio)
            logger.info("Set B (metadados independente): %d jobs adicionados. Total: %d",
                        len(jobs_pendentes) - n_b_antes, len(jobs_pendentes))

    logger.info("Total jobs a submeter: %d", len(jobs_pendentes))

    jobs_submetidos = []
    erros_submissao = []

    def _submeter(job: dict) -> dict:
        try:
            job_id = _submeter_job(api_base_url, audio_bucket, bucket, job["audio_key"], job["transcricao_key"])
            logger.info("Job submetido: %s → job_id=%s", job["nome"], job_id)
            return {**job, "job_id": job_id}
        except Exception as exc:
            logger.error("Falha ao submeter '%s': %s", job["nome"], exc)
            return {**job, "job_id": None, "erro": str(exc)}

    submit_budget = max(context.get_remaining_time_in_millis() / 1000 - _SUBMIT_RESERVE_TIME_S, 10)
    logger.info("Budget de submit: %.0fs", submit_budget)
    pool = ThreadPoolExecutor(max_workers=_SUBMIT_WORKERS)
    try:
        future_to_job = {pool.submit(_submeter, j): j for j in jobs_pendentes}
        try:
            for fut in as_completed(list(future_to_job.keys()), timeout=submit_budget):
                r = fut.result()
                if r.get("job_id"):
                    jobs_submetidos.append(r)
                else:
                    erros_submissao.append({
                        "run_id": run_id, "model_version": model_version, "test_id": test_id,
                        "arquivo": r["nome"], "erro": r.get("erro", "Falha na submissão"),
                    })
        except FuturesTimeoutError:
            nomes_processados = {r["nome"] for r in jobs_submetidos} | {e["arquivo"] for e in erros_submissao}
            nao_processados = [j for j in jobs_pendentes if j["nome"] not in nomes_processados]
            logger.warning(
                "Budget de submit esgotado (%.0fs). %d/%d jobs processados. %d marcados como erro.",
                submit_budget, len(jobs_submetidos) + len(erros_submissao),
                len(jobs_pendentes), len(nao_processados),
            )
            for job in nao_processados:
                erros_submissao.append({
                    "run_id": run_id, "model_version": model_version, "test_id": test_id,
                    "arquivo": job["nome"], "erro": "Não submetido: budget de submit esgotado",
                })
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    manifesto = {
        "run_id": run_id,
        "model_version": model_version,
        "test_id": test_id,
        "execucao": execucao,
        "processing_mode": processing_mode,
        "jobs": jobs_submetidos,
        "erros_audio": erros_audio,
        "erros_submissao": erros_submissao,
    }
    manifest_key = f"{metrics_s3_prefix}/_manifestos/{run_id}.json"
    s3.put_object(
        Bucket=bucket, Key=manifest_key,
        Body=json.dumps(manifesto, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Manifesto salvo: s3://%s/%s", bucket, manifest_key)
    logger.info("=== SUBMIT concluído: %d jobs submetidos ===", len(jobs_submetidos))

    boto3.client("lambda").invoke(
        FunctionName=context.function_name,
        InvocationType="Event",
        Payload=json.dumps({"step": "aggregate", "run_id": run_id, "manifest_key": manifest_key}).encode("utf-8"),
    )
    logger.info("Fase aggregate invocada assincronamente.")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "run_id": run_id,
            "jobs_submetidos": len(jobs_submetidos),
            "erros_audio": len(erros_audio),
            "erros_submissao": len(erros_submissao),
            "manifest_key": f"s3://{bucket}/{manifest_key}",
            "status": "aggregate_invocado",
        }),
    }


# ---------------------------------------------------------------------------
# Fase 2 — Aggregate
# ---------------------------------------------------------------------------

def _fase_agregar(event: dict) -> dict:
    """Aguarda transcrições no S3, calcula métricas em paralelo e escreve no Athena."""
    bucket = _env("BUCKET_NAME")
    athena_s3_prefix = _env("ATHENA_S3_PREFIX").rstrip("/")
    athena_database = _env("ATHENA_DATABASE")
    metrics_s3_prefix = _env("METRICS_S3_PREFIX").rstrip("/")
    metadata_gt_prefix = os.environ.get("METADATA_GROUNDTRUTH_KEY_PATH", "").strip()

    manifest_key = event["manifest_key"]
    s3 = boto3.client("s3", config=Config(max_pool_connections=_AGGREGATE_WORKERS))
    glue = boto3.client("glue")

    manifesto = json.loads(s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read())
    run_id = manifesto["run_id"]
    model_version = manifesto["model_version"]
    test_id = manifesto["test_id"]
    execucao = manifesto["execucao"]

    logger.info("=== FASE AGGREGATE | run_id=%s | jobs=%d ===",
                run_id, len(manifesto["jobs"]))

    processing_mode = manifesto.get("processing_mode", "integrated")
    logger.info("Modo de processamento: %s", processing_mode)

    # Carrega groundtruth de metadados
    gt_metadados = _carregar_gt_metadados(s3, bucket, metadata_gt_prefix)

    if processing_mode == "integrated":
        # Interseção completa: todos os áudios com GT de transcrição E GT de metadados, sem amostragem
        nomes_com_gt_transcricao = {Path(j["gt_key"]).stem for j in manifesto["jobs"] if j.get("gt_key")}
        gt_metadados = {k: v for k, v in gt_metadados.items() if k in nomes_com_gt_transcricao}
        logger.info("Integrado — GT metadados: %d áudios na interseção (transcrição ∩ metadados).", len(gt_metadados))
        if not gt_metadados:
            logger.warning("Nenhum arquivo processado possui GT de metadados. Métricas de metadados não calculadas.")
    else:
        # Independente: Set B já foi pré-selecionado (amostra estratificada) no submit
        logger.info("Independente — GT metadados: %d registros carregados (Set B pré-selecionado no submit).",
                    len(gt_metadados))

    erros: List[dict] = list(manifesto.get("erros_audio", [])) + list(manifesto.get("erros_submissao", []))
    registros_por_audio: List[dict] = []

    def _processar_job(job: dict) -> dict:
        nome = job["nome"]
        try:
            if not _aguardar_transcricao_no_s3(s3, bucket, job["transcricao_key"]):
                raise RuntimeError(f"Timeout aguardando transcrição: {job['transcricao_key']}")

            transcricao = _carregar_transcricao(s3, bucket, job["transcricao_key"])
            # GT de transcrição: arquivo .txt (Set A) ou coluna 'transcription' da planilha (Set B independente).
            if job.get("gt_key"):
                texto_gt = _baixar_texto_groundtruth(s3, bucket, job["gt_key"])
            else:
                texto_gt = job.get("gt_texto")
            metricas_texto = (
                _calcular_metricas_texto(texto_gt, _extrair_texto(transcricao))
                if texto_gt else {k: None for k in ["wer", "mer", "wil", "wip", "cer"]}
            )
            assuntos_predicao = _extrair_assuntos(transcricao)

            # Métricas de metadados (quando disponível GT)
            tem_gt_metadados = nome in gt_metadados
            if tem_gt_metadados:
                predicao_metadados = _extrair_metadados_predicao(transcricao)
                gt_row = {k: v for k, v in gt_metadados[nome].items() if not k.startswith("_")}
                metricas_meta = _calcular_metricas_metadados(gt_row, predicao_metadados)
            else:
                metricas_meta = {"por_campo": {}, "precision": None, "recall": None, "f1": None}

            # Referência de assunto para retrocompatibilidade com schema anterior
            assunto_campo = metricas_meta["por_campo"].get("assunto_do_audio", {})
            assuntos_ref_raw = assunto_campo.get("ref")
            assuntos_referencia_lista = (
                [assuntos_ref_raw] if (assuntos_ref_raw and str(assuntos_ref_raw).strip()) else []
            )

            # Campos planos por metadado: {col}_pred, {col}_ref, {col}_precision, {col}_recall, {col}_f1
            # assunto_do_audio: omite pred/ref (já estão em assuntos_predicao/referencia)
            meta_flat: dict = {}
            for json_key, col in _METADATA_COL_MAP.items():
                campo = metricas_meta["por_campo"].get(json_key, {})
                if json_key != "assunto_do_audio":
                    pred_raw = campo.get("pred")
                    ref_raw = campo.get("ref")
                    meta_flat[f"{col}_pred"] = str(pred_raw) if pred_raw is not None else None
                    meta_flat[f"{col}_ref"] = str(ref_raw) if ref_raw is not None else None
                meta_flat[f"{col}_precision"] = campo.get("precision")
                meta_flat[f"{col}_recall"] = campo.get("recall")
                meta_flat[f"{col}_f1"] = campo.get("f1")

            # Custo estimado LLM (catálogo em llm_pricing.py; tokens vêm do JSON da API)
            llm_usage_raw = extract_llm_usage(transcricao)
            llm_usage_priced = enrich_usage_with_cost(llm_usage_raw)
            llm_flat = flat_llm_cost_fields(llm_usage_priced)

            logger.info(
                "OK %s | WER=%s | CER=%s | assuntos=%s | F1_meta=%s | LLM_cost_usd=%s",
                nome, metricas_texto["wer"], metricas_texto["cer"],
                assuntos_predicao, metricas_meta["f1"],
                llm_flat.get("llm_estimated_cost_usd"),
            )
            return {
                "ok": True,
                "registro": {
                    "run_id": run_id, "model_version": model_version, "test_id": test_id,
                    "execucao": execucao, "arquivo": nome, "audio_key": job["audio_key"],
                    # Métricas de transcrição
                    "wer": metricas_texto["wer"], "mer": metricas_texto["mer"],
                    "wil": metricas_texto["wil"], "wip": metricas_texto["wip"],
                    "cer": metricas_texto["cer"],
                    # Assuntos (retrocompatível)
                    "assuntos_predicao": assuntos_predicao,
                    "assuntos_referencia": assuntos_referencia_lista,
                    # Métricas de metadados — campos planos
                    "tem_gt_metadados": tem_gt_metadados,
                    **meta_flat,
                    # Macro-média de todos os campos de metadados
                    "precision_metadados": metricas_meta["precision"],
                    "recall_metadados": metricas_meta["recall"],
                    "f1_metadados": metricas_meta["f1"],
                    # Uso/custo LLM estimado (SSOT de preço: llm_pricing.py)
                    **llm_flat,
                    "llm_usage": llm_usage_priced,
                },
            }
        except Exception:
            logger.exception("Erro ao processar '%s'.", nome)
            return {"ok": False, "nome": nome}

    with ThreadPoolExecutor(max_workers=_AGGREGATE_WORKERS) as pool:
        for fut in as_completed(pool.submit(_processar_job, j) for j in manifesto["jobs"]):
            resultado = fut.result()
            if resultado["ok"]:
                registros_por_audio.append(resultado["registro"])
            else:
                erros.append({
                    "run_id": run_id, "model_version": model_version, "test_id": test_id,
                    "arquivo": resultado["nome"], "erro": "Exceção durante o processamento",
                })

    # Médias por campo de metadado (somente áudios com GT de metadados)
    registros_com_gt_meta = [r for r in registros_por_audio if r.get("tem_gt_metadados")]
    meta_agg_flat: dict = {}
    for json_key, col in _METADATA_COL_MAP.items():
        for metric in ("precision", "recall", "f1"):
            col_key = f"{col}_{metric}"
            vals = [r[col_key] for r in registros_com_gt_meta if r.get(col_key) is not None]
            meta_agg_flat[f"{col_key}_media"] = round(sum(vals) / len(vals), 4) if vals else None

    registros_com_llm = [
        r for r in registros_por_audio if r.get("llm_estimated_cost_usd") is not None
    ]
    llm_cost_total = (
        round(sum(r["llm_estimated_cost_usd"] for r in registros_com_llm), 8)
        if registros_com_llm else None
    )
    llm_cost_media = (
        round(llm_cost_total / len(registros_com_llm), 8)
        if registros_com_llm and llm_cost_total is not None else None
    )

    registro_agregado = {
        "run_id": run_id,
        "model_version": model_version,
        "test_id": test_id,
        "execucao": execucao,
        "total_arquivos_processados": len(registros_por_audio),
        "total_erros": len(erros),
        "total_com_gt_metadados": len(registros_com_gt_meta),
        # Métricas de transcrição
        "wer_media": _media(registros_por_audio, "wer"),
        "mer_media": _media(registros_por_audio, "mer"),
        "wil_media": _media(registros_por_audio, "wil"),
        "wip_media": _media(registros_por_audio, "wip"),
        "cer_media": _media(registros_por_audio, "cer"),
        # Métricas de metadados — por campo (flat) e macro-média
        **meta_agg_flat,
        "precision_metadados_media": _media(registros_com_gt_meta, "precision_metadados"),
        "recall_metadados_media": _media(registros_com_gt_meta, "recall_metadados"),
        "f1_metadados_media": _media(registros_com_gt_meta, "f1_metadados"),
        # Custo LLM estimado (só áudios com llm_usage_metrics + preço no catálogo)
        "total_com_llm_usage": len(registros_com_llm),
        "llm_estimated_cost_usd_total": llm_cost_total,
        "llm_estimated_cost_usd_media": llm_cost_media,
        "llm_total_input_tokens_media": _media(registros_com_llm, "llm_total_input_tokens"),
        "llm_total_output_tokens_media": _media(registros_com_llm, "llm_total_output_tokens"),
    }

    logger.info(
        "Consolidado — processados: %d | erros: %d | WER médio: %s | CER médio: %s | "
        "F1_meta médio: %s (sobre %d áudios com GT metadados) | "
        "LLM cost total USD: %s (média %s, n=%d com usage)",
        registro_agregado["total_arquivos_processados"],
        registro_agregado["total_erros"],
        registro_agregado["wer_media"],
        registro_agregado["cer_media"],
        registro_agregado["f1_metadados_media"],
        registro_agregado["total_com_gt_metadados"],
        registro_agregado["llm_estimated_cost_usd_total"],
        registro_agregado["llm_estimated_cost_usd_media"],
        registro_agregado["total_com_llm_usage"],
    )

    resultado_completo = {
        "run_id": run_id,
        "model_version": model_version,
        "test_id": test_id,
        "execucao": execucao,
        "agregado": registro_agregado,
        "por_audio": registros_por_audio,
        "erros": erros,
    }
    metrics_key = f"{metrics_s3_prefix}/{model_version}/{test_id}/{run_id}.json"
    s3.put_object(
        Bucket=bucket, Key=metrics_key,
        Body=json.dumps(resultado_completo, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("JSON completo salvo: s3://%s/%s", bucket, metrics_key)

    loc_por_audio = _escrever_athena_ndjson(
        s3, bucket, athena_s3_prefix, _TABLE_POR_AUDIO,
        model_version, test_id, run_id, registros_por_audio,
    )
    loc_agregadas = _escrever_athena_ndjson(
        s3, bucket, athena_s3_prefix, _TABLE_AGREGADAS,
        model_version, test_id, run_id, [registro_agregado],
    )

    _registrar_particao_glue(glue, athena_database, _TABLE_POR_AUDIO,
                             model_version, test_id, bucket, loc_por_audio)
    _registrar_particao_glue(glue, athena_database, _TABLE_AGREGADAS,
                             model_version, test_id, bucket, loc_agregadas)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "run_id": run_id,
            "model_version": model_version,
            "test_id": test_id,
            "processados": registro_agregado["total_arquivos_processados"],
            "erros": registro_agregado["total_erros"],
            "com_gt_metadados": registro_agregado["total_com_gt_metadados"],
            "output_json": f"s3://{bucket}/{metrics_key}",
            "athena_por_audio": f"s3://{bucket}/{loc_por_audio}/",
            "athena_agregadas": f"s3://{bucket}/{loc_agregadas}/",
        }),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    step = (event or {}).get("step", "submit")
    if step == "submit":
        return _fase_submeter(event or {}, context)
    if step == "aggregate":
        return _fase_agregar(event or {})
    raise ValueError(f"Step inválido: {step!r}")
