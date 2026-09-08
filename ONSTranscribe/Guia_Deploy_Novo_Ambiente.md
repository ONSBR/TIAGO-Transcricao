# Guia de Deploy — Novo Ambiente do Zero

**Versão:** 1.2
**Data:** 2026-05-12
**Objetivo:** Subir um ambiente completamente novo de transcrição de áudio a partir do repositório, sem dependência de nenhum ambiente anterior.

---

> Para destruir um ambiente existente antes de fazer um novo deploy, consulte o `Guia_Destroy_Ambiente.md`.

---

## 1. Pré-requisitos

### Ferramentas necessárias

| Ferramenta | Versão mínima | Verificar               |
| ---------- | --------------- | ----------------------- |
| AWS CLI    | v2              | `aws --version`       |
| Terraform  | >= 1.5          | `terraform --version` |
| Docker     | >= 24           | `docker --version`    |
| Python     | >= 3.9          | `python3 --version`   |

### Permissões AWS necessárias

A conta AWS deve ter permissão para criar:

- ECS (cluster, service, task definition, capacity provider)
- EC2 (instância g4dn.xlarge, ASG, Launch Template, Security Groups, ALB)
- Lambda (functions, layers)
- IAM (roles, policies)
- S3 (bucket notifications)
- SQS, SNS, DynamoDB
- OpenSearch, CloudWatch, Bedrock
- ECR

### Quota de EC2

A instância GPU usada é `g4dn.xlarge` (4 vCPUs G-type).
Verifique a quota antes de iniciar:

```bash
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA \
  --region us-east-1 \
  --profile <SEU_PROFILE> \
  --query 'Quota.Value' \
  --output text
```

Valor mínimo necessário: **4**.

---

## 2. Criar profile AWS e preparar buckets S3

Antes de clonar o repositório, garanta que o profile AWS está configurado, que os buckets S3 existem e que os modelos ML estão disponíveis.

---

### 2.1 Criar profile AWS

**Se usar AWS SSO (recomendado para contas corporativas):**

```bash
aws configure sso --profile <SEU_PROFILE>
```

Seguir o fluxo interativo: informar SSO start URL, região SSO, conta e role desejados.

**Se usar credenciais estáticas (Access Key / Secret Key):**

```bash
aws configure --profile <SEU_PROFILE>
```

Preencher quando solicitado:

| Campo                 | Exemplo                 |
| --------------------- | ----------------------- |
| AWS Access Key ID     | `EXAMPLE`             |
| AWS Secret Access Key | `wJalrXUtnFEMI/K7...` |
| Default region name   | `us-east-1`           |
| Default output format | `json`                |

Verificar sessão ativa:

```bash
aws sts get-caller-identity --profile <SEU_PROFILE>
```

---

### 2.2 Criar bucket para o Terraform State

O Terraform precisa de um bucket S3 para armazenar o state remoto e uma tabela DynamoDB para o lock.

> **Atenção:** O nome do bucket de state **não pode** coincidir com o nome do bucket principal da API.

```bash
# Definir nomes (ajustar conforme convenção da organização)
TF_STATE_BUCKET=meu-projeto-tfstate
TF_LOCK_TABLE=meu-projeto-tfstate-lock
AWS_DEFAULT_REGION=us-east-1
AWS_PROFILE=<SEU_PROFILE>

# Criar o bucket
aws s3api create-bucket \
  --bucket "$TF_STATE_BUCKET" \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE"

# Nota: para regiões diferentes de us-east-1, adicionar:
#   --create-bucket-configuration LocationConstraint=$AWS_DEFAULT_REGION

# Habilitar versionamento (permite recuperar states anteriores)
aws s3api put-bucket-versioning \
  --bucket "$TF_STATE_BUCKET" \
  --versioning-configuration Status=Enabled \
  --profile "$AWS_PROFILE"

# Bloquear acesso público
aws s3api put-public-access-block \
  --bucket "$TF_STATE_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,\
BlockPublicPolicy=true,RestrictPublicBuckets=true \
  --profile "$AWS_PROFILE"
```

Criar a tabela DynamoDB para lock de state:

```bash
aws dynamodb create-table \
  --table-name "$TF_LOCK_TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE"
```

Anote os valores — serão usados no `.env` (`TF_STATE_BUCKET`, `TF_LOCK_TABLE`).

---

### 2.3 Criar bucket principal da API

Este bucket recebe áudios, armazena modelos ML, transcrições e métricas de qualidade.

