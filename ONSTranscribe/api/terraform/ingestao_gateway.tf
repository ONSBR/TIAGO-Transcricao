# Primeiro estagio da ingestao: o audio chega no bucket gateway e e movido para o caminho
# da sala de controle no bucket de audio, organizado por gravador e data. E a porta de
# entrada do pipeline — o que dispara todo o resto e a gravacao no destino, tratada em
# sns.tf (segundo estagio).
#
# Este conjunto vinha do Terraform da API .NET, que foi retirado do repositorio junto com
# a propria API. A funcao nao tem relacao com o BackEnd: pertence ao mecanismo de
# transcricao, e por isso passou a ser provisionada aqui.

data "archive_file" "movimentacao_arquivos_wav_sala_de_controle" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/movimentacao_arquivos_wav_sala_de_controle"
  output_path = "${path.module}/lambdas/movimentacao_arquivos_wav_sala_de_controle.zip"
}

resource "aws_iam_role" "movimentacao_arquivos_wav" {
  name = "${var.project_name}-movimentacao-wav-lambda-role"
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

# A funcao le do bucket gateway e escreve no bucket de audio. Antes usava
# AmazonS3FullAccess; aqui o acesso e restrito a esses dois buckets e as acoes
# necessarias para copiar e remover o arquivo de origem.
resource "aws_iam_policy" "movimentacao_arquivos_wav_s3" {
  name        = "${var.project_name}-movimentacao-wav-s3"
  description = "Move o audio do bucket gateway para o caminho da sala de controle"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid      = "LeituraNaOrigem",
        Effect   = "Allow",
        Action   = ["s3:GetObject", "s3:DeleteObject"],
        Resource = "arn:aws:s3:::${var.gateway_bucket}/*"
      },
      {
        Sid      = "EscritaNoDestino",
        Effect   = "Allow",
        Action   = ["s3:PutObject"],
        Resource = "arn:aws:s3:::${var.audio_bucket_name}/*"
      },
      {
        Sid    = "ListagemDosBuckets",
        Effect = "Allow",
        Action = ["s3:ListBucket", "s3:GetBucketLocation"],
        Resource = [
          "arn:aws:s3:::${var.gateway_bucket}",
          "arn:aws:s3:::${var.audio_bucket_name}",
        ]
      },
    ]
  })
  tags = local.default_tags
}

resource "aws_iam_role_policy_attachment" "movimentacao_arquivos_wav_s3" {
  role       = aws_iam_role.movimentacao_arquivos_wav.name
  policy_arn = aws_iam_policy.movimentacao_arquivos_wav_s3.arn
}

# Escrita de log no CloudWatch, que antes vinha embutida no acesso amplo.
resource "aws_iam_role_policy_attachment" "movimentacao_arquivos_wav_logs" {
  role       = aws_iam_role.movimentacao_arquivos_wav.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "movimentacao_arquivos_wav_sala_de_controle" {
  function_name    = "${var.project_name}-movimentacao-wav-${var.environment}"
  role             = aws_iam_role.movimentacao_arquivos_wav.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.9"
  filename         = data.archive_file.movimentacao_arquivos_wav_sala_de_controle.output_path
  source_code_hash = data.archive_file.movimentacao_arquivos_wav_sala_de_controle.output_base64sha256
  timeout          = 900

  tags = local.default_tags

  environment {
    variables = {
      GATEWAY_BUCKET        = var.gateway_bucket
      AUDIO_BUCKET_NAME     = var.audio_bucket_name
      CONTROL_ROOM_KEY_PATH = var.control_room_key_path
    }
  }
}

# ---------------------------------------------------------------------------
# Gatilho: novo .wav no bucket gateway -> SNS -> lambda de movimentacao
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "gateway_audio" {
  name = "${var.project_name}-gateway-audio-${var.environment}"
  tags = local.default_tags
}

data "aws_s3_bucket" "gateway_bucket" {
  bucket = var.gateway_bucket
}

data "aws_iam_policy_document" "sns_allow_s3_publish_gateway_audio" {
  statement {
    sid     = "AllowS3Publish"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    resources = [aws_sns_topic.gateway_audio.arn]

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [data.aws_s3_bucket.gateway_bucket.arn]
    }
  }
}

resource "aws_sns_topic_policy" "gateway_audio" {
  arn    = aws_sns_topic.gateway_audio.arn
  policy = data.aws_iam_policy_document.sns_allow_s3_publish_gateway_audio.json
}

resource "aws_lambda_permission" "allow_sns_invoke_gateway_audio" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.movimentacao_arquivos_wav_sala_de_controle.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.gateway_audio.arn
}

resource "aws_sns_topic_subscription" "lambda_sub_gateway_audio" {
  topic_arn = aws_sns_topic.gateway_audio.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.movimentacao_arquivos_wav_sala_de_controle.arn

  depends_on = [aws_lambda_permission.allow_sns_invoke_gateway_audio]
}

resource "aws_s3_bucket_notification" "gateway_audio" {
  bucket = data.aws_s3_bucket.gateway_bucket.id

  topic {
    topic_arn     = aws_sns_topic.gateway_audio.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = var.gateway_bucket_control_room_key_path
    filter_suffix = ".wav"
  }

  lifecycle {
    # Nao remove notificacoes ja configuradas no bucket por fora deste Terraform.
    ignore_changes = [topic]
  }

  depends_on = [aws_sns_topic_policy.gateway_audio]
}
