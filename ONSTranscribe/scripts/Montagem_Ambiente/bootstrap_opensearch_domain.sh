#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

ENV_FILE_PATH="${1:-$DEFAULT_ENV_FILE}"
load_env "$ENV_FILE_PATH"

require_command aws
require_command jq
require_command mktemp

require_env \
  AWS_PROFILE \
  AWS_DEFAULT_REGION \
  TFVARS_FILE \
  OPENSEARCH_DOMAIN_NAME \
  OPENSEARCH_INDEX_NAME \
  OPENSEARCH_BOOTSTRAP_VPC_ID \
  OPENSEARCH_BOOTSTRAP_SUBNET_IDS \
  OPENSEARCH_SECURITY_GROUP_NAME \
  OPENSEARCH_ENGINE_VERSION \
  OPENSEARCH_INSTANCE_TYPE \
  OPENSEARCH_INSTANCE_COUNT \
  OPENSEARCH_VOLUME_SIZE \
  OPENSEARCH_VOLUME_TYPE \
  OPENSEARCH_TLS_SECURITY_POLICY

require_file "$TFVARS_FILE"
require_absolute_path "TFVARS_FILE" "$TFVARS_FILE"

require_positive_integer() {
  local var_name="$1"
  local value="$2"

  [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail "$var_name deve ser um inteiro positivo"
}

require_positive_integer "OPENSEARCH_INSTANCE_COUNT" "$OPENSEARCH_INSTANCE_COUNT"
require_positive_integer "OPENSEARCH_VOLUME_SIZE" "$OPENSEARCH_VOLUME_SIZE"

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

json_array_from_values() {
  jq -cn '$ARGS.positional' --args "$@"
}

normalize_json_document() {
  local value="$1"

  jq -cS . <<<"$value" 2>/dev/null || printf '%s' "$value"
}

describe_vpc_cidr() {
  local cidr=""

  cidr="$(aws ec2 describe-vpcs \
    --vpc-ids "$OPENSEARCH_BOOTSTRAP_VPC_ID" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'Vpcs[0].CidrBlock' \
    --output text)"

  normalize_aws_text_value "$cidr" || \
    fail "Não foi possível resolver o CIDR da VPC para o bootstrap do OpenSearch: $OPENSEARCH_BOOTSTRAP_VPC_ID"
}

resolve_allowed_cidrs() {
  if [[ -n "${OPENSEARCH_ALLOWED_CIDRS:-}" ]]; then
    split_csv_into_array "$OPENSEARCH_ALLOWED_CIDRS" ALLOWED_CIDRS
  else
    ALLOWED_CIDRS=("$(describe_vpc_cidr)")
  fi

  [[ "${#ALLOWED_CIDRS[@]}" -gt 0 ]] || fail "OPENSEARCH_ALLOWED_CIDRS não contém nenhum CIDR válido"
}

resolve_domain_security_group_id() {
  local group_id=""

  group_id="$(aws ec2 describe-security-groups \
    --filters \
      "Name=vpc-id,Values=$OPENSEARCH_BOOTSTRAP_VPC_ID" \
      "Name=group-name,Values=$OPENSEARCH_SECURITY_GROUP_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || true)"

  normalize_aws_text_value "$group_id"
}

ensure_domain_security_group() {
  local group_id=""
  local cidr=""

  if group_id="$(resolve_domain_security_group_id)"; then
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Security group do domínio OpenSearch já existe: $group_id" >&2
  else
    printf '%s - %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "Criando security group dedicado do OpenSearch: $OPENSEARCH_SECURITY_GROUP_NAME" >&2
    group_id="$(aws ec2 create-security-group \
      --group-name "$OPENSEARCH_SECURITY_GROUP_NAME" \
      --description "Security group do dominio OpenSearch do ONSTranscribe" \
      --vpc-id "$OPENSEARCH_BOOTSTRAP_VPC_ID" \
      --region "$AWS_DEFAULT_REGION" \
      --query 'GroupId' \
      --output text)"

    normalize_aws_text_value "$group_id" >/dev/null || fail "Não foi possível criar o security group do OpenSearch"

    aws ec2 create-tags \
      --resources "$group_id" \
      --region "$AWS_DEFAULT_REGION" \
      --tags \
        "Key=Name,Value=$OPENSEARCH_SECURITY_GROUP_NAME" \
        "Key=Solution,Value=Transcricao" \
        "Key=ManagedBy,Value=BootstrapScript" >/dev/null
  fi

  for cidr in "${ALLOWED_CIDRS[@]}"; do
    aws ec2 authorize-security-group-ingress \
      --group-id "$group_id" \
      --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=$cidr,Description=Allow HTTPS to OpenSearch domain}]" \
      --region "$AWS_DEFAULT_REGION" >/dev/null 2>&1 || true
  done

  printf '%s\n' "$group_id"
}

