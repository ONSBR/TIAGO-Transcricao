#!/usr/bin/env bash

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
require_command bash
require_command chmod
require_command grep
require_command sleep
require_command find
require_command mkdir

require_env \
  AWS_PROFILE \
  AWS_DEFAULT_REGION \
  RUN_PREFLIGHT_AWS_IDENTITY_CHECK \
  RUN_PREFLIGHT_BEDROCK_CHECK \
  RUN_BOOTSTRAP_PRIVATE_NAT \
  RUN_BOOTSTRAP_OPENSEARCH_DOMAIN \
  RUN_BOOTSTRAP_VPC_ENDPOINTS \
  RUN_CREATE_TERRAFORM_BACKEND \
  RUN_CREATE_ECR \
  RUN_BUILD_PUSH_IMAGE \
  RUN_UPLOAD_MODELS \
  RUN_CREATE_OPENSEARCH_INDEX \
  RUN_PREPARE_TERRAFORM_WORKSPACE \
  RUN_TERRAFORM_VALIDATE \
  RUN_TERRAFORM_PLAN \
  RUN_TERRAFORM_APPLY \
  TF_AUTO_APPROVE \
  RUN_POST_DEPLOY_VALIDATIONS \
  RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION \
  RUN_POST_DEPLOY_OPENSEARCH_VALIDATION \
  ONST_ROOT \
  ONST_API_DIR \
  ONST_TERRAFORM_SOURCE_DIR \
  ONST_TERRAFORM_WORKDIR \
  TFVARS_FILE \
  TF_STATE_BUCKET \
  TF_LOCK_TABLE \
  TF_STATE_KEY \
  TF_PLAN_FILE \
  ECR_REPOSITORY_NAME \
  DOCKER_IMAGE_LOCAL_NAME \
  DOCKER_IMAGE_TAG \
  TRANSCRIBE_BUCKET_NAME \
  WHISPER_MODEL_LOCAL_DIR \
  WHISPER_MODEL_S3_PREFIX \
  SEGMENTATION_MODEL_LOCAL_DIR \
  SEGMENTATION_MODEL_S3_PREFIX \
  SUBJECT_MODEL_LOCAL_DIR \
  SUBJECT_MODEL_S3_PREFIX \
  OPENSEARCH_DOMAIN_NAME \
  OPENSEARCH_INDEX_NAME \
  ALB_HEALTHCHECK_PATH \
  ECS_CLUSTER_NAME \
  ECS_SERVICE_NAME \
  ALB_NAME

require_dir "$ONST_ROOT"
require_dir "$ONST_API_DIR"
require_dir "$ONST_TERRAFORM_SOURCE_DIR"
require_file "$TFVARS_FILE"
require_file "$SCRIPT_DIR/prepare_terraform_workspace.sh"
require_file "$SCRIPT_DIR/bootstrap_private_nat.sh"
require_file "$SCRIPT_DIR/bootstrap_opensearch_domain.sh"
require_file "$SCRIPT_DIR/bootstrap_vpc_endpoints.sh"
require_absolute_path "TFVARS_FILE" "$TFVARS_FILE"
require_safe_workdir_path "$ONST_TERRAFORM_WORKDIR"

if is_true "$RUN_BUILD_PUSH_IMAGE"; then
  require_command docker
fi

if is_true "$RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION" || \
   is_true "$RUN_CREATE_OPENSEARCH_INDEX" || \
   is_true "$RUN_POST_DEPLOY_OPENSEARCH_VALIDATION"; then
  require_command curl
fi

if is_true "$RUN_CREATE_OPENSEARCH_INDEX" || \
   is_true "$RUN_BOOTSTRAP_OPENSEARCH_DOMAIN" || \
   is_true "$RUN_POST_DEPLOY_OPENSEARCH_VALIDATION" || \
   is_true "$RUN_POST_DEPLOY_VALIDATIONS" || \
   is_true "$RUN_PREFLIGHT_AWS_IDENTITY_CHECK"; then
  require_command jq
fi

if is_true "$RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION"; then
  require_env POST_DEPLOY_HEALTHCHECK_ATTEMPTS POST_DEPLOY_HEALTHCHECK_SLEEP_SECONDS
fi

if is_true "$RUN_PREFLIGHT_BEDROCK_CHECK"; then
  require_env BEDROCK_MODEL_ID
fi

