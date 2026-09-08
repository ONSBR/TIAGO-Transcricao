#!/usr/bin/env bash

# =============================================================================
# destroy_all_trancribe.sh
# Teardown do ambiente ONSTranscribe provisionado por deploy_all_trancribe.sh.
#
# Ordem (importante):
#   1) terraform destroy  -> remove TUDO que está no state (ECS/GPU/ALB/lambdas/
#      SQS/SNS/DynamoDB/IAM/bedrock prompts/lakeformation perms/SGs).
#      OBS: ECR e OpenSearch NÃO estão no state (foram criados via AWS CLI pela
#      esteira), por isso são tratados separadamente abaixo.
#   2) OpenSearch domain (delete-domain)  -> ~15 min para sumir.
#   3) OpenSearch security group (bootstrap)  -> só some depois das ENIs do
#      domínio serem liberadas; é best-effort, pode exigir reexecução.
#   4) ECR repository (--force, apaga imagens).
#   5) Backend (bucket de state versionado + lock table) -> OPCIONAL, off por
#      padrão (apaga o histórico do state).
#
# Uso:
#   bash destroy_all_trancribe.sh ./.env
#
# Flags (env vars; default entre colchetes):
#   DESTROY_TERRAFORM=[true] DESTROY_OPENSEARCH_DOMAIN=[true]
#   DESTROY_OPENSEARCH_SG=[true] DESTROY_ECR=[true] DESTROY_BACKEND=[false]
#   TF_DESTROY_AUTO_APPROVE=[false]   (false = terraform pede confirmação)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE_PATH="${1:-$DEFAULT_ENV_FILE}"
load_env "$ENV_FILE_PATH"

export AWS_PAGER=""
export TF_IN_AUTOMATION=1

require_command aws
require_command terraform

require_env \
  AWS_PROFILE \
  AWS_DEFAULT_REGION \
  ONST_TERRAFORM_SOURCE_DIR \
  ONST_TERRAFORM_WORKDIR \
  TFVARS_FILE \
  TF_STATE_BUCKET \
  TF_LOCK_TABLE \
  TF_STATE_KEY \
  ECR_REPOSITORY_NAME \
  OPENSEARCH_DOMAIN_NAME \
  OPENSEARCH_SECURITY_GROUP_NAME \
  OPENSEARCH_BOOTSTRAP_VPC_ID

DESTROY_TERRAFORM="${DESTROY_TERRAFORM:-true}"
DESTROY_OPENSEARCH_DOMAIN="${DESTROY_OPENSEARCH_DOMAIN:-true}"
DESTROY_OPENSEARCH_SG="${DESTROY_OPENSEARCH_SG:-true}"
DESTROY_ECR="${DESTROY_ECR:-true}"
DESTROY_BACKEND="${DESTROY_BACKEND:-false}"
TF_DESTROY_AUTO_APPROVE="${TF_DESTROY_AUTO_APPROVE:-false}"

destroy_terraform() {
  log "Step: terraform destroy"

  # O workspace gerado pode não existir (limpeza). Regenera de forma determinística.
  if [[ ! -d "$ONST_TERRAFORM_WORKDIR" ]]; then
    log "Workspace gerado ausente; regenerando via prepare_terraform_workspace.sh"
    require_file "$SCRIPT_DIR/prepare_terraform_workspace.sh"
    chmod +x "$SCRIPT_DIR/prepare_terraform_workspace.sh"
    "$SCRIPT_DIR/prepare_terraform_workspace.sh" "$ENV_FILE_PATH"
  fi

  terraform -chdir="$ONST_TERRAFORM_WORKDIR" init \
    -input=false \
    -reconfigure \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="key=$TF_STATE_KEY" \
    -backend-config="region=$AWS_DEFAULT_REGION" \
    -backend-config="dynamodb_table=$TF_LOCK_TABLE"

  # IMPORTANTE: destruir ANTES de remover o ECR. O data source
  # data.aws_ecr_image.fastapi_image precisa do repo/imagem existindo durante o refresh.
  if is_true "$TF_DESTROY_AUTO_APPROVE"; then
    terraform -chdir="$ONST_TERRAFORM_WORKDIR" destroy -auto-approve -input=false -var-file="$TFVARS_FILE"
  else
    terraform -chdir="$ONST_TERRAFORM_WORKDIR" destroy -input=false -var-file="$TFVARS_FILE"
  fi
  log "terraform destroy concluído"
}

