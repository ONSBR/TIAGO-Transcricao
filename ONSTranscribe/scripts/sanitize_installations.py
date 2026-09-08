"""
Script de saneamento do metadado 'instalacao_ou_usina_envolvida' no OpenSearch.

Uso:
  python sanitize_installations.py [opções]

Opções:
  --dry-run               Exibe as alterações sem aplicá-las
  --all                   Processa todo o histórico
  --from-date YYYY-MM-DD  Processa apenas documentos a partir desta data
  --limit N               Processa no máximo N documentos (útil para pilotos)
  --batch-size N          Tamanho do lote para updates (padrão: 50)
  --index INDEX           Nome do índice OpenSearch (padrão: env OPENSEARCH_INDEX)
  --opensearch-uri URI    URI do OpenSearch (padrão: env OPENSEARCH_URI)
  --bucket BUCKET         S3 bucket com o JSON de instalações (padrão: env INSTALLATIONS_BUCKET)
  --s3-key KEY            S3 key do JSON de instalações (padrão: env INSTALLATIONS_S3_KEY)

Variáveis de ambiente (alternativas aos parâmetros):
  OPENSEARCH_URI, OPENSEARCH_INDEX, AWS_REGION,
  INSTALLATIONS_BUCKET, INSTALLATIONS_S3_KEY,
  INSTALLATION_MATCH_THRESHOLD_HIGH (padrão: 90),
  INSTALLATION_MATCH_THRESHOLD_LOW  (padrão: 60),
  INSTALLATION_DISAMBIGUATION_PROMPT_ID,
  INSTALLATION_DISAMBIGUATION_PROMPT_VERSION,
  TRANSCRIBE_BEDROCK_MODEL

Exemplo de uso seguro (piloto):
  python sanitize_installations.py --dry-run --limit 200

Exemplo rollout incremental:
  python sanitize_installations.py --from-date 2024-01-01 --batch-size 50

Exemplo histórico completo (executar após validar piloto):
  python sanitize_installations.py --all
"""

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import boto3
from opensearchpy import OpenSearch
from rapidfuzz import fuzz, process as fuzz_process

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def _normalize_nomelongo(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def _strip_accents_upper(value: str) -> str:
    """Remove acentos e coloca em maiúsculas (a tabela de instalações é sem acentos)."""
    s = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return s.upper()


def _strip_json_fence(s: str) -> str:
    """Remove cercas markdown (```json ... ```) antes de json.loads."""
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _load_installations_from_s3(bucket: str, key: str, region: str) -> list:
    s3 = boto3.client("s3", region_name=region)
    response = s3.get_object(Bucket=bucket, Key=key)
    data = json.loads(response["Body"].read().decode("utf-8"))
    logger.info(f"Lista de instalações carregada: {len(data)} registros de s3://{bucket}/{key}")
    return data


def _fuzzy_match(mention: str, installations: list, threshold_low: int, limit: int = 5) -> list:
    names = [_strip_accents_upper(inst["nomelongo"]) for inst in installations]
    results = fuzz_process.extract(
        _strip_accents_upper(mention),
        names,
        scorer=fuzz.token_set_ratio,
        score_cutoff=threshold_low,
        limit=limit,
    )
    candidates = []
    for matched_name, score, idx in results:
        entry = dict(installations[idx])
        entry["_score"] = score
        candidates.append(entry)
    candidates.sort(key=lambda x: x["_score"], reverse=True)
    return candidates


def _disambiguate_with_claude(
    mention: str,
    candidates: list,
    context_clues: dict,
    gravador: str,
    bedrock_client,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
) -> str | None:
    try:
        bedrock_agent = boto3.client("bedrock-agent", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        disambiguation_prompt = bedrock_agent.get_prompt(
            promptIdentifier=prompt_id,
            promptVersion=prompt_version,
        )["variants"][0]["templateConfiguration"]["text"]["text"]

        # Mapa sigla/nome do gravador -> UF, usado apenas como dica geográfica na
        # desambiguação. Valores de exemplo: ajuste para as localidades do seu
        # ambiente. Gravador não mapeado resulta em gravador_uf vazio.
        gravador_uf_map = {
            "LOC1": "UF1", "LOC2": "UF2", "LOC3": "UF3", "LOC4": "UF4",
            "Localidade Um": "UF1", "Localidade Dois": "UF2",
            "Localidade Tres": "UF3", "Localidade Quatro": "UF4",
        }
        gravador_uf = gravador_uf_map.get(gravador, "")

        user_content = json.dumps({
            "mencao_original": mention,
            "tema": context_clues.get("tema", "null"),
            "lt_detectada": context_clues.get("lt_detectada", False),
            "gravador_uf": gravador_uf,
            "uf_hint": context_clues.get("uf_hint"),
            "candidatos": [{k: v for k, v in c.items() if k != "_score"} for c in candidates],
            "trecho_transcricao": [],
        }, ensure_ascii=False)

        response = bedrock_client.converse(
            modelId=model_id,
            system=[{"text": disambiguation_prompt}],
            messages=[{"role": "user", "content": [{"text": user_content}]}],
            inferenceConfig={"maxTokens": 512, "temperature": 0.2},
        )
        raw = response["output"]["message"]["content"][0]["text"]
        result = json.loads(_strip_json_fence(raw))
        selected = result.get("nomelongo_selecionado")
        if selected and selected.lower() != "null":
            return _normalize_nomelongo(selected)
        return None
    except Exception as e:
        logger.warning(f"Desambiguação Claude falhou para '{mention}': {e}")
        return None


def normalize_mentions(
    raw_mentions: list,
    gravador: str,
    installations: list,
    threshold_high: int,
    threshold_low: int,
    bedrock_client,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
) -> list:
    result = []
    seen = set()
    for mention in raw_mentions:
        if not mention or str(mention).lower() in ("null", ""):
            continue
        candidates = _fuzzy_match(mention, installations, threshold_low)
        if not candidates:
            continue
        top_score = candidates[0]["_score"]
        if top_score >= threshold_high and (len(candidates) == 1 or candidates[1]["_score"] < threshold_high - 5):
            selected = _normalize_nomelongo(candidates[0]["nomelongo"])
        else:
            selected = _disambiguate_with_claude(
                mention, candidates, {}, gravador, bedrock_client, model_id, prompt_id, prompt_version
            )
        if selected and selected not in seen:
            seen.add(selected)
            result.append(selected)
    return result


# ---------------------------------------------------------------------------
# OpenSearch helpers
# ---------------------------------------------------------------------------

def _build_query(from_date: str | None) -> dict:
    must = [{"exists": {"field": "transcricaoAudio.key_entities.instalacao_ou_usina_envolvida"}}]
    if from_date:
        must.append({"range": {"data": {"gte": from_date}}})
    return {"query": {"bool": {"must": must}}}


def _scroll_documents(os_client: OpenSearch, index: str, query: dict, limit: int | None):
    page = os_client.search(index=index, body=query, scroll="5m", size=100)
    scroll_id = page["_scroll_id"]
    hits = page["hits"]["hits"]
    count = 0
    while hits:
        for doc in hits:
            if limit is not None and count >= limit:
                os_client.clear_scroll(scroll_id=scroll_id)
                return
            yield doc
            count += 1
        page = os_client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = page["_scroll_id"]
        hits = page["hits"]["hits"]
    os_client.clear_scroll(scroll_id=scroll_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Exibe alterações sem aplicar")
    parser.add_argument("--all", dest="all_records", action="store_true", help="Processa todo o histórico")
    parser.add_argument("--from-date", metavar="YYYY-MM-DD", help="Processa registros a partir desta data")
    parser.add_argument("--limit", type=int, help="Limita o número de documentos processados")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--index", default=os.environ.get("OPENSEARCH_INDEX", ""))
    parser.add_argument("--opensearch-uri", default=os.environ.get("OPENSEARCH_URI", ""))
    parser.add_argument("--bucket", default=os.environ.get("INSTALLATIONS_BUCKET", ""))
    parser.add_argument("--s3-key", default=os.environ.get("INSTALLATIONS_S3_KEY", "installations/installations.json"))
    args = parser.parse_args()

    # Validações
    if not args.index:
        sys.exit("Erro: informe --index ou defina OPENSEARCH_INDEX")
    if not args.opensearch_uri:
        sys.exit("Erro: informe --opensearch-uri ou defina OPENSEARCH_URI")
    if not args.bucket:
        sys.exit("Erro: informe --bucket ou defina INSTALLATIONS_BUCKET")

    region = os.environ.get("AWS_REGION", "us-east-1")
    threshold_high = int(os.environ.get("INSTALLATION_MATCH_THRESHOLD_HIGH", "90"))
    threshold_low = int(os.environ.get("INSTALLATION_MATCH_THRESHOLD_LOW", "60"))
    model_id = os.environ.get("TRANSCRIBE_BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-6")
    prompt_id = os.environ.get("INSTALLATION_DISAMBIGUATION_PROMPT_ID", "")
    prompt_version = os.environ.get("INSTALLATION_DISAMBIGUATION_PROMPT_VERSION", "")

    # Data de corte: padrão são os últimos 90 dias
    from_date = None
    if args.from_date:
        from_date = args.from_date
    elif not args.all_records:
        from_date = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        logger.info(f"Nenhuma opção de data fornecida — processando últimos 90 dias (>= {from_date}). "
                    "Use --all para processar todo o histórico.")

    logger.info(f"dry_run={args.dry_run}  from_date={from_date}  limit={args.limit}")

    # Inicializa clientes
    installations = _load_installations_from_s3(args.bucket, args.s3_key, region)
    bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    os_client = OpenSearch(hosts=[args.opensearch_uri], timeout=120)

    query = _build_query(from_date)

    stats = {"total": 0, "altered": 0, "unchanged": 0, "blanked": 0, "errors": 0}
    pending_updates: list[tuple[str, list]] = []

    def flush_batch(force: bool = False):
        if not pending_updates:
            return
        if not force and len(pending_updates) < args.batch_size:
            return
        for doc_id, new_val in pending_updates:
            try:
                os_client.update(
                    index=args.index,
                    id=doc_id,
                    body={"doc": {"transcricaoAudio": {"key_entities": {"instalacao_ou_usina_envolvida": new_val}}}},
                    retry_on_conflict=3,
                    refresh=False,
                )
            except Exception as e:
                logger.error(f"Erro ao atualizar doc {doc_id}: {e}")
                stats["errors"] += 1
        pending_updates.clear()

    for doc in _scroll_documents(os_client, args.index, query, args.limit):
        stats["total"] += 1
        doc_id = doc["_id"]
        source = doc.get("_source", {})
        key_entities = (source.get("transcricaoAudio") or {}).get("key_entities") or {}
        gravador = source.get("gravador", "")

        current_value = key_entities.get("instalacao_ou_usina_envolvida", [])
        if isinstance(current_value, str):
            current_value = [current_value] if current_value else []

        try:
            new_value = normalize_mentions(
                current_value, gravador, installations,
                threshold_high, threshold_low,
                bedrock_client, model_id, prompt_id, prompt_version,
            )
        except Exception as e:
            logger.error(f"Erro ao normalizar doc {doc_id}: {e}")
            stats["errors"] += 1
            continue

        if new_value == current_value:
            stats["unchanged"] += 1
            continue

        if not new_value and current_value:
            stats["blanked"] += 1
        else:
            stats["altered"] += 1

        logger.info(f"[{'DRY' if args.dry_run else 'UPDATE'}] {doc_id}: {current_value} → {new_value}")

        if not args.dry_run:
            pending_updates.append((doc_id, new_value))
            flush_batch()

    if not args.dry_run:
        flush_batch(force=True)

    logger.info(
        f"\nResumo: total={stats['total']} | alterados={stats['altered']} | "
        f"mantidos={stats['unchanged']} | zerados={stats['blanked']} | erros={stats['errors']}"
    )


if __name__ == "__main__":
    main()
