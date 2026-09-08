# =======================
# SNS Topic + Policy permitindo S3 publicar
# =======================

resource "aws_sns_topic" "incoming_audio" {
  name = "${var.project_name}-incoming-audio"
}

data "aws_s3_bucket" "audio_bucket" {
  bucket = var.audio_bucket_name
}

data "aws_iam_policy_document" "sns_allow_s3_publish_incoming_audio" {
  statement {
    sid     = "AllowS3Publish"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    resources = [aws_sns_topic.incoming_audio.arn]

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [data.aws_s3_bucket.audio_bucket.arn]
    }
  }
}

resource "aws_sns_topic_policy" "incoming_audio" {
  arn    = aws_sns_topic.incoming_audio.arn
  policy = data.aws_iam_policy_document.sns_allow_s3_publish_incoming_audio.json
}

# =======================
# Assinar a Lambda no SNS + permissão para o SNS invocar a Lambda
# =======================
resource "aws_lambda_permission" "allow_sns_invoke_incoming_audio" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.transcribe_audio_to_elastic_search.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.incoming_audio.arn
}

resource "aws_sns_topic_subscription" "lambda_sub_incoming_audio" {
  topic_arn = aws_sns_topic.incoming_audio.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.transcribe_audio_to_elastic_search.arn

  depends_on = [aws_lambda_permission.allow_sns_invoke_incoming_audio]
}

# =======================
# Notificações do S3 -> SNS
# =======================
resource "aws_s3_bucket_notification" "incoming_audio" {
  bucket = data.aws_s3_bucket.audio_bucket.id

  topic {
    topic_arn     = aws_sns_topic.incoming_audio.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = var.control_room_key_path
    filter_suffix = ".wav"
  }

  topic {
    topic_arn     = aws_sns_topic.incoming_audio.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = var.transcriptions_key_path
    filter_suffix = ".json"
  }

  topic {
    topic_arn     = aws_sns_topic.incoming_audio.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = var.vocabularies_key_path
    filter_suffix = ".txt"
  }

  lifecycle {
    # Impede que o Terraform altere ou remova os eventos configurados no S3
    ignore_changes = [
      topic
    ]
  }

  depends_on = [aws_sns_topic_policy.incoming_audio]
}