```bash
TRANSCRIBE_BUCKET_NAME=meu-projeto-transcribe

aws s3api create-bucket \
  --bucket "$TRANSCRIBE_BUCKET_NAME" \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE"

# Para regiões diferentes de us-east-1:
#   --create-bucket-configuration LocationConstraint=$AWS_DEFAULT_REGION

aws s3api put-public-access-block \
  --bucket "$TRANSCRIBE_BUCKET_NAME" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,\
BlockPublicPolicy=true,RestrictPublicBuckets=true \
  --profile "$AWS_PROFILE"
```

> **Nota:** O `terraform.tfvars` possui três variáveis de bucket — `raw_bucket_name`, `bucket_name` e
> `audio_bucket_name`. Dependendo da arquitetura, podem ser o mesmo bucket ou buckets distintos. Consulte
> os comentários em `api/terraform/terraform.tfvars` para entender o fluxo entre eles e crie os buckets
> adicionais se necessário, seguindo o mesmo procedimento acima.

---

### 2.4 Definir caminhos (prefixos) no S3

Escolha os prefixos que serão usados dentro do bucket principal. **Não é necessário criá-los antecipadamente** — o S3 cria os "diretórios" no primeiro upload.

| Caminho                        | Variável no `.env` / `tfvars`   | Exemplo de valor                            |
| ------------------------------ | ------------------------------------ | ------------------------------------------- |
| Modelos Whisper                | `WHISPER_MODEL_S3_PREFIX`          | `models-v2/whisper-finetuned/`            |
| Modelos de Segmentação       | `SEGMENTATION_MODEL_S3_PREFIX`     | `models-v2/segmentation-finetuned/`       |
| Modelos de Classificação     | `SUBJECT_MODEL_S3_PREFIX`          | `models-v2/subject-classification-model/` |
| Transcrições                 | `transcriptions_key_path` (tfvars) | `transcriptions/`                         |
| Áudios (sala de controle)     | `control_room_key_path` (tfvars)   | `audio/sala-de-controle/`                 |
| Ground truth de métricas      | `groundtruth_key_path` (tfvars)    | `quality/groundtruth/`                    |
| Base de artefatos de qualidade | `quality_base_key_path` (tfvars)   | `quality/`                                |
| Saída de métricas            | `metrics_output_key` (tfvars)      | `quality/metrics/output.json`             |
| Prefixo de métricas           | `metrics_s3_prefix` (tfvars)       | `quality/metrics/`                        |
| Athena                         | `athena_s3_prefix` (tfvars)        | `quality/athena/`                         |

Anote os valores escolhidos — serão preenchidos no `.env` e no `terraform.tfvars` na Seção 3.

---

### 2.5 Verificar disponibilidade dos modelos ML

Os modelos (Whisper fine-tuned, Segmentação e Classificação de Assunto) devem estar no S3 **antes do primeiro `terraform apply`**, pois o container ECS os baixa na inicialização.

**Você já tem os modelos baixados localmente ou já no S3?**

---

**Cenário A — Modelos já estão no S3**

Verificar se os prefixos estão populados:

```bash
aws s3 ls "s3://$TRANSCRIBE_BUCKET_NAME/$WHISPER_MODEL_S3_PREFIX"      --profile "$AWS_PROFILE"
aws s3 ls "s3://$TRANSCRIBE_BUCKET_NAME/$SEGMENTATION_MODEL_S3_PREFIX"  --profile "$AWS_PROFILE"
aws s3 ls "s3://$TRANSCRIBE_BUCKET_NAME/$SUBJECT_MODEL_S3_PREFIX"       --profile "$AWS_PROFILE"
```

Se os comandos retornarem os arquivos dos modelos, pode prosseguir direto para a Seção 3.
No `.env`, defina `RUN_UPLOAD_MODELS=false` antes do deploy (etapa 8.1).

---

**Cenário B — Modelos estão localmente (ainda não no S3)**

Preencha as variáveis de diretório local no `.env` após clonar o repositório (Seção 3):

| Variável                        | O que preencher                                   |
| -------------------------------- | ------------------------------------------------- |
| `WHISPER_MODEL_LOCAL_DIR`      | Caminho local para os pesos do Whisper fine-tuned |
| `SEGMENTATION_MODEL_LOCAL_DIR` | Caminho local para o modelo de segmentação      |
| `SUBJECT_MODEL_LOCAL_DIR`      | Caminho local para o modelo de classificação    |

O script de deploy fará o upload automaticamente via `aws s3 sync` quando `RUN_UPLOAD_MODELS=true` (etapa 8.1).

---

**Cenário C — Modelos ainda precisam ser obtidos**

Se os modelos não estiverem disponíveis localmente nem no S3, obtenha-os (download do HuggingFace,
transferência de outro ambiente, etc.) **antes de prosseguir com o deploy**. O container ECS falhará
na inicialização se os prefixos S3 dos modelos estiverem vazios.