describe_domain_json_or_empty() {
  aws opensearch describe-domain \
    --domain-name "$OPENSEARCH_DOMAIN_NAME" \
    --region "$AWS_DEFAULT_REGION" \
    --output json 2>/dev/null || true
}

resolve_domain_endpoint_from_json() {
  local domain_json="$1"

  printf '%s' "$domain_json" | jq -r '
    .DomainStatus.Endpoints.vpc //
    .DomainStatus.Endpoint //
    (.DomainStatus.Endpoints | if type == "object" then (to_entries[0].value // empty) else empty end) //
    empty
  '
}

wait_for_domain_ready() {
  local attempt=1
  local max_attempts=120
  local sleep_seconds=30
  local domain_json=""
  local endpoint=""
  local processing=""

  while [[ "$attempt" -le "$max_attempts" ]]; do
    domain_json="$(describe_domain_json_or_empty)"
    [[ -n "$domain_json" ]] || fail "Não foi possível descrever o domínio OpenSearch: $OPENSEARCH_DOMAIN_NAME"

    processing="$(printf '%s' "$domain_json" | jq -r '.DomainStatus.Processing // "false"')"
    endpoint="$(resolve_domain_endpoint_from_json "$domain_json")"

    if [[ "$processing" == "false" ]] && normalize_aws_text_value "$endpoint" >/dev/null 2>&1; then
      return 0
    fi

    log "Aguardando o domínio OpenSearch ficar disponível: processamento=${processing:-unknown} endpoint=${endpoint:-pending} (${attempt}/${max_attempts})"
    sleep "$sleep_seconds"
    ((attempt++))
  done

  fail "Timeout aguardando o domínio OpenSearch ficar disponível: $OPENSEARCH_DOMAIN_NAME"
}

build_access_policy_file() {
  local output_file="$1"
  local account_id="$2"

  jq -cn \
    --arg resource "arn:aws:es:$AWS_DEFAULT_REGION:$account_id:domain/$OPENSEARCH_DOMAIN_NAME/*" \
    '{
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Principal: {AWS: "*"},
          Action: "es:ESHttp*",
          Resource: $resource
        }
      ]
    }' >"$output_file"
}

build_cluster_config_file() {
  local output_file="$1"
  local subnet_count="${#DOMAIN_SUBNET_IDS[@]}"
  local zone_awareness_enabled="false"
  local availability_zone_count=0

  if (( subnet_count > 1 )); then
    [[ "$subnet_count" -eq 2 || "$subnet_count" -eq 3 ]] || \
      fail "OPENSEARCH_BOOTSTRAP_SUBNET_IDS deve conter 1, 2 ou 3 subnets. Valores atuais: $subnet_count"
    (( OPENSEARCH_INSTANCE_COUNT >= subnet_count )) || \
      fail "Com múltiplas AZs, OPENSEARCH_INSTANCE_COUNT deve ser maior ou igual à quantidade de subnets informadas"
    zone_awareness_enabled="true"
    availability_zone_count="$subnet_count"
  fi

  jq -n \
    --arg instance_type "$OPENSEARCH_INSTANCE_TYPE" \
    --argjson instance_count "$OPENSEARCH_INSTANCE_COUNT" \
    --argjson zone_awareness_enabled "$zone_awareness_enabled" \
    --argjson availability_zone_count "$availability_zone_count" \
    '{
      InstanceType: $instance_type,
      InstanceCount: $instance_count,
      DedicatedMasterEnabled: false,
      WarmEnabled: false,
      MultiAZWithStandbyEnabled: false,
      ZoneAwarenessEnabled: $zone_awareness_enabled
    } + (
      if $zone_awareness_enabled then
        {ZoneAwarenessConfig: {AvailabilityZoneCount: $availability_zone_count}}
      else
        {}
      end
    )' >"$output_file"
}

