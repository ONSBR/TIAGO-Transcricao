from datetime import datetime
from opensearchpy import OpenSearch
from opensearchpy.exceptions import NotFoundError
import logging

class OpenSearchRepository:
    def __init__(self, context, uris, index):
        """
        Inicializa o repositório OpenSearch.
        :param context: Contexto da Lambda.
        :param uris: Lista de URIs do OpenSearch.
        :param index: Nome do índice.
        """
        self.context = context
        self.uris = uris
        self.index = index
        self.client = None

    def _get_client(self):
        """
        Retorna o cliente OpenSearch conectado.
        """
        if not self.client:
            self.client = OpenSearch(hosts=self.uris, timeout=120)
        return self.client

    async def insert_or_update(self, item):
        """
        Insere ou atualiza um documento no OpenSearch.
        :param item: Documento do tipo `Audio` a ser inserido/atualizado.
        :return: Boolean indicando sucesso.
        """
        log_execucao = datetime.now().timestamp()
        client = self._get_client()

        try:
            document = item.to_dict()

            response = client.index(index=self.index, id=item.id, body=document)
            if not response.get("result") in ["created", "updated"]:
                raise Exception("Erro ao inserir/atualizar o documento.")
            return True
        except Exception as ex:
            logging.error(f"{log_execucao} => Erro ao inserir ou editar um dado no OpenSearch em {self.uris[0]} no índice {self.index} => {str(ex)}")
            raise

    async def get_by(self, id): 
        """
        Obtém um documento do OpenSearch pelo ID.
        :param id: ID do documento.
        :return: Documento encontrado ou False, se não existir.
        """
        log_execucao = datetime.now().timestamp()
        client = self._get_client()

        try:
            response = client.get(index=self.index, id=id)
            return response.get("_source")
        except NotFoundError:
            # Retornar None se o documento não for encontrado
            logging.info(f"{log_execucao} => Documento com ID {id} não encontrado no índice {self.index}.")
            return None
        except Exception as ex:
            logging.error(f"{log_execucao} => Erro ao buscar um dado no OpenSearch por id em {self.uris[0]} no índice {self.index} => {str(ex)}")
            raise


    async def partial_update(self, id, object_partially_updated):
        """
        Realiza uma atualização parcial de um documento no OpenSearch.
        :param id: ID do documento.
        :param object_partially_updated: Dados a serem atualizados.
        :return: Boolean indicando sucesso.
        """
        log_execucao = datetime.now().timestamp()
        client = self._get_client()

        try:
            response = client.update(
                index=self.index,
                id=id,
                body={
                    "doc": object_partially_updated
                },
                retry_on_conflict=3,
                refresh=True
            )
            return response.get("result") == "updated"
        except Exception as ex:
            logging.error(f"{log_execucao} => Erro ao atualizar um documento com id {id} no OpenSearch {self.uris[0]} no índice {self.index} => {str(ex)}")
            raise