---

## 3. Clonar e configurar o repositório

```bash
git clone https://github.com/ONSBR/TIAGO-Transcricao.git
cd TIAGO-Transcricao/ONSTranscribe
```

### 3.1 Configurar o arquivo de ambiente

```bash
cd ONSTranscribe/scripts/Montagem_Ambiente
cp .env.sample .env
```

Editar `scripts/Montagem_Ambiente/.env` e preencher:

| Variável                        | O que preencher                                            |
| -------------------------------- | ---------------------------------------------------------- |
| `AWS_PROFILE`                  | Nome do profile AWS configurado                            |
| `AWS_DEFAULT_REGION`           | Região (ex:`us-east-1`)                                 |
| `ONST_ROOT`                    | Caminho absoluto até a pasta `ONSTranscribe`            |
| `ONST_API_DIR`                 | Caminho absoluto até `ONSTranscribe/api`                |
| `ONST_TERRAFORM_SOURCE_DIR`    | Caminho até `ONSTranscribe/api/terraform`               |
| `ONST_TERRAFORM_WORKDIR`       | Caminho até onde o workspace será gerado                 |
| `TFVARS_FILE`                  | Caminho absoluto até o `terraform.tfvars`               |
| `TF_STATE_BUCKET`              | Bucket S3 para o Terraform state (criado na Seção 2.2)   |
| `TF_LOCK_TABLE`                | Tabela DynamoDB para lock do state (criada na Seção 2.2) |
| `TF_STATE_KEY`                 | Chave do arquivo de state no bucket                        |
| `TRANSCRIBE_BUCKET_NAME`       | Bucket principal da API (criado na Seção 2.3)            |
| `WHISPER_MODEL_S3_PREFIX`      | Prefixo S3 dos modelos Whisper (definido na Seção 2.4)   |
| `SEGMENTATION_MODEL_S3_PREFIX` | Prefixo S3 dos modelos de segmentação (Seção 2.4)      |
| `SUBJECT_MODEL_S3_PREFIX`      | Prefixo S3 dos modelos de classificação (Seção 2.4)    |
| `GPU_ECS_AMI_ID`               | AMI ID da imagem ECS-optimized GPU para a região          |

> **AMI GPU:** Obter a AMI ECS-optimized com suporte a GPU para sua região:
>
> ```bash
> aws ssm get-parameters \
>   --names /aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended \
>   --region us-east-1 \
>   --query 'Parameters[0].Value' | python3 -m json.tool
> ```
>
> Copiar o campo `image_id` para `GPU_ECS_AMI_ID`.

### 3.2 Configurar o terraform.tfvars

```bash
cp api/terraform/terraform.tfvars.sample \
   api/terraform/terraform.tfvars
```

Editar `api/terraform/terraform.tfvars` e preencher todos os valores.
Ver comentários no arquivo para orientações de cada variável.

> **Atenção:** `elastic_search_uri` só pode ser preenchido após a criação do
> domínio OpenSearch (Seção 7). Deixe como placeholder por enquanto.

### 3.3 Personalizar os prompts do Bedrock

Os prompts usados pela API para enriquecer as transcrições (resumo, entidades, assunto, glossário) são configuráveis por organização. O repositório entrega apenas os arquivos de exemplo genéricos (`.example.txt`). **Os arquivos `.txt` reais são gitignored e precisam ser criados antes do deploy.**

```bash
cd api/terraform/prompts

for f in *.example.txt; do
  dest="${f%.example.txt}.txt"
  cp "$f" "$dest"
  echo "Criado: $dest"
done
```

Editar cada arquivo `.txt` criado e adaptar ao contexto da sua organização:

| Arquivo                     | O que personalizar                                                                                                                                                                      |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `key_entities.txt`        | Substituir `{Sua Organização}` pelo nome real; adaptar os campos de entidades ao seu domínio (ex: substituir `numero_do_SGI` pelo identificador de chamado da sua organização) |
| `get_audio_subject.txt`   | Definir as categorias de assunto relevantes para o seu domínio (ex: tipos de operação, áreas de atuação)                                                                          |
| `summary.txt`             | Ajustar o contexto organizacional descrito no prompt para o seu setor                                                                                                                   |
| `run_beautify.txt`        | Adaptar as regras de correção de texto ao vocabulário do seu domínio                                                                                                                |
| `run_beautify_system.txt` | Ajustar o perfil do assistente ao contexto da organização                                                                                                                             |
| `technical_glossary.txt`  | Incluir os termos técnicos específicos do seu setor                                                                                                                                   |

