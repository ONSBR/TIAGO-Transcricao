# Camada 1 — autoscaling horizontal por profundidade de fila.
#
# Escala o serviço pelo backlog REAL (ApproximateNumberOfMessagesVisible = áudios
# ESPERANDO), não pelo NotVisible (em processamento), que é a métrica enganosa do
# scale-up manual de produção (origem do custo excedente observado). O worker é sequencial
# (1 áudio por máquina), então mais backlog = mais máquinas; quando a fila esvazia,
# desce até o piso.
#
# ATENÇÃO — deploy: aplicar só em DEV se o ambiente de produção já tiver um serviço
# `fastapi-task-gpu` escalado MANUALMENTE (console) com +5/NotVisible: este autoscaling
# colidiria com ele. Substituir o aparato manual por este é um passo operacional
# coordenado com quem opera o ambiente.

resource "aws_appautoscaling_target" "fastapi_gpu" {
  # Pause de custo: processing off → min=max=0 (sem task GPU ociosa).
  # Valores “normais” vêm de var.ecs_min_capacity / var.ecs_max_capacity.
  max_capacity       = var.transcription_processing_enabled ? var.ecs_max_capacity : 0
  min_capacity       = var.transcription_processing_enabled ? var.ecs_min_capacity : 0
  resource_id        = "service/${aws_ecs_cluster.fastapi_cluster.name}/${aws_ecs_service.fastapi_service.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# Scale-up: backlog crescendo → adiciona máquina(s), proporcional ao tamanho do backlog.
resource "aws_appautoscaling_policy" "fastapi_gpu_scale_up" {
  name               = "${var.project_name}-scale-up-backlog-${var.environment}"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.fastapi_gpu.resource_id
  scalable_dimension = aws_appautoscaling_target.fastapi_gpu.scalable_dimension
  service_namespace  = aws_appautoscaling_target.fastapi_gpu.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 300 # ~ tempo de provisionar a máquina GPU antes de reavaliar
    metric_aggregation_type = "Maximum"

    # Limites relativos ao threshold do alarme (15). Backlog 15..25 → +1; 25+ → +2.
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 10
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 10
      scaling_adjustment          = 2
    }
  }
}

# Scale-down: fila vazia sustentada → remove uma máquina (até o piso).
resource "aws_appautoscaling_policy" "fastapi_gpu_scale_down" {
  name               = "${var.project_name}-scale-down-backlog-${var.environment}"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.fastapi_gpu.resource_id
  scalable_dimension = aws_appautoscaling_target.fastapi_gpu.scalable_dimension
  service_namespace  = aws_appautoscaling_target.fastapi_gpu.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = -1
    }
  }
}

# Alarme: backlog alto (≥15 esperando) → dispara o scale-up.
# Threshold 15 (não 5): o histórico de produção (30 dias) mostra a fila quase sempre
# vazia, com picos pequenos esparsos (<18) e um pico raro (~36). Picos pequenos
# drenam numa máquina antes da 2ª subir (~8 min) → disparar neles é desperdício.
# 15 é o ponto onde o backlog leva >10 min pra drenar numa máquina e a 2ª ajuda.
resource "aws_cloudwatch_metric_alarm" "transcribe_backlog_high" {
  alarm_name          = "${var.project_name}-backlog-high-${var.environment}"
  alarm_description   = "Áudios esperando na fila (backlog real) — sobe máquina"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 15
  dimensions          = { QueueName = aws_sqs_queue.transcribe_queue.name }
  alarm_actions       = [aws_appautoscaling_policy.fastapi_gpu_scale_up.arn]
  tags                = local.default_tags
}

# Alarme: fila vazia por ~10 min → dispara o scale-down.
resource "aws_cloudwatch_metric_alarm" "transcribe_backlog_empty" {
  alarm_name          = "${var.project_name}-backlog-empty-${var.environment}"
  alarm_description   = "Fila vazia sustentada — desce uma máquina até o piso"
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = 10
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  dimensions          = { QueueName = aws_sqs_queue.transcribe_queue.name }
  alarm_actions       = [aws_appautoscaling_policy.fastapi_gpu_scale_down.arn]
  tags                = local.default_tags
}
