# Montagem de Ambiente ONSTranscribe

Esta pasta contém uma esteira local para subir apenas a infraestrutura e os componentes da pasta `ONSTranscribe`, usando:

- o Terraform atual do projeto;
- uma cópia de trabalho gerada em tempo de execução;
- composição dos recursos que faltam para ECS com GPU;
- capacidade ECS baseada em `EC2` com `Launch Template`, `Auto Scaling Group` e `Capacity Provider`;
- scripts já existentes no repositório quando fizerem parte do fluxo, como `ONSTranscribe/scripts/create_index.sh`.

## Estado desta esteira

Esta esteira **não está alinhada com o Terraform atual de `ONSTranscribe/api/terraform/`**
e não deve ser usada para um deploy real sem a revisão descrita abaixo.

O `prepare_terraform_workspace.sh` não aplica o Terraform do repositório como ele está: o
`common.sh` **regera** `ecr.tf`, `network.tf`, `lambda.tf`, `ecs.tf` e um
`zz_generated_gpu_support.tf` a partir de heredocs. Esse conteúdo descreve a arquitetura
anterior, em que a capacidade GPU vinha de EC2 autogerenciado (`aws_launch_template` +
`aws_autoscaling_group` + capacity provider de ASG). O Terraform do repositório migrou para
**ECS Managed Instances**, em `capacity_provider.tf`, e não tem nenhum Auto Scaling Group.

O workspace gerado **passa no `terraform validate`** — o desalinhamento não é sintático e
nada impede o `apply` de começar. O que ele produz, verificado no workspace gerado:

- o `ecs.tf` renderizado reverte a task definition para `requires_compatibilities = ["EC2"]`
  e aponta o service para `aws_ecs_capacity_provider.gpu_capacity_provider` (o do ASG), em
  vez do `transcribe_gpu` de Managed Instances. Na prática, desfaz a migração;
- convivem dois `aws_appautoscaling_target` com o mesmo `resource_id` e o mesmo
  `scalable_dimension`: o `fastapi_gpu` de `autoscaling.tf`, que escala por profundidade de
  fila e usa `min_capacity = 0` como pause de custo, e o `fastapi_scaling_target` gerado,
  que escala por CPU/memória e fixa `min_capacity = 1`. São dois recursos Terraform
  disputando o mesmo scalable target da AWS, com drift a cada apply e sem o pause de custo;
- convivem dois capacity providers no mesmo cluster, e o gerado inclui
  `aws_ecs_cluster_capacity_providers`, que o `capacity_provider.tf` desaconselha por escrito
  (no apply falhou com *not in an ACTIVE state* logo depois do create);
- o `lambda.tf` renderizado usa `filebase64sha256` sobre um zip que o próprio
  `data.archive_file` produz no mesmo apply, em vez do `output_base64sha256` do source. Sem
  o zip presente de antemão, o `validate` falha.

O que continua válido e independente do render: os três bootstraps por AWS CLI
(`bootstrap_private_nat.sh`, `bootstrap_vpc_endpoints.sh`, `bootstrap_opensearch_domain.sh`),
o endurecimento do bucket de backend, o `destroy_all_trancribe.sh` e as validações de
pós-deploy. Realinhar a esteira significa remover as funções `render_*` do `common.sh` e
deixar o `terraform init/plan/apply` rodar sobre o source do repositório.

## Arquivos principais

- `.env.sample`
  Arquivo modelo com todas as variáveis que o processo precisa ler.

- `deploy_all_trancribe.sh`
  Script principal que executa o fluxo completo.

- `destroy_all_trancribe.sh`
  Desmonta o ambiente na ordem inversa: `terraform destroy`, domínio e security group do
  OpenSearch, repositório ECR e, só se `DESTROY_BACKEND=true`, o bucket de state e a tabela
  de lock. Cada etapa é controlada por uma variável `DESTROY_*` do `.env`.

- `prepare_terraform_workspace.sh`
  Gera uma cópia de trabalho do Terraform do `ONSTranscribe`, renderiza os arquivos corrigidos para ECS/GPU, ajusta health check e prepara a execução.

- `common.sh`
  Funções compartilhadas entre os scripts.

- `bootstrap_private_nat.sh`
  Bootstrap idempotente de NAT Gateway para a rede privada, criando Elastic IP, NAT e rota default nas route tables privadas quando necessário.