if is_true "$RUN_BOOTSTRAP_PRIVATE_NAT"; then
  require_env \
    NAT_VPC_ID \
    NAT_PUBLIC_SUBNET_ID \
    NAT_PRIVATE_ROUTE_TABLE_IDS \
    NAT_GATEWAY_NAME \
    NAT_EIP_NAME
fi

if is_true "$RUN_BOOTSTRAP_OPENSEARCH_DOMAIN"; then
  require_env \
    OPENSEARCH_BOOTSTRAP_VPC_ID \
    OPENSEARCH_BOOTSTRAP_SUBNET_IDS \
    OPENSEARCH_SECURITY_GROUP_NAME \
    OPENSEARCH_ENGINE_VERSION \
    OPENSEARCH_INSTANCE_TYPE \
    OPENSEARCH_INSTANCE_COUNT \
    OPENSEARCH_VOLUME_SIZE \
    OPENSEARCH_VOLUME_TYPE \
    OPENSEARCH_TLS_SECURITY_POLICY
fi

if is_true "$RUN_BOOTSTRAP_VPC_ENDPOINTS"; then
  require_env \
    VPC_ENDPOINTS_VPC_ID \
    VPC_ENDPOINTS_PRIVATE_SUBNET_IDS \
    VPC_ENDPOINTS_ROUTE_TABLE_IDS \
    VPC_ENDPOINTS_SECURITY_GROUP_NAME \
    VPC_ENDPOINTS_ENABLE_SSM \
    VPC_ENDPOINTS_ENABLE_BEDROCK
fi

if is_true "$RUN_POST_DEPLOY_VALIDATIONS"; then
  require_env \
    ECS_STABILITY_MAX_ATTEMPTS \
    ECS_STABILITY_SLEEP_SECONDS \
    TRANSCRIPTIONS_TABLE_NAME \
    TRANSCRIBE_QUEUE_NAME \
    TRANSCRIBE_DLQ_NAME \
    TRANSCRIBE_AUDIO_TO_SEARCH_LAMBDA_NAME \
    FASTAPI_LOG_GROUP_NAME
fi

if [[ -z "${TF_VAR_openai_api_key:-}" ]]; then
  warn "TF_VAR_openai_api_key is not set. The current Terraform module may still require this variable during plan/apply."
fi

preflight_aws_identity() {
  log "Step: preflight AWS identity check"

  local identity_json=""
  local account_id=""
  local caller_arn=""

  identity_json="$(aws sts get-caller-identity \
    --region "$AWS_DEFAULT_REGION" \
    --output json)" || \
    fail "AWS CLI is not authenticated or the configured profile is invalid"
  account_id="$(printf '%s' "$identity_json" | jq -r '.Account')"
  caller_arn="$(printf '%s' "$identity_json" | jq -r '.Arn')"

  [[ -n "$account_id" && "$account_id" != "null" ]] || fail "Could not parse AWS account ID from STS response"
  [[ -n "$caller_arn" && "$caller_arn" != "null" ]] || fail "Could not parse AWS caller ARN from STS response"

  log "Authenticated AWS account: $account_id"
  log "Authenticated AWS principal: $caller_arn"
}

preflight_bedrock_model() {
  log "Step: preflight Bedrock model access check"
  aws bedrock get-foundation-model \
    --model-identifier "$BEDROCK_MODEL_ID" \
    --region "$AWS_DEFAULT_REGION" >/dev/null
  log "Bedrock model metadata lookup completed: $BEDROCK_MODEL_ID"
  warn "Bedrock preflight validates model metadata only. It does not prove Model Access entitlement or InvokeModel permission."
}

warn_runtime_prerequisites() {
  warn "Private subnets used by ECS and Lambdas must have NAT or VPC endpoints for ECS, ECR, ECR DKR, S3, CloudWatch Logs, DynamoDB, SQS, STS and Bedrock."
  if [[ "$DOCKER_IMAGE_TAG" == "latest" ]]; then
    warn "DOCKER_IMAGE_TAG is set to 'latest'. Prefer immutable image tags for reproducible deployments."
  fi
}

