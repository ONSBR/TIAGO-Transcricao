# Guia de Destroy — Destruição do Ambiente

**Versão:** 1.0  
**Data:** 2026-05-11  
**Objetivo:** Destruir completamente o ambiente de transcrição criado pelo Terraform, liberando todos os recursos AWS provisionados pelo deploy.

> Para criar um novo ambiente após o destroy, consulte o `Guia_Deploy_Novo_Ambiente.md`.

---

## 1. Pré-condições

- Sessão AWS ativa: `aws sts get-caller-identity --profile <SEU_PROFILE>`
- Terraform workspace gerado (se não existir, o script `prepare_terraform_workspace.sh` o gera)

---

## 2. Carregar variáveis e verificar state

```bash
cd <CAMINHO_DO_REPOSITORIO>

source scripts/Montagem_Ambiente/.env
export AWS_PROFILE="$AWS_PROFILE"
export AWS_SDK_LOAD_CONFIG=true

# Confirmar que aponta para o state correto antes de continuar
echo "State:  s3://$TF_STATE_BUCKET/$TF_STATE_KEY"
echo "Region: $AWS_DEFAULT_REGION"
aws sts get-caller-identity --profile "$AWS_PROFILE" --query Account --output text
```

---

## 3. Garantir que o workspace existe

```bash
ls "$ONST_TERRAFORM_WORKDIR/main.tf" || \
  bash scripts/Montagem_Ambiente/prepare_terraform_workspace.sh \
    scripts/Montagem_Ambiente/.env
```

---

## 4. Terraform init

```bash
terraform -chdir="$ONST_TERRAFORM_WORKDIR" init \
  -input=false -reconfigure \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=$TF_STATE_KEY" \
  -backend-config="region=$AWS_DEFAULT_REGION" \
  -backend-config="profile=$AWS_PROFILE"
```

---

## 5. Revisar o que será destruído

```bash
terraform -chdir="$ONST_TERRAFORM_WORKDIR" plan \
  -destroy -var-file="$TFVARS_FILE"
```

**Recursos destruídos pelo destroy:**

| Categoria | Recursos |
|---|---|
| Computação | ECS cluster, service, task definition, capacity provider, ASG, Launch Template |
| Rede | ALB, Target Group, Security Groups |
| Serverless | Lambda functions, Lambda layers, event source mappings |
| Mensageria | SQS queues, SNS topics, S3 notifications |
| Dados | DynamoDB table |
| IA | Bedrock prompts |
| Observabilidade | CloudWatch log groups |
| IAM | Roles e policies criadas pelo Terraform |

**Recursos que o destroy NÃO remove** (foram criados pelos bootstraps e são independentes):

| Recurso | Motivo |
|---|---|
| S3 bucket e dados | Proteção contra perda de dados |
| OpenSearch domain | Bootstrap separado; caro recriar |
| NAT Gateway e EIP | Bootstrap separado |
| VPC Endpoints | Bootstrap separado |
| ECR repository e imagens | Reutilizável entre deploys |
| Terraform state no S3 | Necessário para gerenciar o ambiente |

---

## 6. Executar o destroy

```bash
terraform -chdir="$ONST_TERRAFORM_WORKDIR" destroy \
  -var-file="$TFVARS_FILE"
```

O Terraform pedirá confirmação. Digite `yes` e aguarde.

**Tempo estimado: 5–10 minutos.**

---

## Problemas conhecidos e soluções

### ECS service travado no destroy (>5 min)

**Causa:** Task ainda em execução; o drain aguarda o `deregistration_delay` antes de desregistrar do target group.

**Solução — escalar para 0 e parar as tasks manualmente antes do destroy:**

