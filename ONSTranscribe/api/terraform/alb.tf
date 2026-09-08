resource "aws_lb" "fastapi_alb" {
  name               = "${var.project_name}-alb-${var.environment}"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = var.public_subnet_ids
}

resource "aws_security_group" "alb_sg" {
  name        = "${var.project_name}-alb-sg-${var.environment}"
  description = "Allow inbound HTTP traffic for ALB"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
