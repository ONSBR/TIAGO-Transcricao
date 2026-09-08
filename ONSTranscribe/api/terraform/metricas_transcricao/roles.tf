resource "aws_iam_role" "calcular_metricas_transcricao_lambda_execution_role" {
  name = "calcular-metricas-transcricao-lambda-execution-role"

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

resource "aws_iam_policy" "calcular_metricas_transcricao_lambda_policy" {
  name        = "ons-transcribe-calcular-metricas-transcricao-lambda-policy"
  description = "Permissões do Lambda de métricas: leitura de groundtruths/áudios e escrita de transcrições/métricas no S3"

  tags = local.default_tags

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["s3:ListBucket"],
        Resource = "arn:aws:s3:::${var.metrics_bucket_name}"
      },
      {
        # Indexação dos áudios de qualidade (metrics_audio_quality_key_paths) no bucket de origem.
        # Apenas listagem: o download dos áudios é feito pela API ONSTranscribe, não pelo Lambda.
        Effect   = "Allow",
        Action   = ["s3:ListBucket"],
        Resource = "arn:aws:s3:::${var.audio_bucket_name}"
      },
      {
        Effect = "Allow",
        Action = ["s3:GetObject"],
        Resource = [
          "arn:aws:s3:::${var.metrics_bucket_name}/${var.metrics_groundtruth_key_path}*",
          "arn:aws:s3:::${var.metrics_bucket_name}/${var.metrics_metadata_groundtruth_key_path}/*",
          "arn:aws:s3:::${var.metrics_bucket_name}/${var.transcriptions_key_path}*",
          "arn:aws:s3:::${var.metrics_bucket_name}/${var.metrics_s3_prefix}/*",
        ]
      },
      {
        Effect = "Allow",
        Action = ["s3:PutObject"],
        Resource = [
          # Manifestos e JSONs de métricas versionados gravados em {metrics_s3_prefix}/...
          "arn:aws:s3:::${var.metrics_bucket_name}/${var.metrics_s3_prefix}/*",
          # NDJSON particionado consumido pelo Athena gravado em {athena_s3_prefix}/...
          "arn:aws:s3:::${var.metrics_bucket_name}/${var.metrics_athena_s3_prefix}/*",
        ]
      },
      {
        Effect   = "Allow",
        Action   = ["lambda:InvokeFunction"],
        Resource = aws_lambda_function.calcular_metricas_transcricao.arn
      },
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${aws_lambda_function.calcular_metricas_transcricao.function_name}:*"
      },
      {
        Effect = "Allow",
        Action = [
          "glue:CreatePartition",
          "glue:GetTable",
          "glue:GetDatabase"
        ],
        Resource = [
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/${var.metrics_athena_database_name}",
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/${var.metrics_athena_database_name}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "calcular_metricas_policy_attach" {
  role       = aws_iam_role.calcular_metricas_transcricao_lambda_execution_role.name
  policy_arn = aws_iam_policy.calcular_metricas_transcricao_lambda_policy.arn
}

resource "aws_iam_role_policy_attachment" "calcular_metricas_vpc_policy_attach" {
  role       = aws_iam_role.calcular_metricas_transcricao_lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
