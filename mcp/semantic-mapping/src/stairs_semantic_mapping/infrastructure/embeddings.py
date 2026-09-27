from typing import List, Protocol, Sequence
import numpy as np

import requests


class EmbeddingModel(Protocol):
    """
        Minimal protocol for embedding models used in semantic mapping.

        Any implementation must accept a sequence of texts and return
        a 2D NumPy array of shape (N, dim).
        """
    def embed(self, texts: Sequence[str]) -> np.ndarray:
        ...


def l2_normalize(vectors: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """
    L2-normalize vectors row-wise: shape (N, dim).
    Zero-norm rows are left as zeros.
    """
    if vectors.ndim != 2:
        raise ValueError(
            f"l2_normalize expects a 2D array (N, dim), got shape {vectors.shape}"
        )

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, eps)
    return vectors / norms


class NormalizingEmbeddingModel:
    """
    Wrapper around an EmbeddingModel that applies L2 normalization
    to all returned embeddings.

    Guarantees the invariant:

    "All embeddings passed to PgVectorStore are L2-normalized",

    which allows us to use the L2 operator (<->) in pgvector while
    effectively performing cosine similarity search.
    """
    def __init__(self, base: EmbeddingModel, eps: float = 1e-12):
        self.base = base
        self.eps = eps

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        raw = self.base.embed(texts)
        return l2_normalize(raw, eps=self.eps)


class TEIEmbeddingModel:
    """
    EmbeddingModel implementation that queries a Text Embeddings Inference (TEI)
    server via HTTP.

    Uses the TEI `/embed` endpoint and supports client-side batching for efficient
    inference. The server is expected to return embeddings as a JSON list of lists.

    Parameters
    ----------
    host : str
        Full TEI endpoint URL, e.g. "http://10.0.15.7:8081/embed".
    batch_size : int, optional
        Maximum number of texts to send in a single HTTP request. Default: 32.
    timeout : float, optional
        Request timeout in seconds. Default: 30.0.
    normalize_on_server : bool, optional
        Whether to ask TEI to normalize embeddings server-side. Defaults to False.
        (Client-side normalization is recommended and handled by NormalizingEmbeddingModel)
    access_token : str, optional
        Token for access to text-embedding server

    Notes
    -----
    - Large batches increase request body size and may trigger `413 Payload Too Large`
      depending on the TEI deployment (proxy limits, gateway configuration, etc.).
    - This class does not apply L2 normalization. Wrap it in `NormalizingEmbeddingModel`
      to enforce cosine-distance compatibility with pgvector `<->`.
    - The method `embed(texts)` returns a NumPy array of shape (N, dim).
    """

    def __init__(
        self,
        host: str,
        batch_size: int = 32,
        timeout: float = 30.0,
        normalize_on_server: bool = False,
        access_token: str | None = None,
    ):
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1 or batch_size > 128:
            raise ValueError("batch_size must be an int in [1, 128]")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout > 120:
            raise ValueError("timeout must be in (0, 120]")
        self.host = host
        self.batch_size = batch_size
        self.timeout = timeout
        self.normalize_on_server = normalize_on_server
        self.access_token = access_token

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        texts_list = list(texts)
        if not texts_list:
            return np.zeros((0, 0), dtype="float32")

        all_embs = []

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json"
        } if self.access_token else None

        for start in range(0, len(texts_list), self.batch_size):
            batch = texts_list[start : start + self.batch_size]

            payload = {
                "inputs": batch,
                "normalize": self.normalize_on_server,   # TEI server normalization (client-side is preferred instead)
                "truncate": False,
            }

            resp = requests.post(
                self.host, 
                json=payload, 
                headers=headers or None, 
                timeout=self.timeout
            )

            resp.raise_for_status()

            embs = np.asarray(resp.json(), dtype="float32")

            if embs.ndim == 1:
                embs = embs.reshape(1, -1)

            if embs.ndim != 2 or embs.shape[0] != len(batch) or embs.shape[1] == 0:
                raise ValueError("invalid TEI response shape")
            if all_embs and embs.shape[1] != all_embs[0].shape[1]:
                raise ValueError("inconsistent TEI embedding dimension")
            if not np.isfinite(embs).all():
                raise ValueError("TEI embeddings must contain only finite values")

            all_embs.append(embs)

        return np.vstack(all_embs)