build_create_domain_payload_file() {
  local output_file="$1"
  local access_policy_file="$2"
  local cluster_config_file="$3"
  local domain_sg_id="$4"
  local subnet_ids_json=""

  subnet_ids_json="$(json_array_from_values "${DOMAIN_SUBNET_IDS[@]}")"

  jq -n \
    --arg domain_name "$OPENSEARCH_DOMAIN_NAME" \
    --arg engine_version "$OPENSEARCH_ENGINE_VERSION" \
    --arg volume_type "$OPENSEARCH_VOLUME_TYPE" \
    --arg tls_security_policy "$OPENSEARCH_TLS_SECURITY_POLICY" \
    --arg domain_sg_id "$domain_sg_id" \
    --arg access_policies "$(cat "$access_policy_file")" \
    --argjson cluster_config "$(cat "$cluster_config_file")" \
    --argjson subnet_ids "$subnet_ids_json" \
    --argjson volume_size "$OPENSEARCH_VOLUME_SIZE" \
    '{
      DomainName: $domain_name,
      EngineVersion: $engine_version,
      ClusterConfig: $cluster_config,
      EBSOptions: {
        EBSEnabled: true,
        VolumeType: $volume_type,
        VolumeSize: $volume_size
      },
      AccessPolicies: $access_policies,
      IPAddressType: "ipv4",
      VPCOptions: {
        SubnetIds: $subnet_ids,
        SecurityGroupIds: [$domain_sg_id]
      },
      EncryptionAtRestOptions: {Enabled: true},
      NodeToNodeEncryptionOptions: {Enabled: true},
      DomainEndpointOptions: {
        EnforceHTTPS: true,
        TLSSecurityPolicy: $tls_security_policy
      },
      AdvancedSecurityOptions: {
        Enabled: false
      },
      TagList: [
        {Key: "Name", Value: $domain_name},
        {Key: "Solution", Value: "Transcricao"},
        {Key: "ManagedBy", Value: "BootstrapScript"}
      ]
    }' >"$output_file"
}

build_update_domain_payload_file() {
  local output_file="$1"
  local access_policy_file="$2"
  local security_group_ids_json="$3"
  local subnet_ids_json="$4"

  jq -n \
    --arg domain_name "$OPENSEARCH_DOMAIN_NAME" \
    --arg tls_security_policy "$OPENSEARCH_TLS_SECURITY_POLICY" \
    --arg access_policies "$(cat "$access_policy_file")" \
    --argjson security_group_ids "$security_group_ids_json" \
    --argjson subnet_ids "$subnet_ids_json" \
    '{
      DomainName: $domain_name,
      AccessPolicies: $access_policies,
      VPCOptions: {
        SubnetIds: $subnet_ids,
        SecurityGroupIds: $security_group_ids
      },
      DomainEndpointOptions: {
        EnforceHTTPS: true,
        TLSSecurityPolicy: $tls_security_policy
      }
    }' >"$output_file"
}

upsert_tfvars_string_assignment() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  local escaped_value=""
  local temp_file=""

  escaped_value="$(hcl_escape_string "$value")"
  temp_file="$(mktemp)"

  awk -v key="$key" -v line="$key = \"$escaped_value\"" '
    BEGIN {updated = 0}
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      print line
      updated = 1
      next
    }
    {print}
    END {
      if (!updated) {
        print line
      }
    }
  ' "$file_path" >"$temp_file"

  mv "$temp_file" "$file_path"
}

