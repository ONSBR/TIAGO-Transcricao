# Capacity provider GPU (ECS Managed Instances) — SSOT do tipo/disco da máquina.
#
# Nome estável: GPU-ons-transcribe-<env>
# - prefixo GPU-* casa com a policy de deploy do ambiente, que restringe
#   o Resource a capacity-provider/GPU-*
# - sufixo ons-transcribe-<env> segue o naming do projeto
# Não renomear de novo: o name do CP é ForceNew e o delete exige zero tasks
# non-stopped + instância MI drenada (não automatizar isso com provisioner).
# O legado "GPU" (sem sufixo) permanece no cluster até desassociar/apagar.
#
# ARNs de role/profile montados sem data source IAM (esteira sem GetInstanceProfile
# amplo em todos os envs).

locals {
  ecs_capacity_provider_name = "GPU-ons-transcribe-${var.environment}"

  ecs_infrastructure_role_arn = (
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.ecs_infrastructure_role_name}"
  )
  ecs_instance_profile_arn = (
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:instance-profile/${var.ecs_instance_profile_name}"
  )
}

resource "aws_ecs_capacity_provider" "transcribe_gpu" {
  name    = local.ecs_capacity_provider_name
  cluster = aws_ecs_cluster.fastapi_cluster.name

  managed_instances_provider {
    infrastructure_role_arn = local.ecs_infrastructure_role_arn
    propagate_tags          = "CAPACITY_PROVIDER"

    infrastructure_optimization {
      scale_in_after = var.gpu_scale_in_after_seconds
    }

    instance_launch_template {
      ec2_instance_profile_arn = local.ecs_instance_profile_arn
      monitoring               = "BASIC"
      capacity_option_type     = "ON_DEMAND"

      network_configuration {
        subnets         = var.private_subnet_ids
        security_groups = [aws_security_group.fastapi_sg.id]
      }

      storage_configuration {
        # 30 GiB estoura no deploy da imagem (~8 GB ECR / 15–25 GB unpack + snapshot).
        storage_size_gib = var.gpu_root_volume_size_gb
      }

      instance_requirements {
        vcpu_count {
          min = 4
          max = 4
        }

        memory_mib {
          min = 16384
          max = 16384
        }

        accelerator_types         = ["gpu"]
        accelerator_manufacturers = ["nvidia"]
        allowed_instance_types    = [var.gpu_instance_type]
      }
    }
  }

  tags = merge(local.default_tags, {
    Name = local.ecs_capacity_provider_name
  })

  # Se o name mudar no futuro (evitar), create_before_destroy reduz a janela
  # em que o service fica sem CP; o delete do antigo ainda é passo operacional
  # se houver task/instância residual.
  lifecycle {
    create_before_destroy = true
  }
}

# Managed Instances associa o provider ao cluster no CreateCapacityProvider
# (atributo cluster acima). Não usar aws_ecs_cluster_capacity_providers /
# PutClusterCapacityProviders: no apply falhou com "not in an ACTIVE state"
# logo após o create. O legado "GPU" permanece no cluster até
# ser desassociado/apagado manualmente depois da migração estável.
