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
  VPC_ENDPOINTS_VPC_ID \
  VPC_ENDPOINTS_PRIVATE_SUBNET_IDS \
  VPC_ENDPOINTS_ROUTE_TABLE_IDS \
  VPC_ENDPOINTS_SECURITY_GROUP_NAME \
  VPC_ENDPOINTS_ENABLE_SSM \
  VPC_ENDPOINTS_ENABLE_BEDROCK

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

array_contains() {
  local needle="$1"
  shift
  local item=""

  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done

  return 1
}

normalize_aws_text_value() {
  local value="$1"

  if [[ -z "$value" || "$value" == "None" || "$value" == "null" ]]; then
    return 1
  fi

  printf '%s' "$value"
}

describe_vpc_cidr() {
  local cidr=""

  cidr="$(aws ec2 describe-vpcs \
    --vpc-ids "$VPC_ENDPOINTS_VPC_ID" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'Vpcs[0].CidrBlock' \
    --output text)"

  normalize_aws_text_value "$cidr" || fail "Could not resolve CIDR block for VPC: $VPC_ENDPOINTS_VPC_ID"
}

build_service_name() {
  local short_name="$1"
  printf 'com.amazonaws.%s.%s' "$AWS_DEFAULT_REGION" "$short_name"
}

resolve_endpoint_sg_id() {
  local group_id=""

  group_id="$(aws ec2 describe-security-groups \
    --filters \
      "Name=vpc-id,Values=$VPC_ENDPOINTS_VPC_ID" \
      "Name=group-name,Values=$VPC_ENDPOINTS_SECURITY_GROUP_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || true)"

  if normalize_aws_text_value "$group_id" >/dev/null 2>&1; then
    printf '%s\n' "$group_id"
    return 0
  fi

  return 1
}

ensure_endpoint_security_group() {
  local vpc_cidr="$1"
  local group_id=""

  if group_id="$(resolve_endpoint_sg_id)"; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "VPC endpoint security group already exists: $group_id" >&2
  else
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Creating VPC endpoint security group: $VPC_ENDPOINTS_SECURITY_GROUP_NAME" >&2
    group_id="$(aws ec2 create-security-group \
      --group-name "$VPC_ENDPOINTS_SECURITY_GROUP_NAME" \
      --description "Security group for ONSTranscribe VPC endpoints" \
      --vpc-id "$VPC_ENDPOINTS_VPC_ID" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'GroupId' \
      --output text)"

    normalize_aws_text_value "$group_id" >/dev/null || fail "Could not create VPC endpoint security group"
  fi

  aws ec2 authorize-security-group-ingress \
    --group-id "$group_id" \
    --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=$vpc_cidr,Description=Allow HTTPS from VPC}]" \
    --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1 || true

  printf '%s\n' "$group_id"
}

find_existing_endpoint_id() {
  local service_name="$1"
  local endpoint_type="$2"
  local endpoint_id=""

  endpoint_id="$(aws ec2 describe-vpc-endpoints \
    --filters \
      "Name=vpc-id,Values=$VPC_ENDPOINTS_VPC_ID" \
      "Name=service-name,Values=$service_name" \
      "Name=vpc-endpoint-type,Values=$endpoint_type" \
      "Name=vpc-endpoint-state,Values=pending,pendingAcceptance,available" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'VpcEndpoints[0].VpcEndpointId' \
    --output text 2>/dev/null || true)"

  normalize_aws_text_value "$endpoint_id"
}

assert_service_available() {
  local service_name="$1"
  local description="$2"
  local available_name=""

  available_name="$(aws ec2 describe-vpc-endpoint-services \
    --service-names "$service_name" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'ServiceDetails[0].ServiceName' \
    --output text 2>/dev/null || true)"

  normalize_aws_text_value "$available_name" >/dev/null 2>&1 || \
    fail "VPC endpoint service '$description' is not available in region $AWS_DEFAULT_REGION"
}

wait_for_endpoint() {
  local endpoint_id="$1"
  local attempt=1
  local max_attempts=40
  local sleep_seconds=15
  local endpoint_state=""

  while [[ "$attempt" -le "$max_attempts" ]]; do
    endpoint_state="$(aws ec2 describe-vpc-endpoints \
      --vpc-endpoint-ids "$endpoint_id" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'VpcEndpoints[0].State' \
      --output text 2>/dev/null || true)"

    if [[ "$endpoint_state" == "available" ]]; then
      return 0
    fi

    case "$endpoint_state" in
      failed|rejected|expired|deleting|deleted)
        fail "VPC endpoint '$endpoint_id' reached terminal state '$endpoint_state'"
        ;;
    esac

    log "Waiting for VPC endpoint $endpoint_id to become available. Current state: ${endpoint_state:-unknown} (${attempt}/${max_attempts})"
    sleep "$sleep_seconds"
    ((attempt++))
  done

  fail "Timed out waiting for VPC endpoint '$endpoint_id' to become available"
}