sync_tfvars_with_domain() {
  local endpoint="$1"

  upsert_tfvars_string_assignment "$TFVARS_FILE" "domain_name" "$OPENSEARCH_DOMAIN_NAME"
  upsert_tfvars_string_assignment "$TFVARS_FILE" "index_name" "$OPENSEARCH_INDEX_NAME"
  upsert_tfvars_string_assignment "$TFVARS_FILE" "elastic_search_uri" "https://$endpoint"
  log "terraform.tfvars sincronizado com o domínio OpenSearch bootstrapado"
}

current_domain_requires_update() {
  local domain_json="$1"
  local expected_policy_file="$2"
  local bootstrap_sg_id="$3"
  local requested_subnets_json="$4"
  local current_vpc_id=""
  local fgac_enabled=""
  local current_subnets_json=""
  local current_subnets_normalized=""
  local requested_subnets_normalized=""
  local current_security_group_ids_json=""
  local expected_policy_normalized=""
  local current_policy_normalized=""
  local current_enforce_https=""
  local current_tls_policy=""

  current_vpc_id="$(printf '%s' "$domain_json" | jq -r '.DomainStatus.VPCOptions.VPCId // empty')"
  [[ "$current_vpc_id" == "$OPENSEARCH_BOOTSTRAP_VPC_ID" ]] || \
    fail "O domínio OpenSearch existente está em outra VPC: ${current_vpc_id:-sem-vpc}. Esperado: $OPENSEARCH_BOOTSTRAP_VPC_ID"

  fgac_enabled="$(printf '%s' "$domain_json" | jq -r '.DomainStatus.AdvancedSecurityOptions.Enabled // false')"
  if [[ "$fgac_enabled" == "true" ]]; then
    fail "O domínio OpenSearch existente está com fine-grained access control habilitado. Segundo a AWS, esse recurso não pode ser desabilitado depois de ativado. Para a solução atual, recrie o domínio com outro nome ou exclua o domínio existente e rode o bootstrap novamente."
  fi

  current_subnets_json="$(printf '%s' "$domain_json" | jq -c '.DomainStatus.VPCOptions.SubnetIds // []')"
  current_subnets_normalized="$(printf '%s' "$current_subnets_json" | jq -c 'sort')"
  requested_subnets_normalized="$(printf '%s' "$requested_subnets_json" | jq -c 'sort')"
  [[ "$current_subnets_normalized" == "$requested_subnets_normalized" ]] || \
    fail "O domínio OpenSearch existente usa subnets diferentes das informadas no bootstrap. Atual: $current_subnets_normalized Esperado: $requested_subnets_normalized"

  current_security_group_ids_json="$(printf '%s' "$domain_json" | jq -c '.DomainStatus.VPCOptions.SecurityGroupIds // []')"
  if ! printf '%s' "$current_security_group_ids_json" | jq -e --arg sg "$bootstrap_sg_id" 'index($sg) != null' >/dev/null; then
    return 0
  fi

  current_enforce_https="$(printf '%s' "$domain_json" | jq -r '.DomainStatus.DomainEndpointOptions.EnforceHTTPS // false')"
  current_tls_policy="$(printf '%s' "$domain_json" | jq -r '.DomainStatus.DomainEndpointOptions.TLSSecurityPolicy // empty')"
  if [[ "$current_enforce_https" != "true" || "$current_tls_policy" != "$OPENSEARCH_TLS_SECURITY_POLICY" ]]; then
    return 0
  fi

  expected_policy_normalized="$(normalize_json_document "$(cat "$expected_policy_file")")"
  current_policy_normalized="$(normalize_json_document "$(printf '%s' "$domain_json" | jq -r '.DomainStatus.AccessPolicies // "{}"')")"
  if [[ "$current_policy_normalized" != "$expected_policy_normalized" ]]; then
    return 0
  fi

  return 1
}