- `bootstrap_opensearch_domain.sh`
  Bootstrap idempotente do domínio OpenSearch compatível com a solução atual, criando security group dedicado, domínio VPC sem fine-grained access control e sincronizando o `terraform.tfvars` com `domain_name`, `index_name` e `elastic_search_uri`.

- `bootstrap_vpc_endpoints.sh`
  Bootstrap idempotente de VPC endpoints compartilhados da rede privada, validando se o endpoint já existe e criando-o quando necessário.

- `../create_index._awscli.sh`
  Alternativa ao `create_index.sh` que tenta criar o índice via `aws opensearch create-index`. Em domínios VPC, o suporte depende da versão do serviço e pode continuar exigindo criação do índice a partir de uma máquina com acesso direto ao endpoint do domínio.

## Como usar

1. Copie o arquivo `.env.sample` para `.env`.
2. Preencha todas as variáveis do `.env`.
3. Use o formato estrito `KEY=VALUE`. O `.env` é interpretado como arquivo de configuração, não como shell script.
4. Mantenha entre aspas simples ou duplas os valores que tenham espaços ou caracteres especiais. Comandos, subshells e backticks não são aceitos no `.env`.
5. Monte o `terraform.tfvars` do projeto no caminho indicado por `TFVARS_FILE`. Esse caminho deve ser absoluto.
6. Dê permissão de execução aos scripts:

```bash
cd /caminho/para/ONSTranscribe/scripts/Montagem_Ambiente
chmod +x common.sh prepare_terraform_workspace.sh deploy_all_trancribe.sh destroy_all_trancribe.sh
```

7. Execute:

```bash
./deploy_all_trancribe.sh
```

## Bootstrap de rede privada

Se `RUN_BOOTSTRAP_PRIVATE_NAT=true`, a esteira executa um bootstrap de NAT antes do deploy da aplicação.

Esse bootstrap:

- valida se a subnet pública informada tem rota default para Internet Gateway;
- verifica ou cria o Elastic IP do NAT;
- verifica ou cria o NAT Gateway na subnet pública;
- verifica ou cria a rota `0.0.0.0/0` nas route tables privadas informadas.

Variáveis exigidas nesse modo:

- `NAT_VPC_ID`
- `NAT_PUBLIC_SUBNET_ID`
- `NAT_PRIVATE_ROUTE_TABLE_IDS`
- `NAT_GATEWAY_NAME`
- `NAT_EIP_NAME`

Se `RUN_BOOTSTRAP_OPENSEARCH_DOMAIN=true`, a esteira executa um bootstrap do domínio OpenSearch antes do `plan/apply`.

Esse bootstrap:

- cria ou reaproveita um security group dedicado do domínio OpenSearch;
- libera `TCP 443` para os CIDRs informados em `OPENSEARCH_ALLOWED_CIDRS` ou, quando vazio, para o CIDR inteiro da VPC;
- cria um domínio VPC com `EnforceHTTPS=true`, criptografia em repouso, criptografia node-to-node, `IPAddressType=ipv4` e sem `fine-grained access control`;
- atualiza a access policy do domínio para o modo compatível com o cliente atual da Lambda;
- sincroniza o `terraform.tfvars` com `domain_name`, `index_name` e `elastic_search_uri`.

Variáveis exigidas nesse modo:

- `OPENSEARCH_BOOTSTRAP_VPC_ID`
- `OPENSEARCH_BOOTSTRAP_SUBNET_IDS`
- `OPENSEARCH_SECURITY_GROUP_NAME`
- `OPENSEARCH_ENGINE_VERSION`
- `OPENSEARCH_INSTANCE_TYPE`
- `OPENSEARCH_INSTANCE_COUNT`
- `OPENSEARCH_VOLUME_TYPE`
- `OPENSEARCH_VOLUME_SIZE`
- `OPENSEARCH_TLS_SECURITY_POLICY`

Variável opcional:

- `OPENSEARCH_ALLOWED_CIDRS`

Se `RUN_BOOTSTRAP_VPC_ENDPOINTS=true`, a esteira executa um bootstrap de rede antes do deploy da aplicação.

Esse bootstrap:

- verifica ou cria o security group dos VPC endpoints;
- verifica ou cria os endpoints gateway para `s3` e `dynamodb`;
- verifica ou cria os endpoints de interface para `ecs`, `ecs-agent`, `ecs-telemetry`, `ecr.api`, `ecr.dkr`, `logs`, `sqs` e `sts`;
- opcionalmente cria endpoints de `ssm`, `ssmmessages`, `ec2messages`, `bedrock-runtime` e `bedrock-agent`.

