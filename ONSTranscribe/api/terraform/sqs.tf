resource "aws_sqs_queue" "transcribe_error_queue" {
  name = "dlq-${var.project_name}-queue-${var.environment}"
  # Alinhado à fila principal (worker com heartbeat estende visibility).
  visibility_timeout_seconds = 1800
}

resource "aws_sqs_queue_redrive_allow_policy" "transcribe_error_queue_redrive_allow_policy" {
  queue_url = aws_sqs_queue.transcribe_error_queue.url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.transcribe_queue.arn]
  })

  depends_on = [aws_sqs_queue.transcribe_queue, aws_sqs_queue.transcribe_error_queue]
}

resource "aws_sqs_queue" "transcribe_queue" {
  name = "${var.project_name}-queue-${var.environment}"
  # 30 min base; o worker renova via ChangeMessageVisibility (heartbeat).
  visibility_timeout_seconds = 1800

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.transcribe_error_queue.arn
    # 1 era rígido: qualquer falha de visibility → DLQ sem retry.
    maxReceiveCount = 5
  })

  depends_on = [aws_sqs_queue.transcribe_error_queue]
}
