#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE_PATH="${1:-$DEFAULT_ENV_FILE}"
load_env "$ENV_FILE_PATH"

require_command aws

require_env \
  AWS_PROFILE \
  AWS_DEFAULT_REGION \
  NAT_VPC_ID \
  NAT_PUBLIC_SUBNET_ID \
  NAT_PRIVATE_ROUTE_TABLE_IDS \
  NAT_GATEWAY_NAME \
  NAT_EIP_NAME

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

split_csv_into_array() {
  local input="$1"
  local array_name="$2"
  local raw_items=()
  local item=""

  IFS=',' read -r -a raw_items <<< "$input"
  eval "$array_name=()"

  for item in "${raw_items[@]}"; do
    item="$(trim_whitespace "$item")"
    [[ -n "$item" ]] || continue
    eval "$array_name+=(\"\$item\")"
  done
}

normalize_aws_text_value() {
  local value="$1"

  if [[ -z "$value" || "$value" == "None" || "$value" == "null" ]]; then
    return 1
  fi

  printf '%s' "$value"
}

resolve_public_route_table_id() {
  local route_table_id=""

  route_table_id="$(aws ec2 describe-route-tables \
    --filters "Name=association.subnet-id,Values=$NAT_PUBLIC_SUBNET_ID" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'RouteTables[0].RouteTableId' \
    --output text 2>/dev/null || true)"

  if normalize_aws_text_value "$route_table_id" >/dev/null 2>&1; then
    printf '%s\n' "$route_table_id"
    return 0
  fi

  route_table_id="$(aws ec2 describe-route-tables \
    --filters \
      "Name=vpc-id,Values=$NAT_VPC_ID" \
      "Name=association.main,Values=true" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'RouteTables[0].RouteTableId' \
    --output text 2>/dev/null || true)"

  normalize_aws_text_value "$route_table_id" || \
    fail "Could not resolve route table for public subnet: $NAT_PUBLIC_SUBNET_ID"
}

ensure_internet_gateway() {
  local igw_id=""

  igw_id="$(aws ec2 describe-internet-gateways \
    --filters "Name=attachment.vpc-id,Values=$NAT_VPC_ID" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'InternetGateways[0].InternetGatewayId' \
    --output text 2>/dev/null || true)"

  if normalize_aws_text_value "$igw_id" >/dev/null 2>&1 && [[ "$igw_id" == igw-* ]]; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Internet Gateway already attached to VPC $NAT_VPC_ID: $igw_id" >&2
    printf '%s\n' "$igw_id"
    return 0
  fi

  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Creating Internet Gateway and attaching to VPC $NAT_VPC_ID" >&2
  igw_id="$(aws ec2 create-internet-gateway \
    --region "$AWS_DEFAULT_REGION" \
    --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=${NAT_GATEWAY_NAME/nat/igw}},{Key=Solution,Value=Transcricao},{Key=ManagedBy,Value=BootstrapScript}]" \
    --query 'InternetGateway.InternetGatewayId' \
    --output text)"

  normalize_aws_text_value "$igw_id" || fail "Could not create Internet Gateway"

  aws ec2 attach-internet-gateway \
    --internet-gateway-id "$igw_id" \
    --vpc-id "$NAT_VPC_ID" \
    --region "$AWS_DEFAULT_REGION"

  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Internet Gateway $igw_id attached to VPC $NAT_VPC_ID" >&2
  printf '%s\n' "$igw_id"
}

ensure_public_igw_route() {
  local igw_id="$1"
  local public_route_table_id=""
  local current_gw=""

  public_route_table_id="$(resolve_public_route_table_id)"

  current_gw="$(aws ec2 describe-route-tables \
    --route-table-ids "$public_route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId | [0]" \
    --output text 2>/dev/null || true)"

  if [[ "$current_gw" == "$igw_id" ]]; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Public route table $public_route_table_id already routes 0.0.0.0/0 to $igw_id" >&2
    return 0
  fi

  # Verificar se existe qualquer rota 0.0.0.0/0 (independente do target) para decidir create vs replace
  local route_exists=""
  route_exists="$(aws ec2 describe-route-tables \
    --route-table-ids "$public_route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'] | length(@)" \
    --output text 2>/dev/null || true)"

  if [[ "$route_exists" -gt 0 ]]; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Replacing existing default route in public route table $public_route_table_id with IGW $igw_id" >&2
    aws ec2 replace-route \
      --route-table-id "$public_route_table_id" \
      --destination-cidr-block 0.0.0.0/0 \
      --gateway-id "$igw_id" \
      --region "$AWS_DEFAULT_REGION" >/dev/null
  else
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Creating default route to IGW $igw_id in public route table $public_route_table_id" >&2
    aws ec2 create-route \
      --route-table-id "$public_route_table_id" \
      --destination-cidr-block 0.0.0.0/0 \
      --gateway-id "$igw_id" \
      --region "$AWS_DEFAULT_REGION" >/dev/null
  fi

  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Public subnet $NAT_PUBLIC_SUBNET_ID now routes 0.0.0.0/0 to Internet Gateway $igw_id" >&2
}