Variáveis exigidas nesse modo:

- `VPC_ENDPOINTS_VPC_ID`
- `VPC_ENDPOINTS_PRIVATE_SUBNET_IDS`
- `VPC_ENDPOINTS_ROUTE_TABLE_IDS`
- `VPC_ENDPOINTS_SECURITY_GROUP_NAME`
- `VPC_ENDPOINTS_ENABLE_SSM`
- `VPC_ENDPOINTS_ENABLE_BEDROCK`

## Observação importante

Os arquivos Terraform originais em `ONSTranscribe/api/terraform/` não são alterados por este processo.

O script cria um workspace derivado em:

- `ONST_TERRAFORM_WORKDIR`

É sobre esse workspace gerado que o `terraform init/plan/apply` será executado.

## Melhorias implementadas nesta esteira

- Validação prévia da conta AWS autenticada antes de qualquer ação opcional de deploy.
- Validação opcional do modelo Bedrock informado no `.env`.
- Verificação de que os diretórios de modelos existem e contêm arquivos antes do `s3 sync`.
- Workspace Terraform gerado de forma determinística e formatado antes da execução.
- Service ECS ajustado para usar `capacity_provider_strategy` com `aws_ecs_capacity_provider`.
- `terraform init` executado com `-reconfigure`.
- Bucket de backend do Terraform endurecido com versionamento, bloqueio de acesso público e criptografia SSE-S3.
- `terraform plan` com criação prévia do diretório do arquivo de plano.
- Pós-deploy com espera configurável de estabilização do ECS, validação de DynamoDB, SQS, Lambda e CloudWatch Logs.
- Health check final usando o mesmo path configurado em `ALB_HEALTHCHECK_PATH`.
- Validação opcional do OpenSearch no pós-deploy, cobrindo saúde do domínio e tentativa de validação do índice.

## Observações operacionais

- O ALB do ONSTranscribe é interno. Se `RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=true`, o host que executa o script precisa ter acesso à VPC.
- Se `RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=true`, o host que executa o script precisa ter conectividade com o endpoint do OpenSearch.
- As subnets privadas usadas por ECS e Lambdas precisam de NAT ou VPC Endpoints para ECR, ECR DKR, S3, CloudWatch Logs, DynamoDB e Bedrock.
- No fluxo atual sem NAT, o endpoint de `sts` também é necessário porque a Lambda `transcribe_audio_to_elastic_search` usa `GetCallerIdentity` no provider interno (`LAMBDA_TRANSCRIBE_PROVIDER=internal`).
- O bootstrap de VPC endpoints é feito por AWS CLI e não entra no mesmo state da aplicação, porque esses endpoints podem ser compartilhados com outras cargas da VPC.
- O bootstrap de NAT também é feito por AWS CLI e não entra no mesmo state da aplicação.
- O bootstrap do domínio OpenSearch também é feito por AWS CLI e não entra no mesmo state da aplicação.
- Se já existir um domínio com `fine-grained access control` habilitado, o bootstrap falha cedo. Segundo a AWS, esse recurso não pode ser desabilitado depois de ativado.
- Evite `DOCKER_IMAGE_TAG=latest` em ambientes que precisem de rastreabilidade.
- O preflight de Bedrock verifica o cadastro do modelo e detecta erros básicos de configuração, mas não substitui uma invocação real para comprovar `Model Access`.
- `TF_VAR_openai_api_key` passou a ser opcional na esteira, mas o módulo Terraform atual ainda pode exigir esse valor durante `plan/apply`.
- Os parâmetros `ECS_STABILITY_MAX_ATTEMPTS` e `ECS_STABILITY_SLEEP_SECONDS` controlam a espera do pós-deploy para ambientes em que o startup da API é mais lento por causa do download dos modelos.

## Variáveis sensíveis ao ambiente

Ao trocar de `dev` para `hmg` ou `prd`, revise principalmente:

- `ECS_CLUSTER_NAME`
- `ALB_NAME`
- `TRANSCRIBE_QUEUE_NAME`
- `TRANSCRIBE_DLQ_NAME`
- `FASTAPI_LOG_GROUP_NAME`
- buckets, domínio OpenSearch e caminhos do `terraform.tfvars`

Os nomes do capacity provider, do Auto Scaling Group, do launch template, da role, do
instance profile, do security group e da instância GPU não vêm do `.env`: são derivados de
`var.project_name` e `var.environment` no Terraform renderizado.
