resource "aws_iam_role" "fastapi_task_execution_role" {
  name = "${var.project_name}-task-execution-role"

  tags = local.default_tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Effect = "Allow",
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fastapi_task_execution_role_policy" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_policy" "fastapi_s3_policy" {
  name        = "${var.project_name}-fastapi-s3-policy"
  description = "Policy to allow ECS task to access S3"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ],
        Resource = [
          "arn:aws:s3:::${var.bucket_name}",
          "arn:aws:s3:::${var.bucket_name}/*",
          "arn:aws:s3:::${var.raw_bucket_name}/*",
        ]
      },
      {
        Effect   = "Allow",
        Action   = ["s3:GetObject"],
        Resource = "arn:aws:s3:::${var.bucket_name}/${var.installations_list_s3_key}"
      }
    ]
  })
}

# Acesso da API/ECS ao bucket de métricas — criado somente quando a solução de
# métricas está habilitada. O Lambda calcular_metricas_transcricao instrui a API
# a ler os áudios de teste e gravar as transcrições neste bucket (distinto de
# bucket_name), então a task role da API precisa de leitura/escrita aqui.
resource "aws_iam_policy" "fastapi_metrics_bucket_policy" {
  count = var.deploy_metricas_solution ? 1 : 0

  name        = "${var.project_name}-fastapi-metrics-bucket-policy"
  description = "Permite ao ECS/API ler áudios de teste e gravar transcrições no metrics_bucket"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ],
        Resource = [
          "arn:aws:s3:::${var.metrics_bucket_name}",
          "arn:aws:s3:::${var.metrics_bucket_name}/*",
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fastapi_metrics_bucket_attach" {
  count      = var.deploy_metricas_solution ? 1 : 0
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = aws_iam_policy.fastapi_metrics_bucket_policy[0].arn
}

resource "aws_iam_policy" "fastapi_dynamodb_policy" {
  name        = "${var.project_name}-fastapi-dynamodb-policy"
  description = "Policy to allow ECS task to access DynamoDB Transcriptions table"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ],
        Resource = [
          aws_dynamodb_table.transcriptions_table.arn
        ]
      }
    ]
  })
}

resource "aws_iam_policy" "fastapi_sqs_policy" {
  name        = "${var.project_name}-fastapi-sqs-policy"
  description = "Permite a task ECS consumir a fila de transcrição (worker desacoplado)"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes"
        ],
        Resource = [
          aws_sqs_queue.transcribe_queue.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fastapi_s3_policy_attach" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = aws_iam_policy.fastapi_s3_policy.arn
}

resource "aws_iam_role_policy_attachment" "ecs_autoscaling_policy" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceAutoscaleRole"
}

resource "aws_iam_role_policy_attachment" "bedrock_policy_attach" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

resource "aws_iam_role_policy_attachment" "fastapi_dynamodb_policy_attach" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = aws_iam_policy.fastapi_dynamodb_policy.arn
}

resource "aws_iam_role_policy_attachment" "fastapi_sqs_policy_attach" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = aws_iam_policy.fastapi_sqs_policy.arn
}

resource "aws_iam_policy" "fastapi_cloudwatch_policy" {
  name        = "${var.project_name}-fastapi-cloudwatch-policy"
  description = "Permite a task ECS publicar métricas (utilização de GPU) no CloudWatch"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["cloudwatch:PutMetricData"],
        Resource = "*",
        # PutMetricData não suporta resource-level; escopa pelo namespace.
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = var.project_name
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fastapi_cloudwatch_policy_attach" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = aws_iam_policy.fastapi_cloudwatch_policy.arn
}

resource "aws_iam_policy" "fastapi_ssm_llm_models_policy" {
  name        = "${var.project_name}-fastapi-ssm-llm-models-policy"
  description = "Permite a task ECS ler o JSON de modelos LLM por etapa no Parameter Store"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ],
        Resource = aws_ssm_parameter.llm_step_models.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "fastapi_ssm_llm_models_attach" {
  role       = aws_iam_role.fastapi_task_execution_role.name
  policy_arn = aws_iam_policy.fastapi_ssm_llm_models_policy.arn
}