> **Nota:** O Terraform lê esses arquivos em `apply` via `file("${path.module}/prompts/...")`. Se os `.txt` não existirem, o apply falha. Execute o passo acima antes de qualquer `terraform apply`.

---

### 3.4 Criar arquivo de correções de texto

O arquivo `api/app/utils/text_corrections.py` contém substituições e correções de texto específicas da organização (nomes de pessoas, siglas, termos técnicos). **É gitignored — precisa ser criado localmente antes do build da imagem Docker.** Se não existir, a API sobe e trava com `ImportError` ao inicializar.

```bash
cp api/app/utils/text_corrections_example.py \
   api/app/utils/text_corrections.py
```

Editar `api/app/utils/text_corrections.py` e adaptar:

| Variável          | O que preencher                                                          |
| ----------------- | ------------------------------------------------------------------------ |
| `SUBSTITUTIONS` | Dicionário de substituições de texto (siglas, nomes, termos do domínio) |
| `NUMBER_WORDS`  | Mapeamento de números por extenso para algarismos (se aplicável)        |

> O arquivo `text_corrections_example.py` traz um exemplo comentado como ponto de partida.

---

## 4. Login AWS

```bash
aws sso login --profile <SEU_PROFILE>

# Verificar sessão ativa
aws sts get-caller-identity --profile <SEU_PROFILE>
```

Carregar variáveis do ambiente:

```bash
source scripts/Montagem_Ambiente/.env

echo "Account: $(aws sts get-caller-identity --profile $AWS_PROFILE --query Account --output text)"
echo "Region:  $AWS_DEFAULT_REGION"
echo "State:   s3://$TF_STATE_BUCKET/$TF_STATE_KEY"
```

---

## 5. Verificar pré-requisito: metricas_layer.zip

O Lambda de métricas exige um layer com a biblioteca `jiwer`. Verificar se existe:

```bash
ls -lh "$ONST_TERRAFORM_SOURCE_DIR"
```

Se não existir, gerar:

```bash
sudo apt zip
cd ONSTranscribe/api/terraform/lambdas/calcular_metricas_transcricao
zip ../calcular_metricas_transcricao.zip lambda_function.py
```

---

## 6. Primeira execução — Bootstrap de infraestrutura

Esta etapa cria os recursos de base (NAT Gateway, OpenSearch, VPC Endpoints, ECR, S3 backend).
O deploy **não chegará ao Terraform apply** ainda — ele para após os bootstraps.

---

### 6.1 Garantir saída de rede para as subnets privadas

As tasks ECS e Lambdas rodam em subnets privadas e precisam alcançar serviços AWS (ECR, S3, CloudWatch, Bedrock, SQS, etc.). Sem saída de rede configurada, a task falha ao iniciar:

```
CannotPullContainerError: dial tcp <IP>:443: i/o timeout
```

Há duas abordagens. **Verifique qual se aplica ao seu ambiente antes de prosseguir.**

---

**Diagnóstico — verificar o que já existe na route table privada:**

```bash
aws ec2 describe-route-tables \
  --filters "Name=route-table-id,Values=$NAT_PRIVATE_ROUTE_TABLE_IDS" \
  --profile "$AWS_PROFILE" \
  --query 'RouteTables[0].Routes[*].{dest:DestinationCidrBlock,tgw:TransitGatewayId,nat:NatGatewayId,gw:GatewayId}'
```

---

**Cenário A — Subnet já tem rota `0.0.0.0/0` via Transit Gateway (`tgw-xxx`)**

O TGW geralmente roteia para outra VPC ou on-premises, **não para a internet**. Use VPC Endpoints para que ECR, S3 e demais serviços sejam acessados diretamente dentro da AWS sem depender da rota padrão. Vá direto para a **Seção 6.3**.

**Cenário B — Subnet não tem rota padrão ou tem rota com gateway nulo**

É necessário criar um NAT Gateway para dar saída à internet. Certifique-se que as variáveis abaixo estão preenchidas no `.env`:

| Variável                        | O que preencher                                            |
| ------------------------------- | ---------------------------------------------------------- |
| `NAT_VPC_ID`                  | ID da VPC onde o NAT será criado                         |
| `NAT_PUBLIC_SUBNET_ID`        | Subnet pública (o NAT Gateway precisa de IP público)     |
| `NAT_PRIVATE_ROUTE_TABLE_IDS` | ID(s) das route tables privadas que receberão a rota NAT |
| `NAT_GATEWAY_NAME`            | Nome para o recurso NAT Gateway (tag `Name`)             |
| `NAT_EIP_NAME`                | Nome para o Elastic IP associado ao NAT (tag `Name`)     |

