#!/bin/bash
# =============================================================================
# cleanup_and_redeploy.sh
#
# Libera espaço em disco no cluster ECS e força o redeploy do serviço FastAPI.
#
# O que faz:
#   1. Registra uma task definition de limpeza Docker (containers parados +
#      imagens não utilizadas). Seguro: não toca volumes nem networks.
#   2. Executa a task de limpeza no cluster e aguarda ela terminar.
#   3. Força um novo deploy do serviço ECS.
#
# Quando usar:
#   - Erro "OSError: [Errno 28] No space left on device" nos logs do ECS
#   - Após múltiplos redeploys com falha que deixaram imagens antigas no disco
#
# Uso:
#   1. Copie o arquivo de exemplo e preencha com os valores do seu ambiente:
#        cp api/scripts/cleanup.env.sample api/scripts/cleanup.env
#        # edite cleanup.env
#   2. Execute a partir da raiz do repositório ONSTranscribe:
#        chmod +x api/scripts/cleanup_and_redeploy.sh
#        ./api/scripts/cleanup_and_redeploy.sh
#
# Pré-requisito: AWS CLI configurado com o perfil definido em cleanup.env.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Localizar e carregar o .env
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/cleanup.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "[ERRO] Arquivo de configuração não encontrado: $ENV_FILE"
  echo ""
  echo "Crie-o a partir do exemplo:"
  echo "  cp $SCRIPT_DIR/cleanup.env.sample $ENV_FILE"
  echo "  # edite $ENV_FILE com os valores do seu ambiente"
  exit 1
fi

# shellcheck source=/dev/null
set -o allexport
source "$ENV_FILE"
set +o allexport

# -----------------------------------------------------------------------------
# Validar que todas as variáveis obrigatórias foram preenchidas
# -----------------------------------------------------------------------------
_ERRORS=0
for _VAR in CLUSTER SERVICE SECURITY_GROUP EXECUTION_ROLE_ARN LOG_GROUP AWS_PROFILE AWS_REGION; do
  if [ -z "${!_VAR:-}" ]; then
    echo "[ERRO] Variável obrigatória não preenchida no cleanup.env: $_VAR"
    _ERRORS=$((_ERRORS + 1))
  fi
done

# SUBNETS é uma string separada por vírgula no .env; converter para array
IFS=',' read -ra SUBNETS <<< "${SUBNETS_CSV:-}"
if [ "${#SUBNETS[@]}" -eq 0 ] || [ -z "${SUBNETS[0]}" ]; then
  echo "[ERRO] Variável obrigatória não preenchida no cleanup.env: SUBNETS_CSV"
  _ERRORS=$((_ERRORS + 1))
fi

if [ "$_ERRORS" -gt 0 ]; then
  echo ""
  echo "Preencha as variáveis em $ENV_FILE e tente novamente."
  exit 1
fi

TASK_FAMILY="${CLUSTER}-docker-cleanup"

echo "================================================================"
echo " ECS Cleanup & Redeploy"
echo " Cluster : $CLUSTER"
echo " Service : $SERVICE"
echo "================================================================"
echo ""

# -----------------------------------------------------------------------------
# 1. Registrar task definition
# -----------------------------------------------------------------------------
echo "[1/4] Registrando task definition de cleanup..."

TASK_DEF_ARN=$(aws ecs register-task-definition \
  --family "$TASK_FAMILY" \
  --execution-role-arn "$EXECUTION_ROLE_ARN" \
  --network-mode awsvpc \
  --requires-compatibilities EC2 \
  --container-definitions "[
    {
      \"name\": \"docker-cleanup\",
      \"image\": \"public.ecr.aws/docker/library/docker:cli\",
      \"command\": [\"sh\", \"-c\", \"docker container prune -f && docker image prune -a -f && echo FULL_PRUNE_DONE\"],
      \"memory\": 256,
      \"privileged\": true,
      \"essential\": true,
      \"mountPoints\": [
        {
          \"sourceVolume\": \"docker-socket\",
          \"containerPath\": \"/var/run/docker.sock\",
          \"readOnly\": false
        }
      ],
      \"logConfiguration\": {
        \"logDriver\": \"awslogs\",
        \"options\": {
          \"awslogs-group\": \"$LOG_GROUP\",
          \"awslogs-region\": \"$AWS_REGION\",
          \"awslogs-stream-prefix\": \"cleanup-full\"
        }
      }
    }
  ]" \
  --volumes '[{"name":"docker-socket","host":{"sourcePath":"/var/run/docker.sock"}}]' \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