resource "aws_iam_role" "transcribe_audio_to_elastic_search_lambda_execution_role" {
  name = "${var.project_name}-audio-search-lambda-role"

  tags = local.default_tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Effect = "Allow",
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# resource "aws_iam_role" "movimentacao_arquivos_wav_sala_de_controle_lambda_execution_role" {
#   name = "movimentacao-arquivos-wav-sala-de-controle-lambda-execution-role"

#   assume_role_policy = jsonencode({
#     Version = "2012-10-17",
#     Statement = [
#       {
#         Action = "sts:AssumeRole",
#         Effect = "Allow",
#         Principal = {
#           Service = "lambda.amazonaws.com"
#         }
#       }
#     ]
#   })
# }

# resource "aws_iam_policy" "movimentacao_arquivos_wav_sala_de_controle_lambda_policy" {
#   name = "movimentacao-arquivos-wav-sala-de-controle-lambda-policy"

#   policy = jsonencode({
#     Version = "2012-10-17"
#     Statement = [
#       {
#         Effect = "Allow",
#         Action = [
#           "s3:GetObject",
#           "s3:DeleteObject"
#         ]
#         Resource = "arn:aws:s3:::${var.gateway_bucket}/*"
#       },
#       {
#         Effect = "Allow",
#         Action = ["s3:PutObject"],
#         Resource = "arn:aws:s3:::${var.audio_bucket_name}/${trim(var.control_room_key_path, "/")}/*"
#       }
#     ]
#   })
# }

# resource "aws_iam_role_policy_attachment" "movimentacao_arquivos_wav_sala_de_controle_attachment" {
#   role = aws_iam_role.movimentacao_arquivos_wav_sala_de_controle_lambda_execution_role.name
#   policy_arn = aws_iam_policy.movimentacao_arquivos_wav_sala_de_controle_lambda_policy.arn
# }

resource "aws_iam_policy" "transcribe_audio_to_elastic_search_lambda_policy" {
  name = "${var.project_name}-audio-to-elastic-search-lambda-policy"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["s3:ListBucket"],
        Resource = "arn:aws:s3:::${var.raw_bucket_name}"
      },
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ],
        Resource = [
          "arn:aws:s3:::${var.raw_bucket_name}/${var.control_room_key_path}*",
          "arn:aws:s3:::${var.bucket_name}/${var.transcriptions_key_path}*"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "transcribe:ListTranscriptionJobs",
          "transcribe:StartTranscriptionJob",
          "transcribe:GetTranscriptionJob",
          "transcribe:DeleteTranscriptionJob"
        ],
        Resource = "arn:aws:transcribe:${var.region}:${data.aws_caller_identity.current.account_id}:transcription-job/*"
      },
      {
        Effect = "Allow",
        Action = [
          "es:ESHttpGet",
          "es:ESHttpPut",
          "es:ESHttpPost",
          "es:ESHttpDelete",
          "es:ESHttpPatch"
        ],
        Resource = [
          "arn:aws:es:${var.region}:${data.aws_caller_identity.current.account_id}:domain/${var.domain_name}",
          "arn:aws:es:${var.region}:${data.aws_caller_identity.current.account_id}:domain/${var.domain_name}/*"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${aws_lambda_function.transcribe_audio_to_elastic_search.function_name}:*"
      },
      {
        Effect   = "Allow",
        Action   = ["iam:PassRole"],
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-access-role"
      },
      {
        Effect   = "Allow",
        Action   = ["sqs:SendMessage", "sqs:GetQueueUrl"],
        Resource = "arn:aws:sqs:${var.region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect = "Allow",
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ],
        Resource = aws_ssm_parameter.transcription_processing_enabled.arn
      },
      # {
      #   Effect = "Allow",
      #   Action = [
      #     "dynamodb:PutItem",
      #     "dynamodb:UpdateItem"
      #   ],
      #   Resource = aws_dynamodb_table.transcriptions_table.arn
      # }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach_transcribe_policy" {
  role       = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.name
  policy_arn = aws_iam_policy.transcribe_audio_to_elastic_search_lambda_policy.arn
}

resource "aws_iam_role_policy_attachment" "lambda_network_policy_attach_transcribe" {
  role       = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_sqs_attachment_transcribe" {
  role       = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSQSFullAccess"
}