find_existing_eip_allocation_id() {
  local allocation_id=""

  allocation_id="$(aws ec2 describe-addresses \
    --filters \
      "Name=domain,Values=vpc" \
      "Name=tag:Name,Values=$NAT_EIP_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'Addresses[0].AllocationId' \
    --output text 2>/dev/null || true)"

  normalize_aws_text_value "$allocation_id"
}

ensure_eip() {
  local allocation_id=""

  if allocation_id="$(find_existing_eip_allocation_id)"; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Elastic IP for NAT already exists: $allocation_id" >&2
    printf '%s\n' "$allocation_id"
    return 0
  fi

  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Allocating Elastic IP for NAT: $NAT_EIP_NAME" >&2
  allocation_id="$(aws ec2 allocate-address \
    --domain vpc \
    --region "$AWS_DEFAULT_REGION" \
    --query 'AllocationId' \
    --output text)"

  normalize_aws_text_value "$allocation_id" >/dev/null || fail "Could not allocate Elastic IP for NAT"

  aws ec2 create-tags \
    --resources "$allocation_id" \
    --region "$AWS_DEFAULT_REGION" \
    --tags \
      "Key=Name,Value=$NAT_EIP_NAME" \
      "Key=Solution,Value=Transcricao" \
      "Key=ManagedBy,Value=BootstrapScript" >/dev/null

  printf '%s\n' "$allocation_id"
}

find_existing_nat_gateway_id() {
  local nat_gateway_id=""

  nat_gateway_id="$(aws ec2 describe-nat-gateways \
    --filter \
      "Name=vpc-id,Values=$NAT_VPC_ID" \
      "Name=subnet-id,Values=$NAT_PUBLIC_SUBNET_ID" \
      "Name=state,Values=pending,available" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'NatGateways[0].NatGatewayId' \
    --output text 2>/dev/null || true)"

  normalize_aws_text_value "$nat_gateway_id"
}

wait_for_nat_gateway() {
  local nat_gateway_id="$1"
  local attempt=1
  local max_attempts=40
  local sleep_seconds=15
  local nat_state=""

  while [[ "$attempt" -le "$max_attempts" ]]; do
    nat_state="$(aws ec2 describe-nat-gateways \
      --nat-gateway-ids "$nat_gateway_id" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'NatGateways[0].State' \
      --output text 2>/dev/null || true)"

    if [[ "$nat_state" == "available" ]]; then
      return 0
    fi

    case "$nat_state" in
      failed|deleting|deleted)
        fail "NAT Gateway '$nat_gateway_id' reached terminal state '$nat_state'"
        ;;
    esac

    printf '%s - %s\n' \
      "$(date '+%Y-%m-%d %H:%M:%S')" \
      "Waiting for NAT Gateway $nat_gateway_id to become available. Current state: ${nat_state:-unknown} (${attempt}/${max_attempts})" >&2
    sleep "$sleep_seconds"
    ((attempt++))
  done

  fail "Timed out waiting for NAT Gateway '$nat_gateway_id' to become available"
}

ensure_nat_gateway() {
  local allocation_id="$1"
  local nat_gateway_id=""

  if nat_gateway_id="$(find_existing_nat_gateway_id)"; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "NAT Gateway already exists in public subnet $NAT_PUBLIC_SUBNET_ID: $nat_gateway_id" >&2
    wait_for_nat_gateway "$nat_gateway_id"
    printf '%s\n' "$nat_gateway_id"
    return 0
  fi

  printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Creating NAT Gateway in public subnet $NAT_PUBLIC_SUBNET_ID" >&2
  nat_gateway_id="$(aws ec2 create-nat-gateway \
    --subnet-id "$NAT_PUBLIC_SUBNET_ID" \
    --allocation-id "$allocation_id" \
    --region "$AWS_DEFAULT_REGION" \
    --tag-specifications "ResourceType=natgateway,Tags=[{Key=Name,Value=$NAT_GATEWAY_NAME},{Key=Solution,Value=Transcricao},{Key=ManagedBy,Value=BootstrapScript}]" \
    --query 'NatGateway.NatGatewayId' \
    --output text)"

  normalize_aws_text_value "$nat_gateway_id" >/dev/null || fail "Could not create NAT Gateway"
  wait_for_nat_gateway "$nat_gateway_id"
  printf '%s\n' "$nat_gateway_id"
}