Editar `.env` habilitando apenas o bootstrap do NAT:

```bash
RUN_BOOTSTRAP_PRIVATE_NAT=true
RUN_BOOTSTRAP_OPENSEARCH_DOMAIN=false
RUN_BOOTSTRAP_VPC_ENDPOINTS=false
RUN_TERRAFORM_PLAN=false
RUN_TERRAFORM_APPLY=false
RUN_BUILD_PUSH_IMAGE=false
RUN_UPLOAD_MODELS=false
RUN_POST_DEPLOY_VALIDATIONS=false
RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=false
RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=false
```

```bash
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

Após a execução, voltar o flag: `RUN_BOOTSTRAP_PRIVATE_NAT=false`

**Tempo estimado: ~2 minutos.** Após criar o NAT, prossiga para a **Seção 6.3** para criar os VPC Endpoints.

---

### 6.2 Criar domínio OpenSearch

> **Nota sobre criação do índice OpenSearch:** Manter `RUN_CREATE_OPENSEARCH_INDEX=false`.
> O índice deve ser criado manualmente via console AWS ou pelo script `scripts/create_index.sh`
> a partir de uma máquina com acesso à VPC. O script `create_index._awscli.sh` não funciona
> com domínios VPC no OpenSearch 3.x (erro de Automatic Semantic Enrichment).

Editar `scripts/Montagem_Ambiente/.env` com os valores abaixo para esta etapa:

```bash
RUN_BOOTSTRAP_PRIVATE_NAT=false
RUN_BOOTSTRAP_OPENSEARCH_DOMAIN=true   # ← obrigatório para criar o domínio OpenSearch
RUN_TERRAFORM_PLAN=false
RUN_TERRAFORM_APPLY=false
RUN_BUILD_PUSH_IMAGE=false
RUN_UPLOAD_MODELS=false
RUN_POST_DEPLOY_VALIDATIONS=false
RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=false
RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=false
```

> **`RUN_BOOTSTRAP_OPENSEARCH_DOMAIN` deve ser `true` nesta etapa.** Se permanecer `false`,
> o domínio OpenSearch não será criado e o comando `watch` da próxima seção retornará
> `ResourceNotFoundException: Domain not found`. O domínio precisa existir antes de preencher
> o endpoint no `terraform.tfvars` (Seção 7) e antes do `terraform plan` (Seção 8.1).
> Após o domínio estar ACTIVE, volte esse flag para `false`.

> **Por que desabilitar as validações pós-deploy?** As flags `RUN_POST_DEPLOY_VALIDATIONS`,
> `RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION` e `RUN_POST_DEPLOY_OPENSEARCH_VALIDATION`
> verificam recursos criados pelo Terraform (ECS service, ALB, SQS, Lambda, DynamoDB).
> Se o `terraform apply` ainda não rodou, esses recursos não existem e o script falha com
> `ECS service lookup returned a failure: MISSING`. Mantenha essas três flags em `false`
> até a etapa 8.3.

Executar:

```bash
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

Após a execução, voltar o flag no `.env`:

```bash
RUN_BOOTSTRAP_OPENSEARCH_DOMAIN=false
```

**Tempo estimado para o OpenSearch ficar ACTIVE: 15–20 minutos.**

Monitorar a criação do domínio OpenSearch:


```bash
watch -n 30 "aws opensearch describe-domain \
  --domain-name $OPENSEARCH_DOMAIN_NAME \
  --region $AWS_DEFAULT_REGION \
  --profile $AWS_PROFILE \
  --query 'DomainStatus.{Status:Processing,Endpoint:Endpoint}' \
  --output json"
```

Aguardar `"Processing": false` e `"Endpoint"` preenchido.

---

### 6.3 Criar VPC Endpoints

Os VPC Endpoints permitem que ECS tasks e Lambdas em subnets privadas acessem serviços AWS (ECR, S3, CloudWatch, SQS, Bedrock etc.) sem depender de NAT ou internet. **Esta etapa é obrigatória independente do Cenário A ou B da Seção 6.1.**

Certifique-se que as variáveis abaixo estão preenchidas no `.env`:

