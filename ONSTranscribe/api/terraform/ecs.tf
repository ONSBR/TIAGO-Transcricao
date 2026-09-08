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
  name = "${var.project_name}-cluster-${var.environment}"
}

resource "aws_cloudwatch_log_group" "fastapi_log_group" {
  name              = "/ecs/${var.project_name}-log-group-${var.environment}"
  retention_in_days = 7
}

resource "aws_ecs_service" "fastapi_service" {
  name                 = "fastapi-task-gpu"
  cluster              = aws_ecs_cluster.fastapi_cluster.id
  task_definition      = aws_ecs_task_definition.fastapi_task.arn
  desired_count        = 1
  force_new_deployment = true
  # Permite o destroy do service sem scale-down manual (ver Guia_Destroy_Ambiente.md).
  force_delete = true
  # Espera o deployment estabilizar antes do apply seguir (troca de strategy/CP).
  wait_for_steady_state = true
  propagate_tags        = "SERVICE"

  capacity_provider_strategy {
    # SSOT: capacity_provider.tf (Managed Instances GPU-ons-transcribe-<env>).
    capacity_provider = aws_ecs_capacity_provider.transcribe_gpu.name
    weight            = 1
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
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

  lifecycle {
    # O autoscaling (Camada 1, autoscaling.tf) gerencia o desired_count.
    # Sem isso o terraform resetaria pra 1 a cada apply, brigando com o scaling.
    # Pause (transcription_processing_enabled=false): min=max=0 no AS força desired→0.
    ignore_changes = [desired_count]
  }

  depends_on = [
    aws_lb_target_group.transcribe_tg,
    aws_cloudwatch_log_group.fastapi_log_group,
    aws_ecs_capacity_provider.transcribe_gpu,
  ]
}
resource "aws_ecs_task_definition" "fastapi_task" {
  family                   = "fastapi-task-gpu"
  network_mode             = "awsvpc"
  requires_compatibilities = ["MANAGED_INSTANCES"]
  execution_role_arn       = aws_iam_role.fastapi_task_execution_role.arn
  task_role_arn            = aws_iam_role.fastapi_task_execution_role.arn
  # Alinhado a var.gpu_instance_type (default g4dn.xlarge: 4 vCPU, 16 GiB).
  cpu    = "4096"
  memory = "15000"

  # Configuração para usar GPU
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name              = "fastapi-container"
      image             = "${data.aws_ecr_repository.fastapi_repo.repository_url}:latest"
      essential         = true
      memory            = 14336
      memoryReservation = 14336

      # Configuração de GPU
      resourceRequirements = [
        {
          type  = "GPU"
          value = "1" # 1 GPU (g5.xlarge tem 1 GPU A10G - 24GB VRAM)
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
          name  = "TRANSCRIPTIONS_TABLE"
          value = aws_dynamodb_table.transcriptions_table.name
        },
        {
          name  = "TRANSCRIBE_BEDROCK_MODEL"
          value = var.transcribe_bedrock_model
        },
        {
          name  = "LLM_STEP_MODELS_SSM_PARAMETER"
          value = aws_ssm_parameter.llm_step_models.name
        },
        {
          name  = "LLM_MODEL_BEAUTIFY"
          value = var.llm_model_beautify
        },
        {
          name  = "LLM_MODEL_ENTITIES"
          value = var.llm_model_entities
        },
        {
          name  = "LLM_MODEL_SUBJECT"
          value = var.llm_model_subject
        },
        {
          name  = "LLM_MODEL_SUMMARY"
          value = var.llm_model_summary
        },
        {
          name  = "LLM_FALLBACK_MODEL"
          value = var.llm_fallback_model
        },
        {
          # Casa com a condicao cloudwatch:namespace em roles.tf. Sem isto, o codigo
          # publicaria num namespace que a policy nao autoriza.
          name  = "GPU_METRICS_NAMESPACE"
          value = var.project_name
        },
        {
          name  = "LLM_MAX_TOKENS_BEAUTIFY"
          value = tostring(var.llm_max_tokens_beautify)
        },
        {
          name  = "LLM_MAX_TOKENS_ENTITIES"
          value = tostring(var.llm_max_tokens_entities)
        },
        {
          name  = "LLM_MAX_TOKENS_SUBJECT"
          value = tostring(var.llm_max_tokens_subject)
        },
        {
          name  = "LLM_MAX_TOKENS_SUMMARY"
          value = tostring(var.llm_max_tokens_summary)
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
          name  = "HF_TOKEN"
          value = var.hf_token
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
        },
        {
          name  = "TRANSCRIBE_SQS_QUEUE_URL"
          value = aws_sqs_queue.transcribe_queue.url
        },
        {
          name  = "TRANSCRIPTIONS_KEY_PATH"
          value = var.transcriptions_key_path
        },
        {
          # Bucket dos áudios — o mesmo que o producer/consumer usam (audio_bucket_name).
          # NÃO é o bucket dos modelos (TRANSCRIBE_S3_NAME); o worker baixa o áudio daqui.
          name  = "TRANSCRIBE_AUDIO_BUCKET"
          value = var.audio_bucket_name
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