echo "    OK: $TASK_DEF_ARN"

# -----------------------------------------------------------------------------
# 2. Iniciar a task de cleanup (tenta cada subnet até uma funcionar)
# -----------------------------------------------------------------------------
echo ""
echo "[2/4] Iniciando task de cleanup..."

TASK_ARN=""
for SUBNET in "${SUBNETS[@]}"; do
  SUBNET="${SUBNET// /}"  # remover espaços eventuais
  echo "    Tentando subnet $SUBNET..."
  RESULT=$(aws ecs run-task \
    --cluster "$CLUSTER" \
    --task-definition "$TASK_FAMILY" \
    --launch-type EC2 \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SECURITY_GROUP]}" \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --output json)

  TASK_ARN=$(echo "$RESULT" | python3 -c "import sys,json; t=json.load(sys.stdin).get('tasks',[]); print(t[0]['taskArn'] if t else '')" 2>/dev/null || true)
  FAILURE=$(echo "$RESULT" | python3 -c "import sys,json; f=json.load(sys.stdin).get('failures',[]); print(f[0].get('reason','') if f else '')" 2>/dev/null || true)

  if [ -n "$TASK_ARN" ]; then
    echo "    OK: task iniciada na subnet $SUBNET"
    break
  fi

  echo "    Falhou (reason: $FAILURE) — tentando próxima subnet..."
done

if [ -z "$TASK_ARN" ]; then
  echo ""
  echo "[ERRO] Não foi possível iniciar a task em nenhuma das subnets configuradas."
  echo ""
  echo "Diagnóstico: descubra em qual subnet o container instance está:"
  echo ""
  echo "  aws ecs describe-container-instances \\"
  echo "    --cluster $CLUSTER \\"
  echo "    --container-instances \$(aws ecs list-container-instances \\"
  echo "      --cluster $CLUSTER --query 'containerInstanceArns[0]' --output text \\"
  echo "      --profile $AWS_PROFILE --region $AWS_REGION) \\"
  echo "    --query 'containerInstances[0].attributes[?name==\`ecs.subnet-id\`].value' \\"
  echo "    --profile $AWS_PROFILE --region $AWS_REGION --output text"
  echo ""
  echo "Adicione a subnet correta à variável SUBNETS_CSV em $ENV_FILE"
  exit 1
fi

echo "    ARN: $TASK_ARN"

# -----------------------------------------------------------------------------
# 3. Aguardar o cleanup terminar
# -----------------------------------------------------------------------------
echo ""
echo "[3/4] Aguardando cleanup terminar (pode levar 1-2 minutos)..."

aws ecs wait tasks-stopped \
  --cluster "$CLUSTER" \
  --tasks "$TASK_ARN" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

echo "    OK: cleanup concluído."

# -----------------------------------------------------------------------------
# 4. Forçar novo deploy
# -----------------------------------------------------------------------------
echo ""
echo "[4/4] Forçando novo deploy do serviço $SERVICE..."

aws ecs update-service \
  --cluster "$CLUSTER" \
  --service "$SERVICE" \
  --force-new-deployment \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'service.{status:status,desiredCount:desiredCount,runningCount:runningCount}' \
  --output json

echo ""
echo "================================================================"
echo " Redeploy iniciado com sucesso."
echo " Acompanhe a subida da API com:"
echo ""
echo "   aws logs tail $LOG_GROUP \\"
echo "     --profile $AWS_PROFILE \\"
echo "     --region $AWS_REGION \\"
echo "     --log-stream-name-prefix ecs/fastapi-container \\"
echo "     --follow"
echo "================================================================"