| Variável                              | O que preencher                                                         |
| ------------------------------------- | ----------------------------------------------------------------------- |
| `VPC_ENDPOINTS_VPC_ID`              | ID da VPC                                                               |
| `VPC_ENDPOINTS_PRIVATE_SUBNET_IDS`  | Subnets privadas onde os endpoints de interface serão criados           |
| `VPC_ENDPOINTS_ROUTE_TABLE_IDS`     | Route tables que receberão as rotas dos endpoints gateway (S3/DynamoDB) |
| `VPC_ENDPOINTS_SECURITY_GROUP_NAME` | Nome do security group criado para os endpoints                         |
| `VPC_ENDPOINTS_ENABLE_SSM`          | `true` para criar endpoints SSM (recomendado para acesso às instâncias) |
| `VPC_ENDPOINTS_ENABLE_BEDROCK`      | `true` se a API usa Bedrock                                             |

Editar `.env`:

```bash
RUN_BOOTSTRAP_PRIVATE_NAT=false
RUN_BOOTSTRAP_OPENSEARCH_DOMAIN=false
RUN_BOOTSTRAP_VPC_ENDPOINTS=true
RUN_TERRAFORM_PLAN=false
RUN_TERRAFORM_APPLY=false
RUN_BUILD_PUSH_IMAGE=false
RUN_UPLOAD_MODELS=false
RUN_POST_DEPLOY_VALIDATIONS=false
RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=false
RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=false
```

```bash
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

Após a execução, voltar o flag: `RUN_BOOTSTRAP_VPC_ENDPOINTS=false`

**Tempo estimado: 5–10 minutos** (múltiplos endpoints de interface criados em paralelo).

---

## 7. Preencher o endpoint do OpenSearch

Com o domínio ACTIVE, obter o endpoint:

```bash
aws opensearch describe-domain \
  --domain-name "$OPENSEARCH_DOMAIN_NAME" \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'DomainStatus.Endpoints' \
  --output json
```

Copiar a URL do campo `vpc` e preencher no `terraform.tfvars`:

```hcl
elastic_search_uri = "https://<endpoint-copiado-acima>"
```

---

## 8. Segunda execução — Deploy completo

O deploy completo é feito em três fases sequenciais. **Não pule fases** — cada uma depende da anterior.

---

### 8.1 — Build, upload e Terraform plan

Esta fase faz o build da imagem Docker, sobe os modelos para o S3 e gera o plan do Terraform para revisão. O `apply` **não** é executado ainda.

Editar `scripts/Montagem_Ambiente/.env`:

```bash
RUN_BUILD_PUSH_IMAGE=true
RUN_UPLOAD_MODELS=true
RUN_TERRAFORM_PLAN=true
RUN_TERRAFORM_APPLY=false
TF_AUTO_APPROVE=false
RUN_POST_DEPLOY_VALIDATIONS=false
RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=false
RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=false
```

> **Se os modelos já estiverem no S3 (Cenário A da Seção 2.5):** defina `RUN_UPLOAD_MODELS=false`
> para não repetir o upload.

```bash
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

Ao final, revise o plan gerado em `$ONST_TERRAFORM_WORKDIR/generated.plan` antes de prosseguir.

---

### 8.2 — Terraform apply

Após revisar o plan e confirmar que os recursos listados estão corretos, execute o apply.

Editar `scripts/Montagem_Ambiente/.env`:

```bash
RUN_BUILD_PUSH_IMAGE=false
RUN_UPLOAD_MODELS=false
RUN_TERRAFORM_PLAN=false
RUN_TERRAFORM_APPLY=true
TF_AUTO_APPROVE=false          # mude para true para aprovação automática
RUN_POST_DEPLOY_VALIDATIONS=false
RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=false
RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=false
```

```bash
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

> **Atenção:** O script pedirá confirmação interativa (`yes/no`) a menos que `TF_AUTO_APPROVE=true`.
> Verifique o plan apresentado antes de digitar `yes`.

**Tempo estimado: 10–20 minutos** (criação dos recursos AWS pelo Terraform).

---

### 8.3 — Validações pós-deploy

Após o apply concluir com sucesso, habilite as validações para verificar a saúde do ambiente.

Editar `scripts/Montagem_Ambiente/.env`:

```bash
RUN_BUILD_PUSH_IMAGE=false
RUN_UPLOAD_MODELS=false
RUN_TERRAFORM_PLAN=false
RUN_TERRAFORM_APPLY=false
RUN_POST_DEPLOY_VALIDATIONS=true
RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=true   # desabilite se não tiver acesso à VPC
RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=true
```

```bash
bash scripts/Montagem_Ambiente/deploy_all_trancribe.sh \
  scripts/Montagem_Ambiente/.env