ensure_gateway_endpoint() {
  local short_service_name="$1"
  local service_name=""
  local endpoint_id=""
  local current_route_table_ids_raw=""
  local current_route_table_ids=()
  local missing_route_table_ids=()
  local route_table_id=""
  local modify_cmd=()

  service_name="$(build_service_name "$short_service_name")"
  assert_service_available "$service_name" "$short_service_name"

  if endpoint_id="$(find_existing_endpoint_id "$service_name" "Gateway")"; then
    log "Gateway endpoint already exists for $short_service_name: $endpoint_id"
    current_route_table_ids_raw="$(aws ec2 describe-vpc-endpoints \
      --vpc-endpoint-ids "$endpoint_id" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'VpcEndpoints[0].RouteTableIds' \
      --output text 2>/dev/null || true)"

    if [[ -n "$current_route_table_ids_raw" && "$current_route_table_ids_raw" != "None" ]]; then
      read -r -a current_route_table_ids <<< "$current_route_table_ids_raw"
    fi

    for route_table_id in "${ROUTE_TABLE_IDS[@]}"; do
      if ! array_contains "$route_table_id" "${current_route_table_ids[@]}"; then
        missing_route_table_ids+=("$route_table_id")
      fi
    done

    if [[ "${#missing_route_table_ids[@]}" -gt 0 ]]; then
      log "Associating missing route tables to endpoint $endpoint_id: ${missing_route_table_ids[*]}"
      modify_cmd=(aws ec2 modify-vpc-endpoint --vpc-endpoint-id "$endpoint_id" --region "$AWS_DEFAULT_REGION" --add-route-table-ids)
      modify_cmd+=("${missing_route_table_ids[@]}")
      "${modify_cmd[@]}" >/dev/null
    fi

    wait_for_endpoint "$endpoint_id"
    return 0
  fi

  log "Creating gateway endpoint for $short_service_name"
  endpoint_id="$(aws ec2 create-vpc-endpoint \
    --vpc-id "$VPC_ENDPOINTS_VPC_ID" \
    --vpc-endpoint-type Gateway \
    --service-name "$service_name" \
    --route-table-ids "${ROUTE_TABLE_IDS[@]}" \
    --region "$AWS_DEFAULT_REGION" \
    --tag-specifications "ResourceType=vpc-endpoint,Tags=[{Key=Name,Value=onst-${short_service_name//./-}-endpoint},{Key=Solution,Value=Transcricao},{Key=ManagedBy,Value=BootstrapScript}]" \
    --query 'VpcEndpoint.VpcEndpointId' \
    --output text)"

  normalize_aws_text_value "$endpoint_id" >/dev/null || fail "Could not create gateway endpoint for service: $short_service_name"
  wait_for_endpoint "$endpoint_id"
}

ensure_interface_endpoint() {
  local short_service_name="$1"
  local endpoint_sg_id="$2"
  local service_name=""
  local endpoint_id=""
  local current_subnet_ids_raw=""
  local current_subnet_ids=()
  local current_security_group_ids_raw=""
  local current_security_group_ids=()
  local private_dns_enabled=""
  local missing_subnet_ids=()
  local missing_security_group_ids=()
  local subnet_id=""
  local modify_cmd=()

  service_name="$(build_service_name "$short_service_name")"
  assert_service_available "$service_name" "$short_service_name"

  if endpoint_id="$(find_existing_endpoint_id "$service_name" "Interface")"; then
    log "Interface endpoint already exists for $short_service_name: $endpoint_id"

    current_subnet_ids_raw="$(aws ec2 describe-vpc-endpoints \
      --vpc-endpoint-ids "$endpoint_id" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'VpcEndpoints[0].SubnetIds' \
      --output text 2>/dev/null || true)"
    if [[ -n "$current_subnet_ids_raw" && "$current_subnet_ids_raw" != "None" ]]; then
      read -r -a current_subnet_ids <<< "$current_subnet_ids_raw"
    fi

    current_security_group_ids_raw="$(aws ec2 describe-vpc-endpoints \
      --vpc-endpoint-ids "$endpoint_id" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'VpcEndpoints[0].Groups[*].GroupId' \
      --output text 2>/dev/null || true)"
    if [[ -n "$current_security_group_ids_raw" && "$current_security_group_ids_raw" != "None" ]]; then
      read -r -a current_security_group_ids <<< "$current_security_group_ids_raw"
    fi

    private_dns_enabled="$(aws ec2 describe-vpc-endpoints \
      --vpc-endpoint-ids "$endpoint_id" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'VpcEndpoints[0].PrivateDnsEnabled' \
      --output text 2>/dev/null || true)"

    for subnet_id in "${PRIVATE_SUBNET_IDS[@]}"; do
      if ! array_contains "$subnet_id" "${current_subnet_ids[@]}"; then
        missing_subnet_ids+=("$subnet_id")
      fi
    done

    if ! array_contains "$endpoint_sg_id" "${current_security_group_ids[@]}"; then
      missing_security_group_ids+=("$endpoint_sg_id")
    fi

    modify_cmd=(aws ec2 modify-vpc-endpoint --vpc-endpoint-id "$endpoint_id" --region "$AWS_DEFAULT_REGION")
    if [[ "${#missing_subnet_ids[@]}" -gt 0 ]]; then
      modify_cmd+=(--add-subnet-ids)
      modify_cmd+=("${missing_subnet_ids[@]}")
    fi
    if [[ "${#missing_security_group_ids[@]}" -gt 0 ]]; then
      modify_cmd+=(--add-security-group-ids)
      modify_cmd+=("${missing_security_group_ids[@]}")
    fi
    if [[ "$private_dns_enabled" != "True" && "$private_dns_enabled" != "true" ]]; then
      modify_cmd+=(--private-dns-enabled)
    fi

    if [[ "${#modify_cmd[@]}" -gt 6 ]]; then
      log "Updating interface endpoint $endpoint_id for service $short_service_name"
      "${modify_cmd[@]}" >/dev/null
    fi

    wait_for_endpoint "$endpoint_id"
    return 0
  fi

  log "Creating interface endpoint for $short_service_name"
  endpoint_id="$(aws ec2 create-vpc-endpoint \
    --vpc-id "$VPC_ENDPOINTS_VPC_ID" \
    --vpc-endpoint-type Interface \
    --service-name "$service_name" \
    --subnet-ids "${PRIVATE_SUBNET_IDS[@]}" \
    --security-group-ids "$endpoint_sg_id" \
    --private-dns-enabled \
    --region "$AWS_DEFAULT_REGION" \
    --tag-specifications "ResourceType=vpc-endpoint,Tags=[{Key=Name,Value=onst-${short_service_name//./-}-endpoint},{Key=Solution,Value=Transcricao},{Key=ManagedBy,Value=BootstrapScript}]" \
    --query 'VpcEndpoint.VpcEndpointId' \
    --output text)"

  normalize_aws_text_value "$endpoint_id" >/dev/null || fail "Could not create interface endpoint for service: $short_service_name"
  wait_for_endpoint "$endpoint_id"
}

split_csv_into_array "$VPC_ENDPOINTS_PRIVATE_SUBNET_IDS" PRIVATE_SUBNET_IDS
split_csv_into_array "$VPC_ENDPOINTS_ROUTE_TABLE_IDS" ROUTE_TABLE_IDS

[[ "${#PRIVATE_SUBNET_IDS[@]}" -gt 0 ]] || fail "VPC_ENDPOINTS_PRIVATE_SUBNET_IDS must include at least one subnet"
[[ "${#ROUTE_TABLE_IDS[@]}" -gt 0 ]] || fail "VPC_ENDPOINTS_ROUTE_TABLE_IDS must include at least one route table"

log "Step: bootstrap VPC endpoints for ONSTranscribe"
VPC_CIDR="$(describe_vpc_cidr)"
ENDPOINT_SG_ID="$(ensure_endpoint_security_group "$VPC_CIDR")"

for service_name in s3 dynamodb; do
  ensure_gateway_endpoint "$service_name"
done

for service_name in ecr.api ecr.dkr ecs ecs-agent ecs-telemetry logs sqs sts; do
  ensure_interface_endpoint "$service_name" "$ENDPOINT_SG_ID"
done

if is_true "$VPC_ENDPOINTS_ENABLE_SSM"; then
  for service_name in ssm ssmmessages ec2messages; do
    ensure_interface_endpoint "$service_name" "$ENDPOINT_SG_ID"
  done
fi

if is_true "$VPC_ENDPOINTS_ENABLE_BEDROCK"; then
  for service_name in bedrock-runtime bedrock-agent; do
    ensure_interface_endpoint "$service_name" "$ENDPOINT_SG_ID"
  done
fi

log "Bootstrap de VPC endpoints concluído com sucesso"
