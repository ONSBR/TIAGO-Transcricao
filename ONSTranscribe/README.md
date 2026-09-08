# ONSTranscribe — Pipeline de Transcrição de Áudio com ML e GPU

Pipeline completo de transcrição automática de áudio em português brasileiro, com diarização de locutores, enriquecimento via LLM (Amazon Bedrock) e indexação em OpenSearch. Projetado para operar em escala sobre arquivos `.wav` armazenados em S3, processando-os de ponta a ponta de forma assíncrona e sem intervenção manual.

---

## Índice

1. [Visão Geral](#1-visão-geral)
2. [Arquitetura da Solução](#2-arquitetura-da-solução)
3. [Fluxo de Transcrição e Pós-Processamento](#3-fluxo-de-transcrição-e-pós-processamento)
4. [Requisitos de Infraestrutura](#4-requisitos-de-infraestrutura)
5. [Início Rápido](#5-início-rápido)
6. [Configuração Detalhada](#6-configuração-detalhada)
7. [Personalização do LLM](#7-personalização-do-llm)
8. [Diretrizes de Segurança e Proteção de Dados](#8-diretrizes-de-segurança-e-proteção-de-dados)
9. [Estrutura do Repositório](#9-estrutura-do-repositório)
10. [Operação](#10-operação)

---

## 1. Visão Geral

### O que é

ONSTranscribe é uma solução de transcrição de áudio orientada a eventos. Quando um arquivo `.wav` é depositado em um bucket S3, o pipeline é acionado automaticamente e produz:

- **Transcrição** segmentada por locutor (diarização)
- **Resumo** da conversa gerado por LLM
- **Entidades-chave** extraídas (pessoas, organizações, localidades, documentos)
- **Classificação de assunto** da chamada
- **Glossário técnico** aplicado como pós-correção
- **Documento indexado** no OpenSearch para busca e análise

### Casos de uso

- Centros de controle com alto volume de chamadas gravadas
- Call centers que precisam de busca e auditoria sobre conversas
- Qualquer operação que produza arquivos `.wav` e precise de transcrição automática com metadados

### Tecnologias principais

| Camada                       | Tecnologia                                                                                                                                                                                            |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ASR (Reconhecimento de Fala) | [OpenAI Whisper](https://github.com/openai/whisper) — base: [`openai/whisper-medium`](https://huggingface.co/openai/whisper-medium) (fine-tunable)                                                       |
| Diarização                 | [PyAnnote Audio](https://github.com/pyannote/pyannote-audio) — pipeline: [`fatymatariq/speaker-diarization-3.1`](https://huggingface.co/fatymatariq/speaker-diarization-3.1) (segmentation fine-tunable) |
| Alinhamento forçado         | Wav2Vec2 —[`jonatasgrosman/wav2vec2-large-xlsr-53-portuguese`](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-portuguese)                                                               |
| Classificação de Assunto   | SVM com embeddings TF-IDF (fine-tunable)                                                                                                                                                              |
| Enriquecimento LLM           | Amazon Bedrock (Claude)                                                                                                                                                                               |
| API de Transcrição         | FastAPI + CUDA (container ECS)                                                                                                                                                                        |
| Infraestrutura               | Terraform + AWS (ECS, Lambda, SQS, SNS, ALB, DynamoDB, OpenSearch)                                                                                                                                    |

---

## 2. Arquitetura da Solução

### Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────────┐
│  VPC (subnets privadas + públicas)                                  │
│                                                                     │
│  ┌─────────────┐     ┌─────────────────────────────────────────┐   │
│  │  S3 Bucket  │     │  Camada de Evento                       │   │
│  │  ─────────  │────>│  SNS Topic ──> Lambda: audio_to_search  │   │
│  │  .wav input │     │                   │                     │   │
│  │  .json saída│<────│                   ▼                     │   │
│  └─────────────┘     │            SQS FIFO Queue               │   │
│         ▲            │                   │                     │   │
│         │            │                   ▼                     │   │
│         │            │  Lambda: create_transcribe_job          │   │
│         │            └───────────────────┼─────────────────────┘   │
│         │                                │                         │
│         │            ┌───────────────────▼─────────────────────┐   │
│         │            │  Camada de Processamento (ECS GPU)      │   │
│         │            │                                         │   │
│         │            │  ALB (interno) ──> FastAPI + CUDA       │   │
│         │            │                   ├── Whisper (ASR)     │   │
│         │            │                   ├── PyAnnote (diarz.) │   │
│         │            │                   ├── Classificador     │   │
│         │            │                   └── Bedrock (LLM)    │   │
│         │            └───────────────────┬─────────────────────┘   │
│         └──────────────── .json result ──┘                         │
│                                                                     │
│  ┌─────────────┐     ┌─────────────────────────────────────────┐   │
│  │  OpenSearch │<────│  Lambda: audio_to_search (2ª invocação) │   │
│  │  Index      │     │  Trigger: .json salvo no S3             │   │
│  └─────────────┘     └─────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────┐     ┌─────────────────┐                           │
│  │  DynamoDB   │     │  Bedrock Prompts│                           │
│  │  (jobs)     │     │  (6 prompts)    │                           │
│  └─────────────┘     └─────────────────┘                           │
└─────────────────────────────────────────────────────────────────────┘
```

### Componentes AWS

| Componente                                           | Tipo             | Responsabilidade                                                                     |
| ---------------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------ |
| **S3 Bucket**                                  | Storage          | Armazena áudios `.wav` de entrada e JSONs de saída                               |
| **SNS Topic**                                  | Mensageria       | Recebe notificações S3 e distribui eventos                                         |
| **Lambda: transcribe_audio_to_elastic_search** | Serverless       | Roteador de eventos: detecta `.wav` (enfileira) e `.json` (indexa no OpenSearch) |
| **SQS FIFO**                                   | Fila             | Garante processamento ordenado e sem duplicatas                                      |
| **Lambda: create_transcribe_job_via_api**      | Serverless       | Consome a fila e chama a FastAPI via ALB                                             |
| **ALB (interno)**                              | Rede             | Balanceador interno que roteia requisições para o ECS                              |
| **ECS + FastAPI**                              | Computação GPU | Container com Whisper, PyAnnote, classificador e integração Bedrock                |
| **Amazon Bedrock**                             | LLM              | Gera resumo, entidades, classificação e aplica glossário                          |
| **OpenSearch**                                 | Busca            | Armazena e indexa as transcrições para consulta                                    |
| **DynamoDB**                                   | Banco NoSQL      | Controla o estado dos jobs de transcrição                                          |
| **CloudWatch**                                 | Observabilidade  | Logs de todos os componentes                                                         |

---

## 3. Fluxo de Transcrição e Pós-Processamento

### Fase 1 — Ingestão (evento de áudio)

```
S3 (.wav) → SNS → Lambda (transcribe_audio_to_elastic_search)
    ├── Cria documento inicial no OpenSearch com metadados do arquivo
    └── Envia mensagem para SQS FIFO com a chave do áudio
```

**Convenção de nomenclatura obrigatória do arquivo `.wav`:**

```
YYYYMMDD_HHMMSS_RAMAL_OPERADOR_DURACAO_DIRECAO_AGENTE.wav

Exemplo: 20240115_100000_1000_operador01_180_I_agente01.wav

Campos:
  YYYYMMDD   → data da gravação
  HHMMSS     → horário
  RAMAL      → identificador do ramal/extensão
  OPERADOR   → identificador do operador do centro de controle
  DURACAO    → duração em segundos
  DIRECAO    → I (inbound: agente externo liga para o centro)
              → O (outbound: centro liga para o agente externo)
  AGENTE     → identificador do agente externo
```

> Arquivos fora desse padrão causam `IndexError` na Lambda e são ignorados silenciosamente. Adapte o padrão conforme sua organização no código da Lambda.

### Fase 2 — Processamento (ECS GPU)

```
SQS → Lambda (create_transcribe_job_via_api) → ALB → FastAPI (ECS)
    ├── 1. Download do .wav do S3
    ├── 2. Transcrição com Whisper (ASR)
    ├── 3. Diarização com PyAnnote (identificação de locutores)
    ├── 4. Alinhamento transcrição × diarização
    ├── 5. Classificação de assunto (SVM)
    ├── 6. Enriquecimento LLM via Bedrock:
    │       ├── run_beautify: correção ortográfica/contextual
    │       ├── summary: resumo da conversa
    │       ├── key_entities: extração de entidades
    │       └── get_audio_subject: classificação temática
    └── 7. Salva JSON resultado no S3
```

### Fase 3 — Indexação (evento de JSON)

```
S3 (.json) → SNS → Lambda (transcribe_audio_to_elastic_search)
    └── Atualiza documento no OpenSearch com transcrição completa + metadados LLM
```

### Estrutura do JSON de saída

```json
{
  "transcricaoAudio": {
    "transcription_start": "2024-01-15T10:00:00.000Z",
    "transcription_end": "2024-01-15T10:03:00.000Z",
    "transcription_processing_time": 45.2,
    "speakers": [
      {
        "name": "spk_0",
        "startTime": 0.0,
        "endTime": 12.5,
        "text": "Transcrição do locutor 0..."
      }
    ],
    "key_entities": {
      "raw": "{ ... JSON extraído pelo LLM ... }",
      "assunto_do_audio": ["Categoria A", "Categoria B"]
    },
    "summary": "Resumo gerado pelo LLM..."
  }
}
```

---

## 4. Requisitos de Infraestrutura

### Ferramentas locais

| Ferramenta | Versão mínima | Uso                               |
| ---------- | --------------- | --------------------------------- |
| AWS CLI    | v2              | Deploy e operação               |
| Terraform  | >= 1.5          | Provisionamento de infraestrutura |
| Docker     | >= 24           | Build da imagem FastAPI/CUDA      |
| Python     | >= 3.9          | Scripts de setup e Lambda         |

### Conta AWS

| Serviço              | Motivo                                         |
| --------------------- | ---------------------------------------------- |
| ECS (EC2 launch type) | Container GPU para processamento               |
| EC2 `g4dn.xlarge`   | GPU NVIDIA T4 para Whisper e PyAnnote          |
| Lambda                | Funções de evento e integração             |
| S3                    | Armazenamento de áudios, modelos e resultados |
| SQS, SNS              | Mensageria assíncrona                         |
| DynamoDB              | Estado dos jobs                                |
| OpenSearch            | Indexação e busca das transcrições         |
| Amazon Bedrock        | Modelo LLM (Claude) para enriquecimento        |
| ECR                   | Repositório da imagem Docker                  |
| CloudWatch            | Logs e métricas                               |
| IAM                   | Roles e policies                               |

**Quota de EC2 necessária:** mínimo 4 vCPUs G-type (`g4dn.xlarge`) na região de deploy.

Verificar:

```bash
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA \
  --region <SUA_REGIAO> \
  --profile <SEU_PROFILE> \
  --query 'Quota.Value' \
  --output text
```

### Modelos ML (pré-requisito de deploy)

Os modelos devem estar no S3 **antes** do primeiro deploy. O container os baixa no startup a partir do S3.

#### Modelos utilizados

| Modelo                                | Link Hugging Face                                                                                                                                                                                       | Caminho S3 (configurável)                                       |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| **Whisper (ASR)**               | ajustado: [`onsdevops/tiago-transcricao`](https://huggingface.co/onsdevops/tiago-transcricao) · base: [`openai/whisper-medium`](https://huggingface.co/openai/whisper-medium)                                                                          | `s3://<bucket>/<transcribe_s3_model_path>`                     |
| **Diarização (segmentation)** | ajustado: [`onsdevops/tiago-transcricao`](https://huggingface.co/onsdevops/tiago-transcricao) (subpasta `segmentation/`) · pipeline: [`fatymatariq/speaker-diarization-3.1`](https://huggingface.co/fatymatariq/speaker-diarization-3.1) · base: [`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0) | `s3://<bucket>/<transcribe_s3_segmentation_model_path>`        |

---

#### Passo 0 — Criar o workspace local

Todos os downloads e treinamentos a seguir devem ser executados **dentro de `finetuning/workspace/`** (pasta gitignored). Gere o workspace a partir dos templates antes de qualquer outra etapa:

```bash
# Na raiz do repositório:
bash finetuning/scripts/setup_workspace.sh
```

O script cria a estrutura abaixo em `finetuning/workspace/` (gitignored — nunca vai ao repositório):

```
finetuning/workspace/
├── finetune_whisper/
│   └── finetune_whisper.ipynb
├── finetune_pyannote-diarization/
│   ├── 1_dataset-preparation.ipynb
│   ├── 2_diarization-fine-tuning.ipynb
│   ├── 3_model-tests.ipynb
│   └── 4_model_upload.ipynb
└── subject-classification/
    ├── classification_model.ipynb
    └── pure_LLM.ipynb
```

---

#### Passo 1 — Baixar os modelos do Hugging Face

> **Pré-requisito:** instalar a CLI do HuggingFace e autenticar com seu token (necessário para modelos com acesso restrito como `pyannote/segmentation-3.0`).
>
> Em sistemas Debian/Ubuntu modernos (incluindo WSL), use `uv tool install` em vez de `pip install` para evitar o erro `externally-managed-environment`.

```bash
# Instalar a CLI do HuggingFace (usa uv, que já é dependência do projeto)
uv tool install huggingface_hub

# Autenticar — gere seu token em: https://huggingface.co/settings/tokens
hf auth login
```

##### Whisper — [`openai/whisper-medium`](https://huggingface.co/openai/whisper-medium)

```bash
# Baixar para dentro do workspace (criado no Passo 0)
hf download openai/whisper-medium \
  --local-dir finetuning/workspace/models/whisper
```

> Alternativa via Python:
>
> ```python
> from huggingface_hub import snapshot_download
> snapshot_download(repo_id="openai/whisper-medium",
>                   local_dir="finetuning/workspace/models/whisper")
> ```

Se for usar a versão base **sem fine-tuning**, faça o upload desta pasta para o S3 (Passo 3). Se for fazer fine-tuning, use o notebook `workspace/finetune_whisper/finetune_whisper.ipynb` — o modelo resultante é salvo em `workspace/finetune_whisper/whisper_model_finetuned/`.

##### Diarização — segmentation model: [`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0)

O que vai para o S3 é o **segmentation model** (não o pipeline inteiro). O pipeline base `fatymatariq/speaker-diarization-3.1` substitui seu segmentation model pelo modelo carregado do S3 em runtime.

```bash
# 1. Aceitar os termos de uso do modelo em:
#    https://huggingface.co/pyannote/segmentation-3.0
#    (login HuggingFace necessário)

# 2. Baixar para dentro do workspace
hf download pyannote/segmentation-3.0 \
  --local-dir finetuning/workspace/models/segmentation
```

Se for usar a versão base **sem fine-tuning**, suba esta pasta para o S3. Se for fazer fine-tuning, use os notebooks `workspace/finetune_pyannote-diarization/` (fluxo: 1 → 2 → 3 → 4).

> **Formato esperado:** o código carrega o modelo via `SegmentationModel.from_pretrained()` do HuggingFace, que espera `config.json` + `model.safetensors` no diretório local. O download do `pyannote/segmentation-3.0` entrega o formato PyAnnote nativo (`config.yaml` + `pytorch_model.bin`). Se o download não produzir `model.safetensors`, converta com o script abaixo:
>
> ```bash
> python finetuning/scripts/convert_segmentation_to_hf_format.py \
>   --model-dir finetuning/workspace/models/segmentation
> ```
>
> O script lê `config.yaml` e `pytorch_model.bin` e gera `config.json` + `model.safetensors` no mesmo diretório.

---

#### Passo 2 (opcional) — Treinar o classificador de assuntos

> **Não faz parte do pipeline atual.** O assunto do áudio é resolvido por LLM no Bedrock,
> em `llm_service.get_audio_subject()`, com o prompt de `prompts/get_audio_subject`. A API
> não carrega classificador nenhum, e o Terraform não provisiona caminho para ele.
>
> O material abaixo permanece porque a abordagem é uma alternativa válida — um SVM local
> custa menos por áudio que uma chamada de LLM e não depende de rede. Siga esta seção só se
> for reintroduzir esse caminho, e note que aí é preciso religar o carregamento do modelo na
> API, que foi removido.

O classificador é o único modelo que **não existe no HuggingFace** — ele precisa ser treinado com as transcrições e categorias da sua organização. O resultado são dois arquivos: `classifier_model.joblib` e `classes.npy`.

> **Atenção ao publicar esse artefato.** O `TfidfVectorizer` guarda o vocabulário aprendido
> **em texto claro** dentro do `.joblib`. Treinado sobre transcrições reais, o arquivo passa
> a conter fragmentos literais das conversas — inclusive nomes próprios e identificadores.
> Trate o `.joblib` com o mesmo cuidado que as transcrições de origem: não versione e não
> publique.

**Notebook:** `finetuning/workspace/subject-classification/classification_model.ipynb`

##### 2.1 — Preparar o dataset de treino

Crie um CSV com transcrições rotuladas e salve em `finetuning/data/` (gitignored):

```csv
transcription,label
"texto da transcrição aqui","Categoria A"
"outro trecho de áudio","Categoria B"
```

##### 2.2 — Instalar dependências

```bash
cd finetuning/workspace/subject-classification
uv sync
```

##### 2.3 — Configurar variáveis de ambiente

```bash
export TRAINING_CSV=/caminho/para/dataset_labels.csv
export VALIDATION_CSV=/caminho/para/dataset_validation.csv  # opcional
export CATCHALL_CATEGORY="Outros assuntos"   # categoria quando nenhuma se aplica
export DEFAULT_THRESHOLD=0.5                  # limiar mínimo de confiança
```

##### 2.4 — Executar o notebook

```bash
uv run jupyter notebook classification_model.ipynb
```

Execute todas as células. O notebook salva os artefatos em `finetuning/workspace/subject-classification/`:

| Arquivo                     | Descrição                  |
| --------------------------- | ---------------------------- |
| `classifier_model.joblib` | Modelo SVM treinado          |
| `classes.npy`             | Lista de categorias (labels) |

> **Sem dataset anotado?** Use `pure_LLM.ipynb` para gerar rótulos via LLM (zero-shot/few-shot) e depois treine o SVM com eles. Configure `export LLM_PROVIDER=bedrock` para usar o AWS Bedrock.

---

#### Passo 3 — Upload dos modelos para o S3

Com os artefatos em `finetuning/workspace/`, suba-os para o S3:

```bash
# Whisper (base ou fine-tuned)
aws s3 sync finetuning/workspace/models/whisper/ \
  s3://<BUCKET>/<WHISPER_PATH>/ --profile <PROFILE>

# Segmentation (base ou fine-tuned)
aws s3 sync finetuning/workspace/models/segmentation/ \
  s3://<BUCKET>/<SEGMENTATION_PATH>/ --profile <PROFILE>

```

> Os valores `<WHISPER_PATH>` e `<SEGMENTATION_PATH>` devem coincidir com `transcribe_s3_model_path` e `transcribe_s3_segmentation_model_path` no `terraform.tfvars`.

Para instruções completas de fine-tuning (Whisper e PyAnnote), consulte `finetuning/README.md`.

### Acesso ao modelo Bedrock

Habilitar o modelo desejado em: **Console AWS → Amazon Bedrock → Model access**

---

## 5. Início Rápido

```bash
# 1. Clonar o repositório
git clone https://github.com/ONSBR/TIAGO-Transcricao.git
cd TIAGO-Transcricao/ONSTranscribe

# 2. Copiar e preencher o arquivo de ambiente
cp scripts/Montagem_Ambiente/.env.sample scripts/Montagem_Ambiente/.env
# Editar .env com os valores do seu ambiente

# 3. Copiar e preencher o terraform.tfvars
cp api/terraform/terraform.tfvars.sample api/terraform/terraform.tfvars
# Editar terraform.tfvars com os valores da sua infraestrutura AWS

# 4. Criar os arquivos de prompts a partir dos exemplos
cd api/terraform/prompts
for f in *.example.txt; do cp "$f" "${f%.example.txt}.txt"; done
cd ../../..
# Editar cada .txt com os prompts da sua organização (ver Seção 7)

# 5. Gerar o layer de métricas
pip install jiwer -t /tmp/metricas_layer/python
cd /tmp/metricas_layer && zip -r metricas_layer.zip python/
cp /tmp/metricas_layer/metricas_layer.zip \
   api/terraform/lambdas/layers/metricas_layer.zip
cd -

# 6. Fazer login AWS
aws sso login --profile <SEU_PROFILE>

# 7. Executar o deploy
source scripts/Montagem_Ambiente/.env
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

Consulte o `Guia_Deploy_Novo_Ambiente.md` para o procedimento completo com detalhes de
cada etapa, e o `README.md` de `scripts/Montagem_Ambiente/` para o estado atual da esteira.

---

## 6. Configuração Detalhada

### 6.1 Arquivo de ambiente (.env)

Copiar de `scripts/Montagem_Ambiente/.env.sample` e preencher:

| Variável                     | Descrição                                  | Exemplo                                                                     |
| ----------------------------- | -------------------------------------------- | --------------------------------------------------------------------------- |
| `AWS_PROFILE`               | Profile AWS configurado localmente           | `meu-profile`                                                             |
| `AWS_DEFAULT_REGION`        | Região de deploy                            | `us-east-1`                                                               |
| `ONST_ROOT`                 | Caminho absoluto até a raiz do repositório | `/home/user/ONSTranscribe`                                                |
| `ONST_API_DIR`              | Caminho até `api/`                        | `/home/user/ONSTranscribe/api`                                            |
| `ONST_TERRAFORM_SOURCE_DIR` | Caminho até `api/terraform/`              | `/home/user/ONSTranscribe/api/terraform`                                  |
| `ONST_TERRAFORM_WORKDIR`    | Onde o workspace gerado será criado         | `/home/user/ONSTranscribe/scripts/Montagem_Ambiente/.generated/terraform` |
| `TFVARS_FILE`               | Caminho absoluto até o `terraform.tfvars` | `/home/user/ONSTranscribe/api/terraform/terraform.tfvars`                 |
| `TF_STATE_BUCKET`           | Bucket S3 para o Terraform state             | `meu-bucket-terraform-state`                                              |
| `TF_STATE_KEY`              | Chave do arquivo de state no bucket          | `transcricao/transcribe.tfstate`                                          |
| `GPU_ECS_AMI_ID`            | AMI ECS-optimized GPU para a região         | Ver instrução abaixo                                                      |
| `ECS_CLUSTER_NAME`          | Nome do cluster ECS (gerado pelo Terraform)  | `meu-projeto-cluster-dev`                                                 |
| `GPU_INSTANCE_NAME`         | Tag Name da instância GPU                   | `meu-projeto-gpu-node-dev`                                                |

**Obter AMI GPU para a região:**

```bash
aws ssm get-parameters \
  --names /aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended \
  --region <SUA_REGIAO> \
  --query 'Parameters[0].Value' | python3 -m json.tool
# Copiar o campo "image_id"
```

### 6.2 Terraform (terraform.tfvars)

Copiar de `api/terraform/terraform.tfvars.sample`. Variáveis críticas:

| Variável                    | Descrição                                                        |
| ---------------------------- | ------------------------------------------------------------------ |
| `project_name`             | Prefixo de todos os recursos AWS. Máx 20 caracteres.              |
| `environment`              | `dev`, `hmg` ou `prd`                                        |
| `vpc_id`                   | ID da VPC onde a solução será implantada                        |
| `private_subnet_ids`       | Subnets privadas (ECS tasks, Lambdas) — mínimo 2 AZs             |
| `public_subnet_ids`        | Subnets públicas (ALB) — mínimo 2 AZs                           |
| `elastic_search_uri`       | Endpoint HTTPS do OpenSearch (preenchido após bootstrap)          |
| `transcribe_bedrock_model` | Model ID do Claude no Bedrock                                      |
| `recorders_list`           | Lista de gravadores/localidades no formato `COD\|Nome_COD2\|Nome2` |

> O `terraform.tfvars` com valores reais **nunca deve ser commitado**. Está no `.gitignore` por padrão.

---

## 7. Personalização do LLM

Esta é a seção mais importante para adaptar a solução à sua organização.

### 7.1 Modelo Bedrock

O modelo usado para enriquecimento é configurado **em um único lugar** no `terraform.tfvars`, e propagado automaticamente para todos os 6 prompts Bedrock via variável Terraform:

**`api/terraform/terraform.tfvars`:**

```hcl
transcribe_bedrock_model = "anthropic.claude-3-5-sonnet-20240620-v1:0"
```

O `prompts.tf` usa `var.transcribe_bedrock_model` em todos os blocos `variant`. Alterar o valor no `tfvars` é suficiente.

### 7.2 Prompts

Os 6 prompts controlam todo o comportamento do LLM. Estão em `api/terraform/prompts/` como arquivos `.txt` (gitignored — criados a partir dos `.example.txt`).

| Arquivo                     | Função                                             | O que personalizar                                                                |
| --------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------- |
| `run_beautify_system.txt` | Perfil do assistente de correção                   | Setor da organização, vocabulário técnico                                     |
| `run_beautify.txt`        | Regras de correção ortográfica e contextual       | Siglas do domínio, nomes de localidades, regras de formatação de horário/data |
| `technical_glossary.txt`  | Glossário técnico injetado no prompt de correção | Siglas e termos específicos do seu setor                                         |
| `summary.txt`             | Instrução de resumo                                | Contexto organizacional, tipo de conversa esperada                                |
| `key_entities.txt`        | Schema de extração de entidades                    | Tipos de entidade relevantes (ex: número de chamado, equipamento, localidade)    |
| `get_audio_subject.txt`   | Categorias de classificação de assunto             | Lista de categorias temáticas do seu domínio                                    |

**Fluxo de criação dos prompts:**

```bash
cd api/terraform/prompts

# Criar a partir dos exemplos
for f in *.example.txt; do
  cp "$f" "${f%.example.txt}.txt"
done

# Editar cada arquivo com os dados da sua organização
# Os .example.txt servem como template com instruções inline
```

**Como os prompts são usados no código:**

Os IDs dos prompts criados pelo Terraform são injetados como variáveis de ambiente no container ECS:

```
GLOSSARY_PROMPT_ID         → usado em: api/app/services/llm_service.py
RUN_BEAUTIFY_PROMPT_ID     → usado em: api/app/services/llm_service.py
RUN_BEAUTIFY_SYSTEM_PROMPT_ID → usado em: api/app/services/llm_service.py
KEY_ENTITIES_PROMPT_ID     → usado em: api/app/services/llm_service.py
SUMMARY_PROMPT_ID          → usado em: api/app/services/llm_service.py
SUBJECT_PROMPT_ID          → usado em: api/app/services/llm_service.py
```

Para desenvolvimento local, defina esses IDs no `.env` da API após o primeiro deploy (os IDs são gerados pelo Bedrock e exibidos no output do Terraform).

### 7.3 Correções de texto customizadas

O arquivo `api/app/utils/text_corrections.py` contém regras de pós-processamento específicas do domínio (correções de palavras mal transcritas pelo Whisper no vocabulário do seu setor). **Este arquivo não está versionado** — deve ser criado a partir do template:

```bash
cp api/app/utils/text_corrections_example.py \
   api/app/utils/text_corrections.py
```

Editar `text_corrections.py` adicionando as correções específicas da sua organização:

```python
# Exemplo: corrigir erros comuns do Whisper no vocabulário do seu setor
CORRECTIONS = {
    "mega watts": "megawatts",
    "sub estação": "subestação",
    "kilo volts": "kilovolts",
    # Adicionar as correções do seu domínio aqui
}
```

> O `text_corrections.py` **nunca deve ser commitado** — contém vocabulário proprietário da organização. Está no `.gitignore` por padrão.

### 7.4 Modelos de ML — ASR, Diarização e Alinhamento

O pipeline usa três modelos de ML do Hugging Face, todos configuráveis via variáveis de ambiente no container ECS.

#### Whisper (ASR)

| Propriedade              | Valor padrão                                                                                                                      |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| Modelo base (HF)         | [`openai/whisper-medium`](https://huggingface.co/openai/whisper-medium)                                                             |
| Variável de env         | `WHISPER_BASE_MODEL`                                                                                                             |
| Como é usado            | O `AutoProcessor` e a config de arquitetura são carregados do HF; os pesos do modelo são carregados do S3 (arquivo fine-tuned) |
| Variável de env (pesos) | `MODEL_WHISPER_LOCAL_PATH` → caminho local após download do S3                                                                 |

O modelo base define apenas o tokenizer e a arquitetura. O fine-tuning pode ser trocado sem alterar o código — basta atualizar os pesos no S3 e redeploy.

#### Diarização (PyAnnote)

| Propriedade               | Valor padrão                                                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Pipeline base (HF)        | [`fatymatariq/speaker-diarization-3.1`](https://huggingface.co/fatymatariq/speaker-diarization-3.1)                              |
| Variável de env          | `DIARIZATION_BASE_MODEL`                                                                                                      |
| Segmentation (fine-tuned) | Pesos carregados do S3 via `MODEL_SEGMENTATION_LOCAL_PATH`; substituem o modelo de segmentação do pipeline base             |
| Como é usado             | `Pipeline.from_pretrained(DIARIZATION_BASE_MODEL)` → substitui `pipeline._segmentation.model` pelo modelo fine-tuned do S3 |

> **Acesso ao modelo:** O `fatymatariq/speaker-diarization-3.1` é uma cópia pública do `pyannote/speaker-diarization-3.1`. Se preferir usar o modelo oficial da PyAnnote, é necessário aceitar os termos de uso no Hugging Face e configurar `HF_TOKEN` no container. Veja `finetuning/` para detalhes.

#### Alinhamento forçado (Wav2Vec2)

| Propriedade     | Valor                                                                                                                        |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Modelo (HF)     | [`jonatasgrosman/wav2vec2-large-xlsr-53-portuguese`](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-portuguese) |
| Configuração  | Hardcoded em `api/app/services/alignment_service.py` — trocar editando `_WAV2VEC2_MODEL_ID` no arquivo                  |
| Quando é usado | Alinhamento palavra-a-palavra no tempo (força a correspondência entre token e trecho de áudio)                            |
| Download        | Baixado automaticamente do HF Hub na primeira execução (sem S3); requer acesso à internet ou VPC endpoint do HF           |

### 7.5 Padrão de nomenclatura dos arquivos de áudio

A Lambda `transcribe_audio_to_elastic_search` e o helper de diarização interpretam metadados a partir do nome do arquivo. O padrão atual é:

```
YYYYMMDD_HHMMSS_RAMAL_OPERADOR_DURACAO_DIRECAO_AGENTE.wav
```

Para adaptar ao padrão da sua organização, modifique a função `determine_speakers` em:

```
api/terraform/lambdas/transcribe_audio_to_elastic_search/helpers/transcribe_helper.py
```

E a função de parsing de metadados em:

```
api/terraform/lambdas/transcribe_audio_to_elastic_search/helpers/process_destination_helper.py
```

---

## 8. Diretrizes de Segurança e Proteção de Dados

### 8.1 Arquivos que nunca devem ser commitados

| Arquivo                               | Motivo                                               |
| ------------------------------------- | ---------------------------------------------------- |
| `scripts/Montagem_Ambiente/.env`    | Contém profile AWS, IDs de recursos, paths internos |
| `api/terraform/terraform.tfvars`    | Contém IDs de VPC, subnets, endpoints reais         |
| `api/app/utils/text_corrections.py` | Contém vocabulário proprietário da organização  |
| `api/terraform/prompts/*.txt`       | Contém prompts customizados (IP organizacional)     |
| `finetuning/data/`                  | Contém dados de áudio e transcrições             |
| `finetuning/workspace/`             | Contém notebooks com outputs de modelos             |

Todos estão no `.gitignore`. Verificar periodicamente com:

```bash
git status --short | grep -v "^?"
git diff --name-only HEAD
```

### 8.2 Isolamento de rede

Toda a solução opera dentro de uma VPC privada:

- O ALB é **interno** (sem IP público) — acessível apenas de dentro da VPC
- As Lambdas operam nas subnets privadas
- O OpenSearch é implantado como domínio VPC (sem endpoint público)
- O ECS opera nas subnets privadas
- O acesso à internet sai pelo NAT Gateway (apenas tráfego de saída)

### 8.3 Credenciais e API Keys

| Credencial                      | Como configurar                                             |
| ------------------------------- | ----------------------------------------------------------- |
| Credenciais AWS                 | AWS SSO +`aws sso login` (nunca hardcoded)                |
| OpenAI API Key (opcional)       | `export TF_VAR_openai_api_key="sk-..."` (nunca no tfvars) |
| HuggingFace Token (fine-tuning) | `export HF_TOKEN="hf_..."` (nunca commitado)              |

### 8.4 IAM — Princípio do menor privilégio

As roles IAM criadas pelo Terraform seguem o princípio do menor privilégio:

- **ECS Task Role:** acesso somente ao bucket S3 configurado e à tabela DynamoDB do projeto
- **Lambda Roles:** acesso somente às filas SQS e recursos necessários para cada função
- **Bedrock:** acesso via `AmazonBedrockFullAccess` na task role — recomenda-se restringir ao modelo e prompts específicos em produção

### 8.5 Dados de áudio e transcrições

- Os áudios `.wav` e as transcrições `.json` ficam no S3, cujo acesso é controlado pelas IAM policies
- O OpenSearch é VPC-only; consultas externas requerem VPN ou bastion host
- Para conformidade com LGPD/GDPR, configure o S3 Lifecycle para expiração automática dos áudios conforme a política de retenção da sua organização

### 8.6 Auditoria e monitoramento

- Todos os componentes logam no CloudWatch Logs
- O grupo de logs do ECS é: `/ecs/<project_name>-log-group-<env>`
- As Lambdas logam em: `/aws/lambda/<nome_da_funcao>`
- Recomenda-se habilitar CloudTrail na conta para auditoria de chamadas à API AWS

---

## 9. Estrutura do Repositório

```
ONSTranscribe/
├── api/
│   ├── app/
│   │   ├── api/v1/
│   │   │   └── transcribe.py          # Endpoints FastAPI
│   │   ├── services/
│   │   │   ├── transcribe_service.py  # Orquestração do pipeline
│   │   │   ├── audio_service.py       # Processamento de áudio
│   │   │   ├── alignment_service.py   # Alinhamento ASR + diarização
│   │   │   ├── llm_service.py         # Integração Bedrock (LLM)
│   │   │   ├── model_manager.py       # Carregamento dos modelos ML
│   │   │   ├── s3_service.py          # Operações S3
│   │   │   └── sqs_worker.py          # Consumidor SQS
│   │   ├── utils/
│   │   │   ├── text_corrections_example.py  # Template de correções (versionado)
│   │   │   └── text_corrections.py          # Correções reais (gitignored)
│   │   └── config.py                  # Configuração via variáveis de ambiente
│   ├── terraform/
│   │   ├── *.tf                       # Módulos Terraform (ECS, Lambda, ALB, etc.)
│   │   ├── prompts/
│   │   │   ├── *.example.txt          # Templates dos prompts (versionados)
│   │   │   └── *.txt                  # Prompts reais (gitignored)
│   │   ├── lambdas/
│   │   │   ├── create_transcribe_job_via_api/
│   │   │   ├── transcribe_audio_to_elastic_search/
│   │   │   ├── calcular_metricas_transcricao/
│   │   │   └── movimentacao_arquivos_wav_sala_de_controle/
│   │   └── terraform.tfvars.sample    # Template do tfvars (versionado)
├── finetuning/
│   ├── scripts/                       # Scripts de download e preparação de dados
│   ├── finetune_whisper/              # Template do notebook Whisper
│   ├── finetune_pyannote-diarization/ # Templates dos notebooks PyAnnote
│   └── subject-classification-transcription/ # Template do classificador
├── scripts/
│   └── Montagem_Ambiente/
│       ├── common.sh                  # Funções compartilhadas dos scripts
│       ├── deploy_all_trancribe.sh    # Script principal de deploy
│       ├── prepare_terraform_workspace.sh
│       ├── destroy_all_trancribe.sh   # Desmontagem do ambiente
│       └── .env.sample                # Template do .env (versionado)
├── outputs/                           # Diretório para artefatos de saída local
├── Guia_Deploy_Novo_Ambiente.md       # Guia completo de deploy
├── Guia_Destroy_Ambiente.md           # Guia de destruição do ambiente
└── README.md                          # Este arquivo
```

---

## 10. Operação

### Deploy

Consulte o **`Guia_Deploy_Novo_Ambiente.md`** para o procedimento passo a passo, executado
pelo `deploy_all_trancribe.sh` de `scripts/Montagem_Ambiente/`:

- Bootstrap de infraestrutura (OpenSearch, NAT, VPC Endpoints)
- Build e push da imagem Docker
- Upload dos modelos ML
- Terraform plan e apply
- Monitoramento da subida do ECS

Antes de usar, leia a seção "Estado desta esteira" do README daquela pasta: o Terraform
que a esteira renderiza está defasado em relação ao de `api/terraform/`.

### Destroy

Consulte o **`Guia_Destroy_Ambiente.md`** para destruir o ambiente, incluindo o tratamento
de problemas conhecidos (ENIs de Lambda presas na subnet, instância GPU fora do state). O
`destroy_all_trancribe.sh` da esteira automatiza a sequência, com cada etapa controlada por
uma variável `DESTROY_*` do `.env`.