reconcile_existing_domain() {
  local domain_json="$1"
  local expected_policy_file="$2"
  local bootstrap_sg_id="$3"
  local requested_subnets_json="$4"
  local update_payload_file="$5"
  local current_security_group_ids_json=""
  local desired_security_group_ids_json=""

  if ! current_domain_requires_update "$domain_json" "$expected_policy_file" "$bootstrap_sg_id" "$requested_subnets_json"; then
    log "Domínio OpenSearch existente já está compatível com a solução atual"
    return 0
  fi

  current_security_group_ids_json="$(printf '%s' "$domain_json" | jq -c '.DomainStatus.VPCOptions.SecurityGroupIds // []')"
  desired_security_group_ids_json="$(printf '%s' "$current_security_group_ids_json" | jq -c --arg sg "$bootstrap_sg_id" 'if index($sg) == null then . + [$sg] else . end')"

  build_update_domain_payload_file \
    "$update_payload_file" \
    "$expected_policy_file" \
    "$desired_security_group_ids_json" \
    "$requested_subnets_json"

  log "Atualizando configuração do domínio OpenSearch existente para o padrão compatível com a solução"
  aws opensearch update-domain-config \
    --cli-input-json "file://$update_payload_file" \
    --region "$AWS_DEFAULT_REGION" >/dev/null

  wait_for_domain_ready
}

create_domain_if_missing() {
  local create_payload_file="$1"

  log "Criando domínio OpenSearch compatível com o ONSTranscribe: $OPENSEARCH_DOMAIN_NAME"
  aws opensearch create-domain \
    --cli-input-json "file://$create_payload_file" \
    --region "$AWS_DEFAULT_REGION" >/dev/null

  wait_for_domain_ready
}

split_csv_into_array "$OPENSEARCH_BOOTSTRAP_SUBNET_IDS" DOMAIN_SUBNET_IDS
[[ "${#DOMAIN_SUBNET_IDS[@]}" -gt 0 ]] || fail "OPENSEARCH_BOOTSTRAP_SUBNET_IDS deve conter pelo menos uma subnet"

resolve_allowed_cidrs

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ACCOUNT_ID="$(aws sts get-caller-identity --region "$AWS_DEFAULT_REGION" --query Account --output text)"
DOMAIN_SG_ID="$(ensure_domain_security_group)"
ACCESS_POLICY_FILE="$TMP_DIR/access-policy.json"
CLUSTER_CONFIG_FILE="$TMP_DIR/cluster-config.json"
CREATE_PAYLOAD_FILE="$TMP_DIR/create-domain.json"
UPDATE_PAYLOAD_FILE="$TMP_DIR/update-domain.json"
REQUESTED_SUBNETS_JSON="$(json_array_from_values "${DOMAIN_SUBNET_IDS[@]}")"

build_access_policy_file "$ACCESS_POLICY_FILE" "$ACCOUNT_ID"
build_cluster_config_file "$CLUSTER_CONFIG_FILE"
build_create_domain_payload_file "$CREATE_PAYLOAD_FILE" "$ACCESS_POLICY_FILE" "$CLUSTER_CONFIG_FILE" "$DOMAIN_SG_ID"

log "Step: bootstrap do domínio OpenSearch do ONSTranscribe"
DOMAIN_JSON="$(describe_domain_json_or_empty)"

if [[ -n "$DOMAIN_JSON" ]]; then
  reconcile_existing_domain "$DOMAIN_JSON" "$ACCESS_POLICY_FILE" "$DOMAIN_SG_ID" "$REQUESTED_SUBNETS_JSON" "$UPDATE_PAYLOAD_FILE"
else
  create_domain_if_missing "$CREATE_PAYLOAD_FILE"
fi

FINAL_DOMAIN_JSON="$(describe_domain_json_or_empty)"
[[ -n "$FINAL_DOMAIN_JSON" ]] || fail "Não foi possível descrever o domínio OpenSearch após o bootstrap"
FINAL_ENDPOINT="$(resolve_domain_endpoint_from_json "$FINAL_DOMAIN_JSON")"
normalize_aws_text_value "$FINAL_ENDPOINT" >/dev/null 2>&1 || \
  fail "Não foi possível resolver o endpoint HTTPS do domínio OpenSearch"

sync_tfvars_with_domain "$FINAL_ENDPOINT"
log "Bootstrap do domínio OpenSearch concluído: https://$FINAL_ENDPOINT"