```

O script valida: ECS service estável, DynamoDB, SQS (fila + DLQ), Lambda functions, CloudWatch log group e (opcionalmente) o health endpoint da API e o índice OpenSearch.

> **`RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION`:** O ALB é interno (sem IP público).
> Se o script for executado fora da VPC (ex: máquina local sem VPN), manter `false` para
> evitar timeout. Valide manualmente o health check a partir de um host com acesso à VPC
> (ver Seção 10.1).

---

## 9. Monitorar a subida do serviço ECS

Após o apply, o ECS inicia a instância GPU e carrega os modelos ML.
Esse processo pode levar até **15 minutos**.

```bash
watch -n 15 "aws ecs describe-services \
  --cluster $ECS_CLUSTER_NAME \
  --services $ECS_SERVICE_NAME \
  --region $AWS_DEFAULT_REGION \
  --profile $AWS_PROFILE \
  --query 'services[0].{desired:desiredCount,running:runningCount,pending:pendingCount,status:deployments[0].rolloutState}' \
  --output json"
```

**Sinal de estabilidade:**

- `runningCount == desiredCount`
- `pendingCount == 0`
- `status == "COMPLETED"`

Acompanhar logs do container:

```bash
aws logs tail "/ecs/$ECS_CLUSTER_NAME" \
  --follow \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE" \
  --since 10m
```

Sinais de sucesso nos logs:

- `Whisper model loaded`
- `Segmentation model loaded`
- `Application startup complete`

---

## 10. Validação do ambiente

### 10.1 Health check da API

```bash
ALB_DNS=$(aws elbv2 describe-load-balancers \
  --names "$ALB_NAME" \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'LoadBalancers[0].DNSName' \
  --output text)

echo "ALB: http://$ALB_DNS"

curl -s "http://$ALB_DNS/api/v1/health" | python3 -m json.tool
```

**Resultado esperado:** `{"status": "ok", ...}`

### 10.2 Teste do pipeline — upload de áudio

Fazer upload de um arquivo `.wav` para acionar o pipeline:

```bash
aws s3 cp meu-audio.wav \
  "s3://$(grep bucket_name api/terraform/terraform.tfvars | awk -F'"' '{print $2}')/$(grep control_room_key_path api/terraform/terraform.tfvars | awk -F'"' '{print $2}')TESTE/meu-audio.wav" \
  --profile "$AWS_PROFILE"
```

Acompanhar o processamento nos logs do CloudWatch e verificar se o JSON de transcrição aparece no S3:

```bash
aws s3 ls \
  "s3://$(grep bucket_name api/terraform/terraform.tfvars | awk -F'"' '{print $2}')/$(grep transcriptions_key_path api/terraform/terraform.tfvars | awk -F'"' '{print $2}')" \
  --profile "$AWS_PROFILE" \
  --recursive
```

---

## 11. Referências rápidas

Após o deploy, os recursos criados terão os nomes definidos pelo `project_name` e `environment` do `terraform.tfvars`.
Com `project_name = "minha-transcricao"` e `environment = "dev"`:

| Recurso              | Nome                                      |
| -------------------- | ----------------------------------------- |
| ECS Cluster          | `minha-transcricao-cluster-dev`         |
| ECS Service          | `fastapi-service`                       |
| ALB                  | `minha-transcricao-alb-dev`             |
| ECR Repository       | `minha-transcricao-api`                 |
| SQS Queue            | `minha-transcricao-queue-dev.fifo`      |
| DynamoDB Table       | `minha-transcricao-table-dev`           |
| OpenSearch Domain    | `minha-transcricao-dev`                 |
| OpenSearch Index     | `idx-minha-transcricao-dev`             |
| CloudWatch Log Group | `/ecs/minha-transcricao-log-group-dev`  |
| Terraform State      | `s3://<TF_STATE_BUCKET>/<TF_STATE_KEY>` |

---

## 12. Troubleshooting

### 12.1 ECS task falha com `No space left on device` ao baixar modelos

A instância GPU acumula layers Docker antigas a cada redeploy. Quando o disco enche, o container falha ao tentar baixar os modelos do S3.

**O que o cleanup faz:** remove containers parados e imagens não usadas por nenhum container em execução. Não toca volumes nem networks — seguro para ECS.

---

#### Método 1 — Script automatizado (recomendado, não exige VPN)

O repositório inclui um script em `api/scripts/cleanup_and_redeploy.sh` que:
1. Registra uma task definition de limpeza Docker no próprio cluster ECS
2. Executa a task e aguarda ela terminar
3. Força um novo deploy do `fastapi-service`

**Antes de usar pela primeira vez:** abra o script e ajuste as variáveis da seção `CONFIG`:

