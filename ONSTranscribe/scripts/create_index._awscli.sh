#!/usr/bin/env bash

set -euo pipefail

log() {
  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  printf 'ERRO: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando obrigatório não encontrado: $1"
}

require_env() {
  local var_name=""
  for var_name in "$@"; do
    [[ -n "${!var_name:-}" ]] || fail "Variável de ambiente obrigatória não informada: $var_name"
  done
}

require_command aws
require_command mktemp

require_env DOMAIN_NAME INDEX_NAME AWS_DEFAULT_REGION

SCHEMA_FILE="$(mktemp)"
trap 'rm -f "$SCHEMA_FILE"' EXIT

cat >"$SCHEMA_FILE" <<'EOF'
{
  "settings": {
    "index": {
      "number_of_shards": 27,
      "number_of_replicas": 2,
      "analysis": {
        "analyzer": {
          "custom_ngram_analyzer": {
            "filter": "lowercase",
            "tokenizer": "custom_ngram_tokenizer"
          }
        },
        "tokenizer": {
          "custom_ngram_tokenizer": {
            "type": "ngram",
            "min_gram": 3,
            "max_gram": 4,
            "token_chars": ["letter", "digit"]
          }
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "categorias": { "type": "keyword" },
      "categoriasAutocomplete": {
        "type": "completion",
        "analyzer": "standard",
        "preserve_separators": true,
        "preserve_position_increments": true,
        "max_input_length": 50
      },
      "comentario": { "type": "text", "analyzer": "custom_ngram_analyzer" },
      "data": { "type": "long" },
      "dataFull": { "type": "long" },
      "datafull": { "type": "long" },
      "direcao": {
        "properties": {
          "key": { "type": "keyword" },
          "value": { "type": "text", "analyzer": "custom_ngram_analyzer" }
        }
      },
      "duracao": { "type": "integer" },
      "favoritados": { "type": "keyword" },
      "gravador": { "type": "keyword" },
      "gravadoresAutocomplete": {
        "type": "completion",
        "analyzer": "standard",
        "preserve_separators": true,
        "preserve_position_increments": true,
        "max_input_length": 50
      },
      "hora": { "type": "long" },
      "id": { "type": "keyword" },
      "locutorDois": {
        "properties": {
          "key": { "type": "keyword" },
          "value": { "type": "text", "analyzer": "custom_ngram_analyzer" }
        }
      },
      "locutorUm": {
        "properties": {
          "key": { "type": "keyword" },
          "value": { "type": "text", "analyzer": "custom_ngram_analyzer" }
        }
      },
      "locutores": { "type": "keyword" },
      "locutoresAutocomplete": {
        "type": "completion",
        "analyzer": "standard",
        "preserve_separators": true,
        "preserve_position_increments": true,
        "max_input_length": 50
      },
      "nota": { "type": "float" },
      "ouvintes": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword", "ignore_above": 256 }
        },
        "analyzer": "custom_ngram_analyzer"
      },
      "ramal": { "type": "integer" },
      "shorticut": { "type": "text", "analyzer": "custom_ngram_analyzer" },
      "tags": {
        "type": "keyword",
        "fields": {
          "text": { "type": "text", "analyzer": "custom_ngram_analyzer" }
        }
      },
      "tagsAutocomplete": {
        "type": "completion",
        "analyzer": "standard",
        "preserve_separators": true,
        "preserve_position_increments": true,
        "max_input_length": 50
      },
      "temComentario": { "type": "boolean" },
      "transcricao": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword", "ignore_above": 256 }
        },
        "analyzer": "custom_ngram_analyzer"
      },
      "transcricaoAudio": {
        "properties": {
          "speakers": {
            "properties": {
              "endTime": { "type": "long" },
              "endTimeString": {
                "type": "text",
                "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } },
                "analyzer": "custom_ngram_analyzer"
              },
              "name": {
                "type": "text",
                "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } },
                "analyzer": "custom_ngram_analyzer"
              },
              "startTime": { "type": "long" },
              "startTimeString": {
                "type": "text",
                "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } }
              },
              "text": {
                "type": "text",
                "fields": { "keyword": { "type": "keyword", "ignore_above": 256 } },
                "analyzer": "custom_ngram_analyzer"
              }
            }
          }
        }
      }
    }
  }
}
EOF

log "Verificando o estado do domínio OpenSearch '$DOMAIN_NAME'..."
DOMAIN_PROCESSING="$(aws opensearch describe-domain \
  --domain-name "$DOMAIN_NAME" \
  --region "$AWS_DEFAULT_REGION" \
  --query 'DomainStatus.Processing' \
  --output text 2>/dev/null || true)"

if [[ -z "$DOMAIN_PROCESSING" || "$DOMAIN_PROCESSING" == "None" ]]; then
  DOMAIN_PROCESSING="$(aws es describe-elasticsearch-domain \
    --domain-name "$DOMAIN_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'DomainStatus.Processing' \
    --output text 2>/dev/null || true)"
fi

[[ -n "$DOMAIN_PROCESSING" && "$DOMAIN_PROCESSING" != "None" ]] || \
  fail "Não foi possível consultar o domínio OpenSearch: $DOMAIN_NAME"
[[ "$DOMAIN_PROCESSING" == "False" || "$DOMAIN_PROCESSING" == "false" ]] || \
  fail "O domínio OpenSearch ainda está processando alterações: $DOMAIN_NAME"

log "Verificando se o índice '$INDEX_NAME' já existe..."
if aws opensearch get-index \
  --domain-name "$DOMAIN_NAME" \
  --index-name "$INDEX_NAME" \
  --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1; then
  log "O índice '$INDEX_NAME' já existe. Nenhuma ação necessária."
  exit 0
fi

log "Criando o índice '$INDEX_NAME' via AWS CLI..."
aws opensearch create-index \
  --domain-name "$DOMAIN_NAME" \
  --index-name "$INDEX_NAME" \
  --index-schema "file://$SCHEMA_FILE" \
  --region "$AWS_DEFAULT_REGION" >/dev/null

log "Índice '$INDEX_NAME' criado com sucesso."
