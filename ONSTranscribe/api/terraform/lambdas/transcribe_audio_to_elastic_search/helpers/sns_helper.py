import json
import logging
from helpers.environment_helper import EnvironmentHelper
from helpers.utils_helper import extract_key
from helpers.process_destination_helper import execute

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def process_sns_lambda(event, context):
    """
    Processa eventos SNS para a Lambda.
    """
    result = []

    # Obter e dividir o recorder_string_list em dicionário
    recorder_string_list = EnvironmentHelper.get_recorder_string_list()
    if not recorder_string_list:
        logger.error("Environment variable 'RecodersList' is not set.")
        return None

    recorder_split = recorder_string_list.split("_")
    recorder_dict = {}
    for item in recorder_split:
        parts = item.split("|")
        if len(parts) == 2:
            recorder_dict[parts[0]] = parts[1]
        else:
            logger.warning(f"Skipping invalid recorder entry: {item}")

    # Processar cada registro SNS
    for record in event.get("Records", []):
        if "Sns" not in record or "Message" not in record["Sns"]:
            logger.error("Invalid SNS record structure.")
            continue

        result_process = await process_record_async(record, recorder_dict, context)
        if result_process:
            result.append(result_process)

    return ",".join(result)

async def process_record_async(record, recorder_dict, context):
    """
    Processa individualmente um registro SNS.
    """
    key = ""
    result = {"success": False, "message": "", "additional": "", "audio": None}

    try:
        # Extrair a mensagem do SNS
        message = record["Sns"]["Message"]

        # Verificar se a mensagem já é um objeto JSON
        if isinstance(message, str):
            message = json.loads(message)

        # Validar e extrair a chave do S3
        validate_key = next(
            (x.get("s3", {}).get("object", {}).get("key") for x in message.get("Records", [])), None
        )
        if not validate_key:
            logger.error("No valid S3 Key found in the SNS message.")
            return None

        key_path = EnvironmentHelper.get_key_path()
        if not key_path:
            raise ValueError("Environment variable 'KeyPath' is not set.")

        key = extract_key(message, key_path)
        logger.info(f"Key extraída do SNS: {key}")

        # Processar a chave e o destino
        await execute(context, message, validate_key, key, result, recorder_dict)

        return result["message"]

    except Exception as ex:
        logger.error(f"Error processing object {key} => {str(ex)}")
        logger.error(f"Stack Trace => {ex}")
        raise
