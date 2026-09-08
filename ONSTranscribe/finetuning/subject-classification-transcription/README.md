# Subject Classification for Transcriptions

Pipeline de classificação multilabel de transcrições de áudio usando embeddings (OpenAI), TF-IDF, SVM/LightGBM e abordagem via LLM (qualquer provider).

---

## Visão Geral

O repositório contém dois notebooks complementares:

| Notebook | Abordagem | Quando usar |
|---|---|---|
| `notebooks/classification_model.ipynb` | Embeddings + TF-IDF + SVM/LightGBM | Datasets maiores, maior controle, inferência offline |
| `notebooks/pure_LLM.ipynb` | Prompt engineering + LLM | Prototipagem rápida, zero-shot, sem necessidade de treino |

---

## Estrutura de Diretórios

```
subject-classification-transcription/
├── notebooks/
│   ├── classification_model.ipynb   # Pipeline ML clássico
│   ├── pure_LLM.ipynb               # Classificação via LLM
│   └── openai_embedding_transformer.py  # Transformer sklearn para embeddings OpenAI
├── truths/
│   └── sample_data.csv              # Exemplo mínimo do formato esperado
├── models/                          # Modelos treinados (não versionados)
├── pyproject.toml
└── README.md
```

> **Nota:** Os diretórios `truths/` (exceto `sample_data.csv`), `models/` e `notebooks/embeddings_save/` estão no `.gitignore` — nunca commite dados reais ou modelos treinados.

---

## Formato do Dataset

O CSV de entrada deve conter ao menos estas colunas:

| Coluna | Tipo | Descrição |
|---|---|---|
| `audios` | string | Identificador do arquivo de áudio (ex: `audio_001.wav`) |
| `transcription` | string | Texto transcrito do áudio |
| `Assunto tratado` | string/lista | Labels no formato `"['Categoria A', 'Categoria B']"` |
| `num_labels` | int | Quantidade de labels (opcional, gerado automaticamente) |

Veja `truths/sample_data.csv` para um exemplo completo com 10 registros fictícios.

---

## Configuração do Ambiente

### 1. Instalar dependências

```bash
# Instale o ambiente base
uv sync

# Escolha o provider LLM que você vai usar (para pure_LLM.ipynb):
uv pip install 'subject-classification[bedrock]'    # AWS Bedrock
uv pip install 'subject-classification[openai]'     # OpenAI
uv pip install 'subject-classification[anthropic]'  # Anthropic
```

### 2. Configurar variáveis de ambiente

Crie um arquivo `.env` na raiz (não commitar) ou exporte as variáveis:

```bash
# Caminhos dos seus dados
export TRAINING_CSV=../truths/seu_dataset_treino.csv
export FULL_DATASET_CSV=../truths/seu_dataset_completo.csv
export VALIDATION_CSV=../truths/seu_dataset_validacao.csv

# Colunas de nomes próprios para extrair stopwords (opcional)
# Deixe vazio se o seu dataset não tiver essas colunas
export NAME_COLUMNS=coluna_nome_agente,coluna_nome_operador

# Categoria catch-all (mutuamente exclusiva)
export CATCHALL_CATEGORY="Outros assuntos"

# Diretórios de saída
export EMBEDDINGS_DIR=notebooks/embeddings_save/
export MODELS_DIR=../models/

# Threshold padrão de classificação
export DEFAULT_THRESHOLD=0.5

# Para classification_model.ipynb — chave OpenAI (necessária para embeddings)
export OPENAI_API_KEY=sk-...

# Para pure_LLM.ipynb — escolha o provider
export LLM_PROVIDER=anthropic   # bedrock | openai | anthropic
export LLM_MODEL=claude-sonnet-4-6
export ANTHROPIC_API_KEY=sk-ant-...
# ou, para AWS Bedrock, configure as credenciais AWS normalmente
# ou, para OpenAI:
# export LLM_MODEL=gpt-4o
# export OPENAI_API_KEY=sk-...
```

---

## Como usar — `classification_model.ipynb`

1. **Prepare seu dataset** no formato descrito acima
2. **Configure as variáveis** na célula de configuração no topo do notebook
3. **Defina as categorias** do seu domínio — elas serão aprendidas automaticamente dos labels no CSV
4. **Colunas de nomes próprios** (opcional): se o seu dataset tiver colunas com nomes de pessoas que aparecem nas transcrições, informe-as em `NAME_COLUMNS` para que sejam adicionadas às stopwords do TF-IDF
5. Execute as células em ordem:
   - *Data preparation* → carrega e filtra o dataset
   - *Stop words* → extrai nomes próprios para stopwords
   - *Pipeline* → cria o ColumnTransformer (Embeddings OpenAI + TF-IDF)
   - *Separação dataset* → split estratificado multilabel
   - *Criação dos embeddings* → gera e salva features (cache)
   - *Model* → escolha entre LightGBM, LinearSVC ou SVC
   - *Pipeline training* → treina e avalia
   - *Salva modelo* → persiste em `MODELS_DIR`

### Calibração de thresholds

Após treinar, analise as métricas por classe e ajuste os thresholds na célula de validação:

```python
thresholds = {
    'CLASSE_RARA':    0.2,   # baixo recall → reduz threshold
    'CLASSE_FREQUENTE': 0.7, # alta precisão → aumenta threshold
}
```

---

## Como usar — `pure_LLM.ipynb`

1. **Configure o domínio e as categorias** na célula 0:
   - `DOMAIN_DESCRIPTION`: descrição do tipo de chamada/texto
   - `VALID_CATEGORIES`: lista das suas categorias
   - `CATEGORY_DESCRIPTIONS`: palavras-chave e contexto para cada categoria
2. **Configure o provider LLM** na célula 3 via variável `LLM_PROVIDER`
3. Execute o notebook — o prompt é gerado dinamicamente pela função `build_prompt()`

### Exemplo de customização do prompt

```python
VALID_CATEGORIES = ['Triagem Técnica', 'Escalamento', 'Resolução', 'Outros']

CATEGORY_DESCRIPTIONS = {
    'Triagem Técnica': {
        'keywords': ['erro', 'falha', 'problema técnico', 'não funciona'],
        'context': 'Chamadas de suporte técnico com relato de problema.'
    },
    'Escalamento': {
        'keywords': ['transferir', 'supervisor', 'segundo nível'],
        'context': 'Chamadas que precisam ser escaladas para outro time.'
    },
    ...
}
```

---

## Como adaptar para um novo domínio

1. **Dataset**: prepare um CSV com transcrições e labels no formato descrito
2. **Categorias**: defina as categorias relevantes para o seu contexto
3. **Stopwords**: adicione termos recorrentes mas sem valor semântico (nomes, siglas internas)
4. **Thresholds**: calibre por classe após avaliar precision/recall no conjunto de validação
5. **Prompt**: para a abordagem LLM, detalhe palavras-chave e contexto de cada categoria

---

## Componentes

### `openai_embedding_transformer.py`

Transformer sklearn compatível com `Pipeline` e `ColumnTransformer` que chama a API de embeddings da OpenAI. Suporta:
- Qualquer modelo de embedding da OpenAI (`text-embedding-3-small`, `text-embedding-3-large`, etc.)
- Processamento em batches
- Normalização L2 opcional
- Serialização com joblib (remove o cliente antes de serializar)