```bash
aws ecs update-service \
  --cluster "$ECS_CLUSTER_NAME" \
  --service "$ECS_SERVICE_NAME" \
  --desired-count 0 \
  --region "$AWS_DEFAULT_REGION" --profile "$AWS_PROFILE"

for task in $(aws ecs list-tasks \
  --cluster "$ECS_CLUSTER_NAME" \
  --service-name "$ECS_SERVICE_NAME" \
  --region "$AWS_DEFAULT_REGION" --profile "$AWS_PROFILE" \
  --query 'taskArns[]' --output text); do
  aws ecs stop-task \
    --cluster "$ECS_CLUSTER_NAME" --task "$task" \
    --region "$AWS_DEFAULT_REGION" --profile "$AWS_PROFILE"
done
```

---

### Instância EC2 GPU não destruída pelo Terraform

**Causa:** A instância é criada pelo Auto Scaling Group e pode não estar no Terraform state, ou o cluster ECS ainda a lista como container instance ativa.

**Sintoma:** `ClusterContainsContainerInstancesException` durante o destroy.

**Solução:**

```bash
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$GPU_INSTANCE_NAME" \
            "Name=instance-state-name,Values=running" \
  --query "Reservations[0].Instances[0].InstanceId" \
  --output text --profile "$AWS_PROFILE" --region "$AWS_DEFAULT_REGION")

echo "Terminando instância: $INSTANCE_ID"

aws ec2 terminate-instances \
  --instance-ids "$INSTANCE_ID" \
  --profile "$AWS_PROFILE" --region "$AWS_DEFAULT_REGION"
```

Aguardar a instância atingir o status `terminated` antes de prosseguir com o destroy.

---

### Security Group do ALB travado — ENIs de Lambda ainda em uso

**Sintoma:** `DependencyViolation` ao deletar o security group do ALB. ENIs com description `AWS Lambda VPC ENI-<nome_da_função>` aparecem com status `in-use`.

**Causa:** O Lambda Hyperplane gerencia ENIs de forma assíncrona. Após a deleção da função, as ENIs podem levar até 40 minutos para serem liberadas pela AWS.

**Verificar ENIs associadas ao SG:**

```bash
aws ec2 describe-network-interfaces \
  --filters "Name=group-id,Values=<SG_ID>" \
  --query "NetworkInterfaces[*].{ID:NetworkInterfaceId,Status:Status,AttachmentStatus:Attachment.Status,AttachmentId:Attachment.AttachmentId}" \
  --output table --profile "$AWS_PROFILE"
```

**Se `AttachmentId` for `None`** — estado inconsistente; deletar diretamente:

```bash
# Repetir para cada ENI listada
aws ec2 delete-network-interface \
  --network-interface-id <ENI_ID> \
  --profile "$AWS_PROFILE"

# Com as ENIs removidas, deletar o SG
aws ec2 delete-security-group \
  --group-id <SG_ID> \
  --profile "$AWS_PROFILE"
```

**Se `AttachmentId` tiver valor** — aguardar o Lambda Hyperplane liberar (20–40 min) e tentar novamente. O `terraform destroy` retomará automaticamente quando o SG puder ser deletado.

---

## 7. Verificação pós-destroy

```bash
# ECS cluster deve estar INACTIVE
aws ecs describe-clusters \
  --clusters "$ECS_CLUSTER_NAME" \
  --region "$AWS_DEFAULT_REGION" --profile "$AWS_PROFILE" \
  --query 'clusters[0].status' --output text
# Esperado: "INACTIVE" ou "None"

# Instância GPU não deve existir mais
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$GPU_INSTANCE_NAME" \
            "Name=instance-state-name,Values=running" \
  --region "$AWS_DEFAULT_REGION" --profile "$AWS_PROFILE" \
  --query 'Reservations[].Instances[].InstanceId' --output text
# Esperado: vazio

# Lambda functions não devem existir
aws lambda get-function \
  --function-name create_transcribe_job_via_api \
  --region "$AWS_DEFAULT_REGION" --profile "$AWS_PROFILE" 2>&1 | grep -i "not found"
# Esperado: "Function not found"
```
