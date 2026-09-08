# pip install scikit-learn openai numpy pandas
import os
import numpy as np
import pandas as pd
from typing import Iterable, List, Optional

from sklearn.base import BaseEstimator, TransformerMixin

from openai import OpenAI


class OpenAIEmbeddingTransformer(BaseEstimator, TransformerMixin):
    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        batch_size: int = 96,
        normalize: bool = True,
    ):
        self.model = model
        self.api_key = api_key
        self.batch_size = batch_size
        self.normalize = normalize
        self._client = None
        self.embedding_dim_ = None

    def __getstate__(self):
        """Remove o cliente da OpenAI antes de salvar para evitar erro de pickle"""
        state = self.__dict__.copy()
        state["_client"] = None
        return state

    def __setstate__(self, state):
        """Restaura estado sem cliente (será recriado na primeira chamada)"""
        self.__dict__.update(state)
        self._client = None

    def _get_client(self):
        if self._client is None:
            key = self.api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                raise ValueError("Defina OPENAI_API_KEY ou passe api_key=")
            os.environ["OPENAI_API_KEY"] = key
            self._client = OpenAI()
        return self._client

    def fit(self, X, y=None):
        X = self._as_text_list(X)
        if len(X) == 0:
            raise ValueError("Entrada vazia em fit().")
        vec = self._embed_batch(X[:1])
        self.embedding_dim_ = len(vec[0])
        return self

    def transform(self, X):
        X = self._as_text_list(X)
        if len(X) == 0:
            return np.empty((0, getattr(self, "embedding_dim_", 0)), dtype=np.float32)

        out = []
        for i in range(0, len(X), self.batch_size):
            batch = X[i : i + self.batch_size]
            out.extend(self._embed_batch(batch))

        arr = np.asarray(out, dtype=np.float32)
        if self.normalize and arr.size > 0:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            arr = arr / norms
        return arr

    def _embed_batch(self, inputs: List[str]) -> List[List[float]]:
        resp = self._get_client().embeddings.create(model=self.model, input=inputs)
        vecs = [d.embedding for d in resp.data]
        if self.embedding_dim_ is None and vecs:
            self.embedding_dim_ = len(vecs[0])
        return vecs

    @staticmethod
    def _as_text_list(X) -> List[str]:
        if isinstance(X, pd.Series):
            return X.astype(str).tolist()
        if isinstance(X, pd.DataFrame):
            if X.shape[1] != 1:
                raise ValueError("Passe apenas uma coluna de texto.")
            return X.iloc[:, 0].astype(str).tolist()
        return ["" if x is None else str(x) for x in X]