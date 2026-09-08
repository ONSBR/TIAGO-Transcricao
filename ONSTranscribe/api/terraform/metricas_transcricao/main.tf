data "archive_file" "calcular_metricas_transcricao_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/calcular_metricas_transcricao"
  output_path = "${path.module}/../lambdas/calcular_metricas_transcricao.zip"
}

# PRÉ-REQUISITO: gerar o zip do layer antes do terraform apply:
#   pip install jiwer openpyxl -t /tmp/metricas_layer/python
#   cd /tmp/metricas_layer && zip -r metricas_layer.zip python/
#   cp /tmp/metricas_layer/metricas_layer.zip ../lambdas/layers/metricas_layer.zip
resource "aws_lambda_layer_version" "metricas_layer" {
  layer_name          = "metricas_layer"
  compatible_runtimes = ["python3.9"]

  filename         = "${path.module}/../lambdas/layers/metricas_layer.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambdas/layers/metricas_layer.zip")
}

resource "aws_lambda_function" "calcular_metricas_transcricao" {
  function_name    = "calcular_metricas_transcricao"
  role             = aws_iam_role.calcular_metricas_transcricao_lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.9"
  filename         = "${path.module}/../lambdas/calcular_metricas_transcricao.zip"
  source_code_hash = data.archive_file.calcular_metricas_transcricao_lambda.output_base64sha256
  # Timeout máximo do Lambda (15 min). Cada áudio não transcrito pode levar até 10 min na API.
  timeout = 900

  tags = local.default_tags

  environment {
    variables = {
      BUCKET_NAME                   = var.metrics_bucket_name
      AUDIO_BUCKET_NAME             = var.audio_bucket_name
      ONS_TRANS_API_URL             = "http://${var.alb_dns_name}"
      TRANSCRIPTIONS_KEY_PATH       = var.transcriptions_key_path
      GROUNDTRUTH_KEY_PATH          = var.metrics_groundtruth_key_path
      AUDIO_KEY_PATHS               = var.metrics_audio_quality_key_paths
      METRICS_S3_PREFIX             = var.metrics_s3_prefix
      ATHENA_S3_PREFIX              = var.metrics_athena_s3_prefix
      ATHENA_DATABASE               = var.metrics_athena_database_name
      MODEL_VERSION                 = var.metrics_model_version
      TEST_ID                       = var.metrics_test_id
      METADATA_GROUNDTRUTH_KEY_PATH = var.metrics_metadata_groundtruth_key_path
      MAX_AUDIO_COUNT               = tostring(var.metrics_max_audio_count)
      PROCESSING_MODE               = var.processing_mode
    }
  }

  layers = [
    var.transcribe_layer_arn,
    aws_lambda_layer_version.metricas_layer.arn,
  ]

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [var.security_group_id]
  }
}