| Variável            | O que ajustar                                                                 |
|---------------------|-------------------------------------------------------------------------------|
| `CLUSTER`           | Nome do cluster ECS                                                           |
| `SERVICE`           | Nome do serviço ECS                                                           |
| `SUBNETS`           | Array com as subnets privadas onde o container instance pode estar            |
| `SECURITY_GROUP`    | Security group do serviço ECS                                                 |
| `EXECUTION_ROLE_ARN`| ARN da role de execução das tasks                                             |
| `LOG_GROUP`         | CloudWatch log group onde os logs do cleanup serão gravados                   |
| `AWS_PROFILE`       | Profile AWS CLI                                                               |
| `AWS_REGION`        | Região AWS                                                                    |

> **Como descobrir a subnet correta do container instance** (erro `LOCATION` ao rodar a task):
> ```bash
> aws ecs describe-container-instances \
>   --cluster <CLUSTER> \
>   --container-instances $(aws ecs list-container-instances \
>     --cluster <CLUSTER> \
>     --query 'containerInstanceArns[0]' --output text \
>     --profile <PROFILE> --region <REGION>) \
>   --query 'containerInstances[0].attributes[?name==`ecs.subnet-id`].value' \
>   --profile <PROFILE> --region <REGION> --output text
> ```
> Adicione a subnet retornada ao array `SUBNETS` no script.

Executar:

```bash
chmod +x api/scripts/cleanup_and_redeploy.sh
./api/scripts/cleanup_and_redeploy.sh
```

Acompanhar a subida após o cleanup:

```bash
aws logs tail /ecs/<LOG_GROUP_NAME> \
  --profile <AWS_PROFILE> \
  --region <AWS_REGION> \
  --log-stream-name-prefix ecs/fastapi-container \
  --follow
```

---

#### Método 2 — SSM (requer conectividade com a VPN/rede da organização)

> **Pré-requisito de rede:** o endpoint SSM pode estar roteado para um IP privado acessível somente dentro da rede corporativa. Se receber `Connect timeout on endpoint URL: ssm.amazonaws.com`, use o Método 1.

```bash
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=${project_name}-gpu-node-${environment}" \
            "Name=instance-state-name,Values=running" \
  --region "$AWS_DEFAULT_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text)

# 1. Verificar uso do disco
COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["df -h /var/lib/docker && docker system df"]' \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION" \
  --query 'Command.CommandId' --output text)

# Aguardar ~5s e checar resultado:
aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION" \
  --query 'StandardOutputContent' --output text
```

Se `Use%` > 70%, limpar:

```bash
COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker container prune -f && docker image prune -a -f"]' \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION" \
  --query 'Command.CommandId' --output text)

aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION" \
  --query 'StandardOutputContent' --output text
```

Após a limpeza, forçar novo deployment:

```bash
aws ecs update-service \
  --cluster "$ECS_CLUSTER_NAME" \
  --service "$ECS_SERVICE_NAME" \
  --force-new-deployment \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION"
```

### 12.2 ECS task falha com `ModuleNotFoundError: app.utils.text_corrections`

O arquivo `text_corrections.py` é gitignored e não está no repositório. Deve ser criado manualmente antes de cada build da imagem Docker.

```bash
cd api/app/utils
cp text_corrections_example.py text_corrections.py
# Editar text_corrections.py conforme o domínio antes do build
```

Ver detalhes em [Seção 3.4](#34-criar-arquivo-de-correções-de-texto).

### 12.3 ECS task falha com `CannotPullContainerError: i/o timeout`

A subnet privada não tem rota para os endpoints da AWS. Verifique se os VPC Endpoints foram criados (Seção 6.3). Se ainda não foram:

```bash
RUN_BOOTSTRAP_VPC_ENDPOINTS=true
```

Rodar o bootstrap script novamente e aguardar os endpoints ficarem `available`.

### 12.4 Task definition aponta para imagem antiga após novo build

O Terraform fixa o digest da imagem no momento do `apply`. Se uma nova imagem foi enviada ao ECR sem re-executar o Terraform, o ECS continua usando o digest antigo mesmo com `force-new-deployment`.

Solução: registrar manualmente uma nova revisão da task definition com o digest atual:

```bash
NEW_DIGEST=$(aws ecr describe-images \
  --repository-name "$ECR_REPOSITORY_NAME" \
  --image-ids imageTag=latest \
  --profile "$AWS_PROFILE" \
  --region "$AWS_DEFAULT_REGION" \
  --query 'imageDetails[0].imageDigest' --output text)

ACCOUNT_ID=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query 'Account' --output text)
NEW_IMAGE="${ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com/${ECR_REPOSITORY_NAME}@${NEW_DIGEST}"
echo "Nova imagem: $NEW_IMAGE"
```

Depois registrar nova task definition com esse `NEW_IMAGE` e atualizar o serviço ECS para usar a nova revisão.

