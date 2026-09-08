import asyncio
from helpers.sns_helper import process_sns_lambda

def lambda_handler(event, context):
    """
    Manipulador principal da Lambda.
    """
    # Aguarda a função assíncrona e retorna o resultado
    return asyncio.run(process_sns_lambda(event, context))
