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
  name        = "${var.project_name}-api-sg-${var.environment}"
  description = "Allow HTTP traffic"
  vpc_id      = var.vpc_id

  ingress {
    description = "Allow HTTP inbound traffic"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "fastapi-sg-${var.environment}"
  }
}

resource "aws_lb_target_group" "transcribe_tg" {
  name        = "${var.project_name}-api-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/docs"
    interval            = 300
    timeout             = 120
    healthy_threshold   = 2
    unhealthy_threshold = 2
    port                = "traffic-port"
  }
}
