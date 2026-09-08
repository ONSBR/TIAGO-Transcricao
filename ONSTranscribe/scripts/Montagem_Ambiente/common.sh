#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
DEFAULT_ENV_FILE="$SCRIPT_DIR/.env"

log() {
  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_file() {
  [[ -f "$1" ]] || fail "Required file not found: $1"
}

require_dir() {
  [[ -d "$1" ]] || fail "Required directory not found: $1"
}

require_env() {
  local var_name=""
  for var_name in "$@"; do
    [[ -n "${!var_name:-}" ]] || fail "Environment variable '$var_name' must be set in .env"
  done
}

require_non_negative_integer() {
  local var_name="$1"
  local value="$2"

  [[ "$value" =~ ^[0-9]+$ ]] || fail "$var_name must be a non-negative integer"
}

require_absolute_path() {
  local path_label="$1"
  local path_value="$2"

  [[ "$path_value" = /* ]] || fail "$path_label must be an absolute path"
}

require_safe_workdir_path() {
  local workdir="$1"

  [[ "$workdir" = /* ]] || fail "ONST_TERRAFORM_WORKDIR must be an absolute path"
  [[ "$workdir" != "/" ]] || fail "ONST_TERRAFORM_WORKDIR must not be '/'"
  [[ "$workdir" == *".generated"* || "$workdir" == "$SCRIPT_DIR/"* ]] || \
    fail "ONST_TERRAFORM_WORKDIR must stay under the script directory or contain '.generated'"
}

require_non_empty_dir() {
  local dir_path="$1"
  local first_file=""

  require_dir "$dir_path"
  first_file="$(find "$dir_path" -mindepth 1 -type f -print -quit 2>/dev/null)" || \
    fail "Failed to inspect directory: $dir_path"
  [[ -n "$first_file" ]] || fail "Required directory has no files: $dir_path"
}

is_true() {
  case "${1:-}" in
    true|TRUE|1|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

strip_matching_quotes() {
  local value="$1"

  if [[ "$value" =~ ^\".*\"$ ]]; then
    value="${value:1:${#value}-2}"
    value="${value//\\\\/\\}"
    value="${value//\\\"/\"}"
  elif [[ "$value" =~ ^\'.*\'$ ]]; then
    value="${value:1:${#value}-2}"
  fi

  printf '%s' "$value"
}

load_env() {
  local env_file="${1:-$DEFAULT_ENV_FILE}"
  local line=""
  local key=""
  local value=""
  local lineno=0
  local trimmed=""

  require_file "$env_file"

  while IFS= read -r line || [[ -n "$line" ]]; do
    ((lineno++)) || true

    trimmed="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$trimmed" ]] && continue
    [[ "$trimmed" == \#* ]] && continue

    [[ "$trimmed" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || \
      fail "Malformed .env line $lineno in $env_file: $line"

    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"

    [[ "$value" != *'$('* ]] || fail "Command substitution is not allowed in .env (line $lineno)"
    [[ "$value" != *'`'* ]] || fail "Backticks are not allowed in .env (line $lineno)"
    [[ "$value" != *$'\n'* ]] || fail "Multiline values are not allowed in .env (line $lineno)"

    value="$(strip_matching_quotes "$value")"
    export "$key=$value"
  done < "$env_file"
}

assert_file_contains_literal() {
  local file_path="$1"
  local expected_text="$2"
  local description="$3"

  grep -Fq "$expected_text" "$file_path" || fail "$description was not found in $file_path"
}

assert_file_not_contains_regex() {
  local file_path="$1"
  local regex="$2"
  local description="$3"

  if grep -Eq "$regex" "$file_path"; then
    fail "$description is still present in $file_path"
  fi
}

hcl_escape_string() {
  local value="$1"

  [[ "$value" != *$'\n'* ]] || fail "HCL string values must not contain newlines"
  [[ "$value" != *$'\r'* ]] || fail "HCL string values must not contain carriage returns"

  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"

  printf '%s' "$value"
}

ensure_s3_bucket_hardening() {
  local bucket_name="$1"
  local region="$2"

  aws s3api put-bucket-versioning \
    --bucket "$bucket_name" \
    --versioning-configuration Status=Enabled \
    --region "$region" >/dev/null

  aws s3api put-public-access-block \
    --bucket "$bucket_name" \
    --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
    --region "$region" >/dev/null

  aws s3api put-bucket-encryption \
    --bucket "$bucket_name" \
    --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
    --region "$region" >/dev/null
}

create_bucket_if_missing() {
  local bucket_name="$1"
  local region="$2"
  local head_bucket_output=""

  if head_bucket_output="$(aws s3api head-bucket --bucket "$bucket_name" --region "$region" 2>&1)"; then
    log "S3 backend bucket already exists: $bucket_name"
    ensure_s3_bucket_hardening "$bucket_name" "$region"
    return 0
  fi

  if [[ "$head_bucket_output" == *"Forbidden"* ]] || \
     [[ "$head_bucket_output" == *"AccessDenied"* ]] || \
     [[ "$head_bucket_output" == *"(403)"* ]]; then
    fail "S3 backend bucket '$bucket_name' already exists, but the current AWS identity does not have access to it"
  fi

  if [[ "$head_bucket_output" == *"301"* ]] || \
     [[ "$head_bucket_output" == *"PermanentRedirect"* ]]; then
    fail "S3 backend bucket '$bucket_name' already exists in a different region or is owned by another account"
  fi

  log "Creating S3 backend bucket: $bucket_name"
  if [[ "$region" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$bucket_name" --region "$region" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "$bucket_name" \
      --region "$region" \
      --create-bucket-configuration "LocationConstraint=$region" >/dev/null
  fi

  ensure_s3_bucket_hardening "$bucket_name" "$region"
}

wait_for_dynamodb_table_active() {
  local table_name="$1"
  local region="$2"
  local attempt=1
  local table_status=""

  while [[ "$attempt" -le 30 ]]; do
    table_status="$(aws dynamodb describe-table \
      --table-name "$table_name" \
      --region "$region" \
      --query 'Table.TableStatus' \
      --output text 2>/dev/null || true)"

    if [[ "$table_status" == "ACTIVE" ]]; then
      log "DynamoDB lock table is ACTIVE: $table_name"
      return 0
    fi

    sleep 5
    ((attempt++)) || true
  done

  fail "DynamoDB lock table did not reach ACTIVE state: $table_name"
}

create_dynamodb_lock_table_if_missing() {
  local table_name="$1"
  local region="$2"

  if aws dynamodb describe-table --table-name "$table_name" --region "$region" >/dev/null 2>&1; then
    log "DynamoDB lock table already exists: $table_name"
    wait_for_dynamodb_table_active "$table_name" "$region"
    return 0
  fi

  log "Creating DynamoDB lock table: $table_name"
  aws dynamodb create-table \
    --table-name "$table_name" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$region" >/dev/null

  wait_for_dynamodb_table_active "$table_name" "$region"
}

ensure_ecr_repository() {
  local repository_name="$1"
  local region="$2"

  if aws ecr describe-repositories --repository-names "$repository_name" --region "$region" >/dev/null 2>&1; then
    log "ECR repository already exists: $repository_name"
    return 0
  fi

  log "Creating ECR repository: $repository_name"
  aws ecr create-repository \
    --repository-name "$repository_name" \
    --region "$region" \
    --image-scanning-configuration scanOnPush=true >/dev/null
}

render_ecr_tf() {
  local output_file="$1"
  local image_tag=""

  image_tag="$(hcl_escape_string "$DOCKER_IMAGE_TAG")"

  cat >"$output_file" <<EOF
data "aws_ecr_repository" "fastapi_repo" {
  name = var.ecr_repo_name
}

data "aws_ecr_image" "fastapi_image" {
  repository_name = data.aws_ecr_repository.fastapi_repo.name
  image_tag       = "$image_tag"
}
EOF
}

render_network_tf() {
  local output_file="$1"
  local healthcheck_path=""

  require_non_negative_integer "ALB_HEALTHCHECK_INTERVAL_SECONDS" "$ALB_HEALTHCHECK_INTERVAL_SECONDS"
  require_non_negative_integer "ALB_HEALTHCHECK_TIMEOUT_SECONDS" "$ALB_HEALTHCHECK_TIMEOUT_SECONDS"
  require_non_negative_integer "ALB_HEALTHCHECK_HEALTHY_THRESHOLD" "$ALB_HEALTHCHECK_HEALTHY_THRESHOLD"
  require_non_negative_integer "ALB_HEALTHCHECK_UNHEALTHY_THRESHOLD" "$ALB_HEALTHCHECK_UNHEALTHY_THRESHOLD"

  healthcheck_path="$(hcl_escape_string "$ALB_HEALTHCHECK_PATH")"

  cat >"$output_file" <<EOF
resource "aws_lb_listener" "fastapi_listener" {
  load_balancer_arn = aws_lb.fastapi_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.transcribe_tg.arn
  }
}

resource "aws_security_group" "fastapi_sg" {
  name        = "\${var.project_name}-api-sg-\${var.environment}"
  description = "Allow HTTP traffic"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Allow HTTP inbound traffic from the ALB security group"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_sg.id]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "fastapi-sg-\${var.environment}"
  }
}

resource "aws_lb_target_group" "transcribe_tg" {
  name        = "\${var.project_name}-api-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "$healthcheck_path"
    interval            = $ALB_HEALTHCHECK_INTERVAL_SECONDS
    timeout             = $ALB_HEALTHCHECK_TIMEOUT_SECONDS
    healthy_threshold   = $ALB_HEALTHCHECK_HEALTHY_THRESHOLD
    unhealthy_threshold = $ALB_HEALTHCHECK_UNHEALTHY_THRESHOLD
    port                = "traffic-port"
  }
}
EOF
}

render_lambda_tf() {
  local output_file="$1"
  local recorders_list=""
  local service_type=""
  local transcribe_provider=""

  recorders_list="$(hcl_escape_string "$LAMBDA_RECODERS_LIST")"
  service_type="$(hcl_escape_string "$LAMBDA_SERVICE_TYPE")"
  transcribe_provider="$(hcl_escape_string "$LAMBDA_TRANSCRIBE_PROVIDER")"

  cat >"$output_file" <<EOF
data "archive_file" "transcribe_audio_to_elastic_search_lambda" {
  type        = "zip"
  source_dir  = "\${path.module}/lambdas/transcribe_audio_to_elastic_search"
  output_path = "\${path.module}/lambdas/transcribe_audio_to_elastic_search.zip"
}

resource "aws_lambda_layer_version" "transcribe_layer" {
  layer_name          = "transcribe_layer"
  compatible_runtimes = ["python3.9"]

  filename         = "\${path.module}/lambdas/layers/transcribe_layer.zip"
  source_code_hash = filebase64sha256("\${path.module}/lambdas/layers/transcribe_layer.zip")
}

# Consumo da fila: worker SQS na task ECS. Sem Lambda create_transcribe_job_via_api.

resource "aws_lambda_function" "transcribe_audio_to_elastic_search" {
  function_name    = "transcribe_audio_to_elastic_search"
  role             = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.9"
  filename         = "\${path.module}/lambdas/transcribe_audio_to_elastic_search.zip"
  source_code_hash = filebase64sha256("\${path.module}/lambdas/transcribe_audio_to_elastic_search.zip")
  timeout          = 900

  tags = local.default_tags

  environment {
    variables = {
      BucketName            = var.audio_bucket_name,
      DataAccessRoleArn     = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.arn,
      KeyPath               = var.control_room_key_path,
      index                 = var.index_name,
      MediaFormat           = ".wav",
      RecodersList          = "$recorders_list",
      ServiceType           = "$service_type",
      TranscriptionsKeyPath = var.transcriptions_key_path,
      VocabulariesKeyPath   = var.vocabularies_key_path,
      VocabularyName        = var.vocabulary_name,
      TranscribeProvider    = "$transcribe_provider",
      ONSTranscribeApiUrl   = "http://\${aws_lb.fastapi_alb.dns_name}/api/v1",
      ElasticsearchUri      = var.elastic_search_uri,
      CreateJobQueueName    = replace(aws_sqs_queue.transcribe_queue.name, ".fifo", ""),
      TranscriptionProcessingEnabledSsmParameter = aws_ssm_parameter.transcription_processing_enabled.name
    }
  }

  layers = [aws_lambda_layer_version.transcribe_layer.arn]

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.alb_sg.id]
  }
}
EOF
}

render_ecs_tf() {
  local output_file="$1"

  require_non_negative_integer "ECS_SERVICE_DESIRED_COUNT" "$ECS_SERVICE_DESIRED_COUNT"
  require_non_negative_integer "ECS_AUTOSCALING_MIN_CAPACITY" "$ECS_AUTOSCALING_MIN_CAPACITY"
  require_non_negative_integer "ECS_AUTOSCALING_MAX_CAPACITY" "$ECS_AUTOSCALING_MAX_CAPACITY"
  require_non_negative_integer "ECS_HEALTH_CHECK_GRACE_PERIOD_SECONDS" "$ECS_HEALTH_CHECK_GRACE_PERIOD_SECONDS"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_WEIGHT" "$ECS_CAPACITY_PROVIDER_WEIGHT"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_BASE" "$ECS_CAPACITY_PROVIDER_BASE"

  cat >"$output_file" <<EOF
resource "aws_dynamodb_table" "transcriptions_table" {
  name         = var.transcriptions_table_name
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  tags = {
    Name        = var.transcriptions_table_name
    Environment = var.environment
  }
}

resource "aws_ecs_cluster" "fastapi_cluster" {
  name = "\${var.project_name}-cluster-\${var.environment}"
}

resource "aws_cloudwatch_log_group" "fastapi_log_group" {
  name              = "/ecs/\${var.project_name}-log-group-\${var.environment}"
  retention_in_days = 7
}

resource "aws_ecs_service" "fastapi_service" {
  name                              = "fastapi-service"
  cluster                           = aws_ecs_cluster.fastapi_cluster.id
  task_definition                   = aws_ecs_task_definition.fastapi_task.arn
  desired_count                     = $ECS_SERVICE_DESIRED_COUNT
  propagate_tags                    = "SERVICE"
  health_check_grace_period_seconds = $ECS_HEALTH_CHECK_GRACE_PERIOD_SECONDS

  # Com 1 GPU (cota vCPU G=4 -> 1 g4dn) o redeploy não pode rodar 2 tasks.
  # min=0/max=100 faz o ECS parar a antiga antes de subir a nova (sem deadlock).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.gpu_capacity_provider.name
    weight            = $ECS_CAPACITY_PROVIDER_WEIGHT
    base              = $ECS_CAPACITY_PROVIDER_BASE
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.fastapi_sg.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.transcribe_tg.arn
    container_name   = "fastapi-container"
    container_port   = 80
  }

  depends_on = [
    aws_lb_target_group.transcribe_tg,
    aws_cloudwatch_log_group.fastapi_log_group,
    aws_autoscaling_group.gpu_ecs_asg,
    aws_ecs_cluster_capacity_providers.fastapi_cluster_capacity_providers,
  ]
}

resource "aws_ecs_task_definition" "fastapi_task" {
  family                   = "fastapi-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.fastapi_task_execution_role.arn
  task_role_arn            = aws_iam_role.fastapi_task_execution_role.arn
  cpu                      = "4096"
  memory                   = "15360"

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "fastapi-container"
      image     = data.aws_ecr_image.fastapi_image.image_uri
      essential = true

      resourceRequirements = [
        {
          type  = "GPU"
          value = "1"
        }
      ]

      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
        }
      ]

      environment = [
        {
          name  = "TRANSCRIBE_S3_NAME"
          value = var.bucket_name
        },
        {
          name  = "TRANSCRIBE_S3_MODEL_PATH"
          value = var.transcribe_s3_model_path
        },
        {
          name  = "TRANSCRIBE_S3_SEGMENTATION_MODEL_PATH"
          value = var.transcribe_s3_segmentation_model_path
        },
        {
          name  = "TRANSCRIBE_S3_SUBJ_CLASSIFIER_MODEL_PATH"
          value = var.transcribe_s3_subj_classification_model_path
        },
        {
          name  = "TRANSCRIPTIONS_TABLE"
          value = aws_dynamodb_table.transcriptions_table.name
        },
        {
          name  = "TRANSCRIBE_BEDROCK_MODEL"
          value = var.transcribe_bedrock_model
        },
        {
          name  = "RUN_BEAUTIFY_PROMPT_ID"
          value = aws_bedrockagent_prompt.run_beautify_prompt.id
        },
        {
          name  = "RUN_BEAUTIFY_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.run_beautify_prompt.version
        },
        {
          name  = "RUN_BEAUTIFY_SYSTEM_PROMPT_ID"
          value = aws_bedrockagent_prompt.run_beautify_system_prompt.id
        },
        {
          name  = "RUN_BEAUTIFY_SYSTEM_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.run_beautify_system_prompt.version
        },
        {
          name  = "KEY_ENTITIES_PROMPT_ID"
          value = aws_bedrockagent_prompt.key_entities_prompt.id
        },
        {
          name  = "KEY_ENTITIES_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.key_entities_prompt.version
        },
        {
          name  = "SUMMARY_PROMPT_ID"
          value = aws_bedrockagent_prompt.summary_prompt.id
        },
        {
          name  = "SUMMARY_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.summary_prompt.version
        },
        {
          name  = "GLOSSARY_PROMPT_ID"
          value = aws_bedrockagent_prompt.technical_glossary_prompt.id
        },
        {
          name  = "GLOSSARY_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.technical_glossary_prompt.version
        },
        {
          name  = "SUBJECT_PROMPT_ID"
          value = aws_bedrockagent_prompt.get_audio_subject_prompt.id
        },
        {
          name  = "SUBJECT_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.get_audio_subject_prompt.version
        },
        {
          name  = "OPENAI_API_KEY"
          value = var.openai_api_key
        },
        {
          name  = "INSTALLATION_DISAMBIGUATION_PROMPT_ID"
          value = aws_bedrockagent_prompt.installation_disambiguation_prompt.id
        },
        {
          name  = "INSTALLATION_DISAMBIGUATION_PROMPT_VERSION"
          value = aws_bedrockagent_prompt.installation_disambiguation_prompt.version
        },
        {
          name  = "INSTALLATIONS_LIST_S3_KEY"
          value = var.installations_list_s3_key
        },
        {
          name  = "INSTALLATION_MATCH_THRESHOLD_HIGH"
          value = tostring(var.installation_match_threshold_high)
        },
        {
          name  = "INSTALLATION_MATCH_THRESHOLD_LOW"
          value = tostring(var.installation_match_threshold_low)
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.fastapi_log_group.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

resource "aws_appautoscaling_target" "fastapi_scaling_target" {
  max_capacity       = $ECS_AUTOSCALING_MAX_CAPACITY
  min_capacity       = $ECS_AUTOSCALING_MIN_CAPACITY
  resource_id        = "service/\${aws_ecs_cluster.fastapi_cluster.name}/\${aws_ecs_service.fastapi_service.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "fastapi_scaling_policy_cpu" {
  name               = "\${var.project_name}-scaling-policy-cpu-\${var.environment}"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.fastapi_scaling_target.resource_id
  scalable_dimension = "ecs:service:DesiredCount"
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    target_value       = 80.0
    scale_in_cooldown  = 80
    scale_out_cooldown = 80
  }
}

resource "aws_appautoscaling_policy" "fastapi_scaling_policy_memory" {
  name               = "\${var.project_name}-scaling-policy-memory-\${var.environment}"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.fastapi_scaling_target.resource_id
  scalable_dimension = "ecs:service:DesiredCount"
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }

    target_value       = 75.0
    scale_in_cooldown  = 80
    scale_out_cooldown = 80
  }
}
EOF
}

render_gpu_support_tf() {
  local output_file="$1"
  local gpu_ecs_ami_id=""
  local gpu_ec2_instance_type=""

  require_non_negative_integer "GPU_ASG_MIN_SIZE" "$GPU_ASG_MIN_SIZE"
  require_non_negative_integer "GPU_ASG_MAX_SIZE" "$GPU_ASG_MAX_SIZE"
  require_non_negative_integer "GPU_ASG_DESIRED_CAPACITY" "$GPU_ASG_DESIRED_CAPACITY"
  require_non_negative_integer "GPU_ASG_HEALTH_CHECK_GRACE_PERIOD_SECONDS" "$GPU_ASG_HEALTH_CHECK_GRACE_PERIOD_SECONDS"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_TARGET_CAPACITY" "$ECS_CAPACITY_PROVIDER_TARGET_CAPACITY"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_MIN_SCALING_STEP" "$ECS_CAPACITY_PROVIDER_MIN_SCALING_STEP"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_MAX_SCALING_STEP" "$ECS_CAPACITY_PROVIDER_MAX_SCALING_STEP"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_WEIGHT" "$ECS_CAPACITY_PROVIDER_WEIGHT"
  require_non_negative_integer "ECS_CAPACITY_PROVIDER_BASE" "$ECS_CAPACITY_PROVIDER_BASE"

  gpu_ecs_ami_id="$(hcl_escape_string "$GPU_ECS_AMI_ID")"
  gpu_ec2_instance_type="$(hcl_escape_string "$GPU_EC2_INSTANCE_TYPE")"

  cat >"$output_file" <<EOF
resource "aws_iam_role" "gpu_ecs_instance_role" {
  name = "\${var.project_name}-ecs-instance-role-\${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Effect = "Allow",
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = local.default_tags
}

resource "aws_iam_role_policy_attachment" "gpu_ecs_instance_role_ecs_attach" {
  role       = aws_iam_role.gpu_ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "gpu_ecs_instance_role_ssm_attach" {
  role       = aws_iam_role.gpu_ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "gpu_ecs_instance_profile" {
  name = "\${var.project_name}-ecs-instance-profile-\${var.environment}"
  role = aws_iam_role.gpu_ecs_instance_role.name
}

resource "aws_security_group" "gpu_ecs_instances_sg" {
  name        = "\${var.project_name}-ecs-instance-sg-\${var.environment}"
  description = "Security group for ECS GPU container instances"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.default_tags
}

resource "aws_launch_template" "gpu_ecs_launch_template" {
  name                   = "\${var.project_name}-gpu-lt-\${var.environment}"
  image_id               = "$gpu_ecs_ami_id"
  instance_type          = "$gpu_ec2_instance_type"
  update_default_version = true

  # Volume root maior: a imagem CUDA+torch + camadas + modelos baixados em runtime
  # estouram o default da AMI (~30GB) -> CannotPullContainerError "no space left on device".
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = ${GPU_ROOT_VOLUME_SIZE_GB:-100}
      volume_type           = "gp3"
      delete_on_termination = true
    }
  }

  iam_instance_profile {
    name = aws_iam_instance_profile.gpu_ecs_instance_profile.name
  }

  vpc_security_group_ids = [aws_security_group.gpu_ecs_instances_sg.id]

  monitoring {
    enabled = true
  }

  user_data = base64encode(<<-EOT
#!/bin/bash
echo ECS_CLUSTER=\${aws_ecs_cluster.fastapi_cluster.name} >> /etc/ecs/ecs.config
echo ECS_ENABLE_GPU_SUPPORT=true >> /etc/ecs/ecs.config
EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.default_tags, {
      Name = "\${var.project_name}-gpu-node-\${var.environment}"
    })
  }

  tag_specifications {
    resource_type = "volume"
    tags = local.default_tags
  }

  tags = local.default_tags
}

resource "aws_autoscaling_group" "gpu_ecs_asg" {
  name                      = "\${var.project_name}-gpu-asg-\${var.environment}"
  min_size                  = $GPU_ASG_MIN_SIZE
  max_size                  = $GPU_ASG_MAX_SIZE
  desired_capacity          = $GPU_ASG_DESIRED_CAPACITY
  health_check_type         = "EC2"
  health_check_grace_period = $GPU_ASG_HEALTH_CHECK_GRACE_PERIOD_SECONDS
  vpc_zone_identifier       = var.private_subnet_ids

  launch_template {
    id      = aws_launch_template.gpu_ecs_launch_template.id
    version = "\$Latest"
  }

  tag {
    key                 = "Name"
    value               = "\${var.project_name}-gpu-node-\${var.environment}"
    propagate_at_launch = true
  }

  tag {
    key                 = "Solution"
    value               = "Transcricao"
    propagate_at_launch = true
  }

  tag {
    key                 = "Ambiente"
    value               = var.environment
    propagate_at_launch = true
  }

  tag {
    key                 = "CreatedBy"
    value               = "Terraform"
    propagate_at_launch = true
  }

  # O ECS (capacity provider) adiciona a tag AmazonECSManaged ao ASG após a criação.
  # Ignorar mudanças de tag evita drift perpétuo no plan/apply.
  lifecycle {
    ignore_changes = [tag]
  }
}

resource "aws_ecs_capacity_provider" "gpu_capacity_provider" {
  name = "\${var.project_name}-gpu-capacity-provider-\${var.environment}"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.gpu_ecs_asg.arn
    managed_termination_protection = "DISABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = $ECS_CAPACITY_PROVIDER_TARGET_CAPACITY
      minimum_scaling_step_size = $ECS_CAPACITY_PROVIDER_MIN_SCALING_STEP
      maximum_scaling_step_size = $ECS_CAPACITY_PROVIDER_MAX_SCALING_STEP
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "fastapi_cluster_capacity_providers" {
  cluster_name       = aws_ecs_cluster.fastapi_cluster.name
  capacity_providers = [aws_ecs_capacity_provider.gpu_capacity_provider.name]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.gpu_capacity_provider.name
    weight            = $ECS_CAPACITY_PROVIDER_WEIGHT
    base              = $ECS_CAPACITY_PROVIDER_BASE
  }
}
EOF
}
