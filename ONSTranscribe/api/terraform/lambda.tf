data "archive_file" "transcribe_audio_to_elastic_search_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/transcribe_audio_to_elastic_search"
  output_path = "${path.module}/lambdas/transcribe_audio_to_elastic_search.zip"
}

resource "aws_lambda_layer_version" "transcribe_layer" {
  layer_name          = "transcribe_layer"
  compatible_runtimes = ["python3.9"]

  filename         = "${path.module}/lambdas/layers/transcribe_layer.zip"
  source_code_hash = filebase64sha256("${path.module}/lambdas/layers/transcribe_layer.zip")
}

# Consumo da fila: worker SQS dentro da task ECS (api/app/services/sqs_worker.py).
# A Lambda create_transcribe_job_via_api + ESM foi removida (competia com o worker e
# gerava 504/NotVisible quando ativa junto ao worker). Capacidade = desiredCount das tasks.

# A solução de métricas de qualidade (Lambda calcular_metricas_transcricao,
# layer metricas_layer, Glue/Athena e Lake Formation) está segregada no módulo
# ./metricas_transcricao, habilitado por var.deploy_metricas_solution (ver metricas.tf).

resource "aws_lambda_function" "transcribe_audio_to_elastic_search" {
  function_name    = "${var.project_name}-audio-to-search"
  role             = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.9"
  filename         = data.archive_file.transcribe_audio_to_elastic_search_lambda.output_path
  source_code_hash = data.archive_file.transcribe_audio_to_elastic_search_lambda.output_base64sha256
  timeout          = 900

  tags = local.default_tags

  environment {
    variables = {
      BucketName            = var.audio_bucket_name,
      DataAccessRoleArn     = aws_iam_role.transcribe_audio_to_elastic_search_lambda_execution_role.arn,
      KeyPath               = var.control_room_key_path,
      index                 = var.index_name,
      MediaFormat           = ".wav",
      RecodersList          = var.recorders_list,
      ServiceType           = "whisper",
      TranscriptionsKeyPath = var.transcriptions_key_path,
      VocabulariesKeyPath   = var.vocabularies_key_path,
      VocabularyName        = var.vocabulary_name,
      TranscribeProvider    = "internal",
      TranscribeApiUrl      = "http://${aws_lb.fastapi_alb.dns_name}/api/v1",
      ElasticsearchUri      = var.elastic_search_uri,
      CreateJobQueueName    = aws_sqs_queue.transcribe_queue.name,
      # Path SSM do toggle de enqueue (runtime). Valor: put-parameter --overwrite.
      TranscriptionProcessingEnabledSsmParameter = aws_ssm_parameter.transcription_processing_enabled.name
      # TranscriptionsTable    = var.transcriptions_table_name
    }
  }

  layers = [aws_lambda_layer_version.transcribe_layer.arn]

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.alb_sg.id]
  }
}

# resource "aws_lambda_function" "movimentacao_arquivos_wav_sala_de_controle" {
#   function_name = "movimentacao_arquivos_wav_sala_de_controle_${var.environment}"
#   role          = aws_iam_role.movimentacao_arquivos_wav_sala_de_controle_lambda_execution_role.arn
#   handler       = "lambda_function.lambda_handler"
#   runtime       = "python3.9"
#   filename      = "${path.module}/lambdas/movimentacao_arquivos_wav_sala_de_controle.zip"
#   timeout       = 900

#   environment {
#     variables = {
#       GATEWAY_BUCKET        = var.gateway_bucket,
#       AUDIO_BUCKET_NAME     = var.audio_bucket_name,
#       CONTROL_ROOM_KEY_PATH = var.control_room_key_path,
#       LOCATION_CODES        = var.location_codes
#     }
#   }

#   layers = [aws_lambda_layer_version.transcribe_layer.arn]

#   vpc_config {
#     subnet_ids            = var.private_subnet_ids
#     security_group_ids = [aws_security_group.alb_sg.id]
#   }
# }