destroy_opensearch_domain() {
  log "Step: delete OpenSearch domain (fora do terraform state)"
  if aws opensearch describe-domain \
      --domain-name "$OPENSEARCH_DOMAIN_NAME" \
      --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1; then
    aws opensearch delete-domain \
      --domain-name "$OPENSEARCH_DOMAIN_NAME" \
      --region "$AWS_DEFAULT_REGION" >/dev/null
    log "Exclusão do domínio solicitada: $OPENSEARCH_DOMAIN_NAME (a remoção completa leva ~15 min)"
  else
    log "Domínio OpenSearch não encontrado (já removido?): $OPENSEARCH_DOMAIN_NAME"
  fi
}

destroy_opensearch_sg() {
  log "Step: delete OpenSearch security group (bootstrap)"
  local sg_id=""
  sg_id="$(aws ec2 describe-security-groups \
    --filters \
      "Name=vpc-id,Values=$OPENSEARCH_BOOTSTRAP_VPC_ID" \
      "Name=group-name,Values=$OPENSEARCH_SECURITY_GROUP_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || true)"

  if [[ -z "$sg_id" || "$sg_id" == "None" ]]; then
    log "Security group do OpenSearch não encontrado: $OPENSEARCH_SECURITY_GROUP_NAME"
    return 0
  fi

  if aws ec2 delete-security-group --group-id "$sg_id" --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1; then
    log "Security group do OpenSearch removido: $sg_id"
  else
    warn "Não foi possível remover o SG $sg_id agora. As ENIs do domínio ainda podem estar em uso enquanto o domínio é excluído (~15 min). Reexecute o destroy após o domínio sumir, ou rode: aws ec2 delete-security-group --group-id $sg_id --region $AWS_DEFAULT_REGION"
  fi
}

destroy_ecr() {
  log "Step: delete ECR repository (fora do terraform state)"
  if aws ecr describe-repositories \
      --repository-names "$ECR_REPOSITORY_NAME" \
      --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1; then
    aws ecr delete-repository \
      --repository-name "$ECR_REPOSITORY_NAME" \
      --force \
      --region "$AWS_DEFAULT_REGION" >/dev/null
    log "Repositório ECR removido (com imagens): $ECR_REPOSITORY_NAME"
  else
    log "Repositório ECR não encontrado (já removido?): $ECR_REPOSITORY_NAME"
  fi
}

destroy_backend() {
  warn "Step: delete Terraform backend (OPCIONAL) — isso apaga o histórico do state!"
  aws dynamodb delete-table \
    --table-name "$TF_LOCK_TABLE" \
    --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1 \
    && log "Lock table removida: $TF_LOCK_TABLE" \
    || warn "Não foi possível remover a lock table $TF_LOCK_TABLE (já removida?)"

  warn "O bucket de state '$TF_STATE_BUCKET' é VERSIONADO. Para removê-lo, esvazie TODAS as versões e delete markers antes:"
  warn "  aws s3api delete-objects --bucket $TF_STATE_BUCKET --delete \"\$(aws s3api list-object-versions --bucket $TF_STATE_BUCKET --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json)\""
  warn "  aws s3api delete-objects --bucket $TF_STATE_BUCKET --delete \"\$(aws s3api list-object-versions --bucket $TF_STATE_BUCKET --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json)\""
  warn "  aws s3api delete-bucket --bucket $TF_STATE_BUCKET --region $AWS_DEFAULT_REGION"
  warn "Não automatizado por segurança."
}

main() {
  log "Iniciando teardown do ONSTranscribe"
  log "Conta/Região alvo: profile=$AWS_PROFILE region=$AWS_DEFAULT_REGION"

  if is_true "$DESTROY_TERRAFORM"; then destroy_terraform; fi
  if is_true "$DESTROY_OPENSEARCH_DOMAIN"; then destroy_opensearch_domain; fi
  if is_true "$DESTROY_OPENSEARCH_SG"; then destroy_opensearch_sg; fi
  if is_true "$DESTROY_ECR"; then destroy_ecr; fi
  if is_true "$DESTROY_BACKEND"; then destroy_backend; fi

  log "Teardown concluído."
  log "Pendências assíncronas: o domínio OpenSearch pode levar ~15 min para sumir; se o SG não foi removido, reexecute o destroy depois."
  log "NÃO removidos (compartilhados/pré-existentes): NAT Gateway, VPC endpoints, route tables, VPC, buckets de dados (dev-s3-transcricao-data) e modelos em S3."
}

main "$@"