ensure_private_default_route() {
  local route_table_id="$1"
  local nat_gateway_id="$2"
  local current_nat_gateway_id=""
  local current_gateway_id=""
  local current_transit_gateway_id=""
  local current_network_interface_id=""
  local current_instance_id=""

  current_nat_gateway_id="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].NatGatewayId | [0]" \
    --output text 2>/dev/null || true)"
  current_gateway_id="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId | [0]" \
    --output text 2>/dev/null || true)"
  current_transit_gateway_id="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].TransitGatewayId | [0]" \
    --output text 2>/dev/null || true)"
  current_network_interface_id="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].NetworkInterfaceId | [0]" \
    --output text 2>/dev/null || true)"
  current_instance_id="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].InstanceId | [0]" \
    --output text 2>/dev/null || true)"

  # Verificar estado da rota existente (active = conflito real; blackhole = rota órfã, pode substituir)
  local current_route_state=""
  current_route_state="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].State | [0]" \
    --output text 2>/dev/null || true)"

  if normalize_aws_text_value "$current_nat_gateway_id" >/dev/null 2>&1; then
    if [[ "$current_nat_gateway_id" == "$nat_gateway_id" ]]; then
      log "Private route table already points to NAT Gateway: $route_table_id -> $nat_gateway_id"
      return 0
    fi
    fail "Private route table '$route_table_id' already has a default route to another NAT Gateway: $current_nat_gateway_id"
  fi

  if normalize_aws_text_value "$current_gateway_id" >/dev/null 2>&1 || \
     normalize_aws_text_value "$current_transit_gateway_id" >/dev/null 2>&1 || \
     normalize_aws_text_value "$current_network_interface_id" >/dev/null 2>&1 || \
     normalize_aws_text_value "$current_instance_id" >/dev/null 2>&1; then
    # Rota com target ativo é conflito real; rota blackhole (target deletado) pode ser substituída
    if [[ "$current_route_state" != "blackhole" ]]; then
      fail "Private route table '$route_table_id' already has an active default route configured. Review it manually before attaching NAT."
    fi
    log "Replacing blackhole default route in private route table: $route_table_id -> $nat_gateway_id"
    aws ec2 replace-route \
      --route-table-id "$route_table_id" \
      --destination-cidr-block 0.0.0.0/0 \
      --nat-gateway-id "$nat_gateway_id" \
      --region "$AWS_DEFAULT_REGION" >/dev/null
    return 0
  fi

  # Rota existe com target nulo (sem gateway definido) — substituir
  local route_exists=""
  route_exists="$(aws ec2 describe-route-tables \
    --route-table-ids "$route_table_id" \
    --region "$AWS_DEFAULT_REGION" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'] | length(@)" \
    --output text 2>/dev/null || true)"

  if [[ "$route_exists" -gt 0 ]]; then
    log "Replacing orphan default route in private route table: $route_table_id -> $nat_gateway_id"
    aws ec2 replace-route \
      --route-table-id "$route_table_id" \
      --destination-cidr-block 0.0.0.0/0 \
      --nat-gateway-id "$nat_gateway_id" \
      --region "$AWS_DEFAULT_REGION" >/dev/null
  else
    log "Creating default route to NAT Gateway in private route table: $route_table_id -> $nat_gateway_id"
    aws ec2 create-route \
      --route-table-id "$route_table_id" \
      --destination-cidr-block 0.0.0.0/0 \
      --nat-gateway-id "$nat_gateway_id" \
      --region "$AWS_DEFAULT_REGION" >/dev/null
  fi
}

split_csv_into_array "$NAT_PRIVATE_ROUTE_TABLE_IDS" PRIVATE_ROUTE_TABLE_IDS
[[ "${#PRIVATE_ROUTE_TABLE_IDS[@]}" -gt 0 ]] || fail "NAT_PRIVATE_ROUTE_TABLE_IDS must include at least one route table"

log "Step: bootstrap NAT Gateway for ONSTranscribe private subnets"
IGW_ID="$(ensure_internet_gateway)"
ensure_public_igw_route "$IGW_ID"
EIP_ALLOCATION_ID="$(ensure_eip)"
NAT_GATEWAY_ID="$(ensure_nat_gateway "$EIP_ALLOCATION_ID")"

for route_table_id in "${PRIVATE_ROUTE_TABLE_IDS[@]}"; do
  ensure_private_default_route "$route_table_id" "$NAT_GATEWAY_ID"
done

log "Bootstrap de NAT Gateway concluído com sucesso"
