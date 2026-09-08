import os
import json
import re
import time
import boto3


# Tabela de instalações consultada no Athena. O database vem de ATHENA_DATABASE e é
# informado no QueryExecutionContext, portanto o FROM não precisa de prefixo de schema.
DEFAULT_TABLE = "instalacoes"

QUERY = """
SELECT
    ins_id,
    nomelongo,
    nomecurto,
    sigla,
    estad_id,
    cos_id,
    tpins_id,
    nomedes
FROM {table}
WHERE dtentrada IS NOT NULL
  AND dtdesativa IS NULL
"""

POLL_INTERVAL = 5
MAX_WAIT_SECONDS = 120


def _normalize_nomelongo(value: str) -> str:
    if not value:
        return ""
    return re.sub(r'\s+', ' ', value).strip()


def _run_athena_query(athena_client, database: str, s3_output: str) -> str:
    table = os.environ.get("ATHENA_TABLE", DEFAULT_TABLE)
    response = athena_client.start_query_execution(
        QueryString=QUERY.format(table=table),
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": s3_output},
    )
    return response["QueryExecutionId"]


def _wait_for_query(athena_client, execution_id: str) -> None:
    elapsed = 0
    while elapsed < MAX_WAIT_SECONDS:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        result = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = result["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena query {state}: {reason}")
    raise TimeoutError(f"Athena query não concluiu em {MAX_WAIT_SECONDS}s")


def _fetch_results(athena_client, execution_id: str) -> list:
    installations = []
    paginator = athena_client.get_paginator("get_query_results")
    pages = paginator.paginate(QueryExecutionId=execution_id)

    header_skipped = False
    for page in pages:
        rows = page["ResultSet"]["Rows"]
        for row in rows:
            if not header_skipped:
                header_skipped = True
                continue
            cols = [c.get("VarCharValue", "") for c in row["Data"]]
            if len(cols) < 8:
                continue
            installations.append({
                "ins_id":    cols[0].strip(),
                "nomelongo": _normalize_nomelongo(cols[1]),
                "nomecurto": cols[2].strip(),
                "sigla":     cols[3].strip(),
                "estad_id":  cols[4].strip(),
                "cos_id":    cols[5].strip(),
                "tpins_id":  cols[6].strip(),
                "nomedes":   cols[7].strip(),
            })

    return installations


def lambda_handler(event, context):
    database = os.environ["ATHENA_DATABASE"]
    s3_output = os.environ["ATHENA_S3_OUTPUT"]
    bucket = os.environ["INSTALLATIONS_BUCKET"]
    s3_key = os.environ["INSTALLATIONS_S3_KEY"]
    region = os.environ.get("AWS_REGION", "us-east-1")

    athena_client = boto3.client("athena", region_name=region)
    s3_client = boto3.client("s3", region_name=region)

    print(f"Iniciando query Athena: database={database}")
    execution_id = _run_athena_query(athena_client, database, s3_output)
    print(f"QueryExecutionId: {execution_id}")

    _wait_for_query(athena_client, execution_id)
    print("Query concluída com sucesso.")

    installations = _fetch_results(athena_client, execution_id)
    print(f"Total de instalações ativas: {len(installations)}")

    body = json.dumps(installations, ensure_ascii=False)
    s3_client.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    print(f"Lista publicada em s3://{bucket}/{s3_key}")

    return {"statusCode": 200, "total": len(installations)}
