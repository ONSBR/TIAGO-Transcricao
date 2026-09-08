#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE_PATH="${1:-$DEFAULT_ENV_FILE}"
load_env "$ENV_FILE_PATH"

require_command cp
require_command rm
require_command mkdir
require_command grep
require_command terraform

require_env \
  ONST_TERRAFORM_SOURCE_DIR \
  ONST_TERRAFORM_WORKDIR \
  TFVARS_FILE \
  DOCKER_IMAGE_TAG \
  ECS_SERVICE_DESIRED_COUNT \
  ECS_AUTOSCALING_MIN_CAPACITY \
  ECS_AUTOSCALING_MAX_CAPACITY \
  ECS_HEALTH_CHECK_GRACE_PERIOD_SECONDS \
  ECS_CAPACITY_PROVIDER_TARGET_CAPACITY \
  ECS_CAPACITY_PROVIDER_MIN_SCALING_STEP \
  ECS_CAPACITY_PROVIDER_MAX_SCALING_STEP \
  ECS_CAPACITY_PROVIDER_WEIGHT \
  ECS_CAPACITY_PROVIDER_BASE \
  ALB_HEALTHCHECK_PATH \
  ALB_HEALTHCHECK_INTERVAL_SECONDS \
  ALB_HEALTHCHECK_TIMEOUT_SECONDS \
  ALB_HEALTHCHECK_HEALTHY_THRESHOLD \
  ALB_HEALTHCHECK_UNHEALTHY_THRESHOLD \
  GPU_ECS_AMI_ID \
  GPU_EC2_INSTANCE_TYPE \
  GPU_ASG_MIN_SIZE \
  GPU_ASG_MAX_SIZE \
  GPU_ASG_DESIRED_CAPACITY \
  GPU_ASG_HEALTH_CHECK_GRACE_PERIOD_SECONDS \
  LAMBDA_RECODERS_LIST \
  LAMBDA_SERVICE_TYPE \
  LAMBDA_TRANSCRIBE_PROVIDER

require_dir "$ONST_TERRAFORM_SOURCE_DIR"
require_file "$TFVARS_FILE"
require_absolute_path "TFVARS_FILE" "$TFVARS_FILE"
require_safe_workdir_path "$ONST_TERRAFORM_WORKDIR"

log "Preparing generated Terraform workspace: $ONST_TERRAFORM_WORKDIR"
rm -rf "$ONST_TERRAFORM_WORKDIR"
mkdir -p "$ONST_TERRAFORM_WORKDIR"
cp -R "$ONST_TERRAFORM_SOURCE_DIR"/. "$ONST_TERRAFORM_WORKDIR"/

ECR_FILE="$ONST_TERRAFORM_WORKDIR/ecr.tf"
ECS_FILE="$ONST_TERRAFORM_WORKDIR/ecs.tf"
NETWORK_FILE="$ONST_TERRAFORM_WORKDIR/network.tf"
LAMBDA_FILE="$ONST_TERRAFORM_WORKDIR/lambda.tf"
GPU_FILE="$ONST_TERRAFORM_WORKDIR/zz_generated_gpu_support.tf"

log "Rendering corrected Terraform files into generated workspace"
render_ecr_tf "$ECR_FILE"
render_ecs_tf "$ECS_FILE"
render_network_tf "$NETWORK_FILE"
render_lambda_tf "$LAMBDA_FILE"
render_gpu_support_tf "$GPU_FILE"

log "Formatting generated Terraform files"
terraform -chdir="$ONST_TERRAFORM_WORKDIR" fmt >/dev/null

log "Validating generated workspace content"
assert_file_contains_literal "$ECR_FILE" "image_tag       = \"$DOCKER_IMAGE_TAG\"" "ECR image tag override"
assert_file_contains_literal "$ECS_FILE" "desired_count                     = $ECS_SERVICE_DESIRED_COUNT" "ECS desired_count override"
assert_file_contains_literal "$ECS_FILE" "capacity_provider_strategy {" "ECS capacity provider strategy"
assert_file_not_contains_regex "$ECS_FILE" 'launch_type\s*=' "ECS launch_type attribute"
assert_file_contains_literal "$ECS_FILE" "health_check_grace_period_seconds = $ECS_HEALTH_CHECK_GRACE_PERIOD_SECONDS" "ECS health check grace period"
assert_file_contains_literal "$ECS_FILE" "deployment_minimum_healthy_percent = 0" "ECS deployment minimum healthy percent"
assert_file_contains_literal "$ECS_FILE" "deployment_maximum_percent         = 100" "ECS deployment maximum percent"
assert_file_contains_literal "$ECS_FILE" "ignore_changes = [desired_count]" "ECS lifecycle ignore_changes"
assert_file_contains_literal "$ECS_FILE" "aws_autoscaling_group.gpu_ecs_asg," "ECS dependency on GPU ASG"
assert_file_contains_literal "$ECS_FILE" "aws_ecs_cluster_capacity_providers.fastapi_cluster_capacity_providers," "ECS dependency on cluster capacity providers"
assert_file_contains_literal "$NETWORK_FILE" "path                = \"$ALB_HEALTHCHECK_PATH\"" "ALB health check path override"
assert_file_contains_literal "$NETWORK_FILE" "interval            = $ALB_HEALTHCHECK_INTERVAL_SECONDS" "ALB health check interval override"
assert_file_contains_literal "$NETWORK_FILE" "timeout             = $ALB_HEALTHCHECK_TIMEOUT_SECONDS" "ALB health check timeout override"
assert_file_contains_literal "$NETWORK_FILE" "healthy_threshold   = $ALB_HEALTHCHECK_HEALTHY_THRESHOLD" "ALB healthy threshold override"
assert_file_contains_literal "$NETWORK_FILE" "unhealthy_threshold = $ALB_HEALTHCHECK_UNHEALTHY_THRESHOLD" "ALB unhealthy threshold override"
assert_file_contains_literal "$LAMBDA_FILE" "RecodersList                               = \"$LAMBDA_RECODERS_LIST\"" "Lambda RecodersList override"
assert_file_contains_literal "$LAMBDA_FILE" "ServiceType                                = \"$LAMBDA_SERVICE_TYPE\"" "Lambda ServiceType override"
assert_file_contains_literal "$LAMBDA_FILE" "TranscribeProvider                         = \"$LAMBDA_TRANSCRIBE_PROVIDER\"" "Lambda TranscribeProvider override"
assert_file_not_contains_regex "$LAMBDA_FILE" 'Teste\s*=' "Lambda Teste variable"
assert_file_contains_literal "$LAMBDA_FILE" 'source_code_hash = filebase64sha256("${path.module}/lambdas/transcribe_audio_to_elastic_search.zip")' "Lambda ES source_code_hash"
assert_file_contains_literal "$GPU_FILE" "resource \"aws_launch_template\" \"gpu_ecs_launch_template\"" "GPU launch template resource"
assert_file_contains_literal "$GPU_FILE" "resource \"aws_autoscaling_group\" \"gpu_ecs_asg\"" "GPU autoscaling group resource"
assert_file_contains_literal "$GPU_FILE" "resource \"aws_ecs_capacity_provider\" \"gpu_capacity_provider\"" "GPU ECS capacity provider resource"
assert_file_contains_literal "$GPU_FILE" "resource \"aws_ecs_cluster_capacity_providers\" \"fastapi_cluster_capacity_providers\"" "GPU ECS cluster capacity provider binding"

log "Generated Terraform workspace is ready"
log "Workspace: $ONST_TERRAFORM_WORKDIR"