resolve_plan_file_host_path() {
  if [[ "$TF_PLAN_FILE" = /* ]]; then
    printf '%s\n' "$TF_PLAN_FILE"
  else
    printf '%s/%s\n' "$ONST_TERRAFORM_WORKDIR" "$TF_PLAN_FILE"
  fi
}

create_backend() {
  log "Step: create Terraform backend resources if missing"
  create_bucket_if_missing "$TF_STATE_BUCKET" "$AWS_DEFAULT_REGION"
  create_dynamodb_lock_table_if_missing "$TF_LOCK_TABLE" "$AWS_DEFAULT_REGION"
}

bootstrap_vpc_endpoints() {
  log "Step: bootstrap VPC endpoints for private subnets"
  chmod +x "$SCRIPT_DIR/bootstrap_vpc_endpoints.sh"
  "$SCRIPT_DIR/bootstrap_vpc_endpoints.sh" "$ENV_FILE_PATH"
}

bootstrap_private_nat() {
  log "Step: bootstrap NAT Gateway for private subnets"
  chmod +x "$SCRIPT_DIR/bootstrap_private_nat.sh"
  "$SCRIPT_DIR/bootstrap_private_nat.sh" "$ENV_FILE_PATH"
}

bootstrap_opensearch_domain() {
  log "Step: bootstrap OpenSearch domain"
  chmod +x "$SCRIPT_DIR/bootstrap_opensearch_domain.sh"
  "$SCRIPT_DIR/bootstrap_opensearch_domain.sh" "$ENV_FILE_PATH"
}

create_ecr_repo() {
  log "Step: create ECR repository if missing"
  ensure_ecr_repository "$ECR_REPOSITORY_NAME" "$AWS_DEFAULT_REGION"
}

build_and_push_image() {
  log "Step: build and push FastAPI image"
  local account_id=""
  account_id="$(aws sts get-caller-identity --region "$AWS_DEFAULT_REGION" --query Account --output text)"
  local full_image_uri="$account_id.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$ECR_REPOSITORY_NAME:$DOCKER_IMAGE_TAG"

  pushd "$ONST_API_DIR" >/dev/null
  docker build --platform linux/amd64 -t "$DOCKER_IMAGE_LOCAL_NAME:$DOCKER_IMAGE_TAG" .
  popd >/dev/null

  aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | \
    docker login --username AWS --password-stdin "$account_id.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com"

  docker tag "$DOCKER_IMAGE_LOCAL_NAME:$DOCKER_IMAGE_TAG" "$full_image_uri"
  docker push "$full_image_uri"

  log "Image pushed: $full_image_uri"
}

upload_models() {
  log "Step: sync ML models to S3"
  require_non_empty_dir "$WHISPER_MODEL_LOCAL_DIR"
  require_non_empty_dir "$SEGMENTATION_MODEL_LOCAL_DIR"
  require_non_empty_dir "$SUBJECT_MODEL_LOCAL_DIR"

  aws s3 sync "$WHISPER_MODEL_LOCAL_DIR" "s3://$TRANSCRIBE_BUCKET_NAME/$WHISPER_MODEL_S3_PREFIX"
  aws s3 sync "$SEGMENTATION_MODEL_LOCAL_DIR" "s3://$TRANSCRIBE_BUCKET_NAME/$SEGMENTATION_MODEL_S3_PREFIX"
  aws s3 sync "$SUBJECT_MODEL_LOCAL_DIR" "s3://$TRANSCRIBE_BUCKET_NAME/$SUBJECT_MODEL_S3_PREFIX"
}

create_opensearch_index() {
  log "Step: create OpenSearch index using AWS CLI helper script"
  local index_script="$ONST_ROOT/scripts/create_index._awscli.sh"
  require_file "$index_script"

  chmod +x "$index_script"
  (
    export AWS_PROFILE AWS_DEFAULT_REGION
    export DOMAIN_NAME="$OPENSEARCH_DOMAIN_NAME"
    export INDEX_NAME="$OPENSEARCH_INDEX_NAME"
    cd "$ONST_ROOT"
    "$index_script"
  ) || fail "OpenSearch index creation script failed"
}

prepare_workspace() {
  log "Step: prepare generated Terraform workspace"
  chmod +x "$SCRIPT_DIR/prepare_terraform_workspace.sh"
  "$SCRIPT_DIR/prepare_terraform_workspace.sh" "$ENV_FILE_PATH"
}

terraform_init() {
  log "Step: terraform init"
  require_dir "$ONST_TERRAFORM_WORKDIR"
  terraform -chdir="$ONST_TERRAFORM_WORKDIR" init \
    -input=false \
    -reconfigure \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="key=$TF_STATE_KEY" \
    -backend-config="region=$AWS_DEFAULT_REGION" \
    -backend-config="dynamodb_table=$TF_LOCK_TABLE"
}

terraform_validate() {
  log "Step: terraform validate"
  terraform -chdir="$ONST_TERRAFORM_WORKDIR" validate
}

terraform_plan() {
  log "Step: terraform plan"
  local plan_file_host_path=""
  local plan_dir=""
  plan_file_host_path="$(resolve_plan_file_host_path)"
  plan_dir="${plan_file_host_path%/*}"
  [[ -n "$plan_dir" ]] || plan_dir="$ONST_TERRAFORM_WORKDIR"
  mkdir -p "$plan_dir"
  terraform -chdir="$ONST_TERRAFORM_WORKDIR" plan \
    -input=false \
    -var-file="$TFVARS_FILE" \
    -out="$plan_file_host_path"
}

terraform_apply() {
  log "Step: terraform apply"
  local plan_file_host_path=""
  plan_file_host_path="$(resolve_plan_file_host_path)"
  require_file "$plan_file_host_path"
  if is_true "$TF_AUTO_APPROVE"; then
    terraform -chdir="$ONST_TERRAFORM_WORKDIR" apply -auto-approve "$plan_file_host_path"
  else
    terraform -chdir="$ONST_TERRAFORM_WORKDIR" apply "$plan_file_host_path"
  fi
}

wait_for_ecs_service_stable() {
  log "Waiting for ECS service to reach a stable state"
  local attempt=1
  local service_json=""
  local failure_reason=""
  local service_status=""
  local desired_count=""
  local running_count=""
  local pending_count=""
  local deployments_count=""

  require_non_negative_integer "ECS_STABILITY_MAX_ATTEMPTS" "$ECS_STABILITY_MAX_ATTEMPTS"
  require_non_negative_integer "ECS_STABILITY_SLEEP_SECONDS" "$ECS_STABILITY_SLEEP_SECONDS"

  while [[ "$attempt" -le "$ECS_STABILITY_MAX_ATTEMPTS" ]]; do
    service_json="$(aws ecs describe-services \
      --cluster "$ECS_CLUSTER_NAME" \
      --services "$ECS_SERVICE_NAME" \
      --region "$AWS_DEFAULT_REGION" \
      --output json)" || fail "Could not describe ECS service: $ECS_SERVICE_NAME"

    failure_reason="$(printf '%s' "$service_json" | jq -r '.failures[0].reason // empty')"
    [[ -z "$failure_reason" ]] || fail "ECS service lookup returned a failure: $failure_reason"

    service_status="$(printf '%s' "$service_json" | jq -r '.services[0].status // empty')"
    desired_count="$(printf '%s' "$service_json" | jq -r '.services[0].desiredCount // 0')"
    running_count="$(printf '%s' "$service_json" | jq -r '.services[0].runningCount // 0')"
    pending_count="$(printf '%s' "$service_json" | jq -r '.services[0].pendingCount // 0')"
    deployments_count="$(printf '%s' "$service_json" | jq -r '.services[0].deployments | length')"

    if printf '%s' "$service_json" | jq -e '
      .services[0] as $svc |
      $svc != null and
      ($svc.status == "ACTIVE") and
      ($svc.pendingCount == 0) and
      ($svc.runningCount == $svc.desiredCount) and
      (($svc.deployments | length) == 1) and
      ($svc.deployments | all((.rolloutState // "COMPLETED") == "COMPLETED"))
    ' >/dev/null; then
      log "ECS service reached a stable state"
      return 0
    fi

    log "ECS service not stable yet (attempt $attempt/$ECS_STABILITY_MAX_ATTEMPTS): status=$service_status desired=$desired_count running=$running_count pending=$pending_count deployments=$deployments_count"
    sleep "$ECS_STABILITY_SLEEP_SECONDS"
    ((attempt++)) || true
  done

  local last_event=""
  last_event="$(aws ecs describe-services \
    --cluster "$ECS_CLUSTER_NAME" \
    --services "$ECS_SERVICE_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'services[0].events[0].message' \
    --output text 2>/dev/null || true)"
  fail "ECS service did not reach a stable state after $ECS_STABILITY_MAX_ATTEMPTS attempts. Last reported event: ${last_event:-unavailable}"
}

validate_dynamodb_table() {
  aws dynamodb describe-table \
    --table-name "$TRANSCRIPTIONS_TABLE_NAME" \
    --region "$AWS_DEFAULT_REGION" >/dev/null
  log "DynamoDB table validated: $TRANSCRIPTIONS_TABLE_NAME"
}

validate_sqs_queue() {
  local queue_name="$1"
  local queue_url=""

  queue_url="$(aws sqs get-queue-url \
    --queue-name "$queue_name" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'QueueUrl' \
    --output text)" || fail "Could not resolve SQS queue URL: $queue_name"

  aws sqs get-queue-attributes \
    --queue-url "$queue_url" \
    --attribute-names All \
    --region "$AWS_DEFAULT_REGION" >/dev/null
  log "SQS queue validated: $queue_name"
}

validate_lambda_function() {
  local function_name="$1"

  aws lambda get-function \
    --function-name "$function_name" \
    --region "$AWS_DEFAULT_REGION" >/dev/null
  log "Lambda validated: $function_name"
}

validate_log_group() {
  local log_group_name="$1"
  local resolved_name=""

  resolved_name="$(aws logs describe-log-groups \
    --log-group-name-prefix "$log_group_name" \
    --region "$AWS_DEFAULT_REGION" \
    --output json | \
    jq -r --arg name "$log_group_name" '[.logGroups[]? | select(.logGroupName == $name) | .logGroupName][0] // empty')"

  [[ "$resolved_name" == "$log_group_name" ]] || fail "CloudWatch log group not found: $log_group_name"
  log "CloudWatch log group validated: $log_group_name"
}

validate_health_endpoint() {
  local alb_dns=""
  local attempt=""
  local health_url=""

  alb_dns="$(aws elbv2 describe-load-balancers \
    --names "$ALB_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'LoadBalancers[0].DNSName' \
    --output text)"

  [[ -n "$alb_dns" && "$alb_dns" != "None" ]] || fail "Could not resolve ALB DNS for validation: $ALB_NAME"

  health_url="http://$alb_dns$ALB_HEALTHCHECK_PATH"
  log "Checking FastAPI health endpoint: $health_url"

  attempt=1
  while [[ "$attempt" -le "$POST_DEPLOY_HEALTHCHECK_ATTEMPTS" ]]; do
    if curl --fail --silent --show-error --connect-timeout 10 "$health_url" >/dev/null 2>&1; then
      log "FastAPI health endpoint responded successfully on attempt $attempt"
      return 0
    fi

    log "Health endpoint not ready yet (attempt $attempt/$POST_DEPLOY_HEALTHCHECK_ATTEMPTS)"
    sleep "$POST_DEPLOY_HEALTHCHECK_SLEEP_SECONDS"
    ((attempt++)) || true
  done

  fail "FastAPI health endpoint did not respond after $POST_DEPLOY_HEALTHCHECK_ATTEMPTS attempts. If the ALB is internal, run this validation from a host with VPC access or disable RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION."
}

describe_opensearch_domain_json() {
  local domain_json=""

  domain_json="$(aws opensearch describe-domain \
    --domain-name "$OPENSEARCH_DOMAIN_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --output json 2>/dev/null || true)"

  if [[ -z "$domain_json" ]]; then
    domain_json="$(aws es describe-elasticsearch-domain \
      --domain-name "$OPENSEARCH_DOMAIN_NAME" \
      --region "$AWS_DEFAULT_REGION" \
      --output json 2>/dev/null || true)"
  fi

  [[ -n "$domain_json" ]] || fail "Could not describe OpenSearch domain: $OPENSEARCH_DOMAIN_NAME"
  printf '%s\n' "$domain_json"
}

resolve_opensearch_endpoint() {
  local domain_json=""
  local endpoint=""

  domain_json="$(describe_opensearch_domain_json)"
  endpoint="$(printf '%s' "$domain_json" | jq -r '
    .DomainStatus.Endpoints.vpc //
    .DomainStatus.Endpoint //
    (.DomainStatus.Endpoints | if type == "object" then (to_entries[0].value // empty) else empty end) //
    empty
  ' 2>/dev/null || true)"

  [[ -n "$endpoint" && "$endpoint" != "None" && "$endpoint" != "null" ]] || fail "Could not resolve OpenSearch endpoint for domain: $OPENSEARCH_DOMAIN_NAME"
  printf '%s\n' "$endpoint"
}

validate_opensearch() {
  local domain_json=""
  local opensearch_endpoint=""
  local domain_processing=""
  local index_http_status=""

  domain_json="$(describe_opensearch_domain_json)"
  opensearch_endpoint="$(printf '%s' "$domain_json" | jq -r '
    .DomainStatus.Endpoints.vpc //
    .DomainStatus.Endpoint //
    (.DomainStatus.Endpoints | if type == "object" then (to_entries[0].value // empty) else empty end) //
    empty
  ' 2>/dev/null || true)"
  domain_processing="$(printf '%s' "$domain_json" | jq -r '.DomainStatus.Processing // "false"')"

  [[ -n "$opensearch_endpoint" && "$opensearch_endpoint" != "null" ]] || fail "Could not resolve OpenSearch endpoint for domain: $OPENSEARCH_DOMAIN_NAME"
  [[ "$domain_processing" == "false" ]] || fail "OpenSearch domain is still processing changes: $OPENSEARCH_DOMAIN_NAME"

  index_http_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --connect-timeout 10 -X HEAD "https://$opensearch_endpoint/$OPENSEARCH_INDEX_NAME")"

  case "$index_http_status" in
    200)
      log "OpenSearch domain and index validated: $OPENSEARCH_DOMAIN_NAME / $OPENSEARCH_INDEX_NAME"
      ;;
    403)
      warn "OpenSearch domain is healthy, but index existence could not be validated without signed HTTP access (HTTP 403)."
      ;;
    000)
      warn "OpenSearch domain is healthy, but the endpoint is not reachable from this host for HTTP validation."
      ;;
    404)
      fail "OpenSearch index validation failed for '$OPENSEARCH_INDEX_NAME' (HTTP 404)"
      ;;
    *)
      fail "OpenSearch index validation failed for '$OPENSEARCH_INDEX_NAME' (HTTP $index_http_status)"
      ;;
  esac
}

post_deploy_validations() {
  log "Step: post-deploy validations"

  if ! terraform -chdir="$ONST_TERRAFORM_WORKDIR" output; then
    warn "terraform output failed. The state may be incomplete or the backend may be temporarily unreachable."
  fi

  wait_for_ecs_service_stable
  validate_dynamodb_table
  validate_sqs_queue "$TRANSCRIBE_QUEUE_NAME"
  validate_sqs_queue "$TRANSCRIBE_DLQ_NAME"
  validate_lambda_function "$TRANSCRIBE_AUDIO_TO_SEARCH_LAMBDA_NAME"
  validate_log_group "$FASTAPI_LOG_GROUP_NAME"

  if is_true "$RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION"; then
    validate_health_endpoint
  else
    warn "Skipping ALB health endpoint validation because RUN_POST_DEPLOY_HEALTH_ENDPOINT_VALIDATION=false"
  fi

  if is_true "$RUN_POST_DEPLOY_OPENSEARCH_VALIDATION"; then
    validate_opensearch
  else
    warn "Skipping OpenSearch validation because RUN_POST_DEPLOY_OPENSEARCH_VALIDATION=false"
  fi
}

main() {
  log "Starting ONSTranscribe full deploy"

  warn_runtime_prerequisites

  if is_true "$RUN_PREFLIGHT_AWS_IDENTITY_CHECK"; then
    preflight_aws_identity
  fi

  if is_true "$RUN_PREFLIGHT_BEDROCK_CHECK"; then
    preflight_bedrock_model
  fi

  if is_true "$RUN_BOOTSTRAP_PRIVATE_NAT"; then
    bootstrap_private_nat
  fi

  if is_true "$RUN_BOOTSTRAP_OPENSEARCH_DOMAIN"; then
    bootstrap_opensearch_domain
  fi

  if is_true "$RUN_BOOTSTRAP_VPC_ENDPOINTS"; then
    bootstrap_vpc_endpoints
  fi

  if is_true "$RUN_CREATE_TERRAFORM_BACKEND"; then
    create_backend
  fi

  if is_true "$RUN_CREATE_ECR"; then
    create_ecr_repo
  fi

  if is_true "$RUN_BUILD_PUSH_IMAGE"; then
    build_and_push_image
  fi

  if is_true "$RUN_UPLOAD_MODELS"; then
    upload_models
  fi

  if is_true "$RUN_CREATE_OPENSEARCH_INDEX"; then
    create_opensearch_index
  fi

  if is_true "$RUN_PREPARE_TERRAFORM_WORKSPACE"; then
    prepare_workspace
  fi

  terraform_init

  if is_true "$RUN_TERRAFORM_VALIDATE"; then
    terraform_validate
  fi

  if is_true "$RUN_TERRAFORM_PLAN"; then
    terraform_plan
  fi

  if is_true "$RUN_TERRAFORM_APPLY"; then
    terraform_apply
  fi

  if is_true "$RUN_POST_DEPLOY_VALIDATIONS"; then
    post_deploy_validations
  fi

  log "ONSTranscribe deploy flow completed"
}

main "$@"
