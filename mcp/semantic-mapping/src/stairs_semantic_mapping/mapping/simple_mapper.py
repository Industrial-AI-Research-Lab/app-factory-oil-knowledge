import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Sequence, Dict, List, Optional, Any

from stairs_semantic_mapping.infrastructure.embeddings import EmbeddingModel
from stairs_semantic_mapping.infrastructure.vector_query import VectorStore, VectorQueryResult, PgVectorStore
from stairs_semantic_mapping.mapping import BaseSemanticMapper


@dataclass
class SimpleMapperConfig:
    """
    Configuration for SimpleMapper.

    Parameters
    ----------
    top_k : int
        How many nearest names to return per input name.
    query_prefix : str
        Optional prefix applied to each raw input before embedding, e.g. "query: ".
        This is useful for instruction-tuned models like e5-large-multilingual.
    metadata_fields : List[str]
        Which metadata fields to extract from VectorQueryResult.metadatas.
        If empty, metadata will not be requested nor returned.

        Important: for PgVectorStore this list must be a subset of
        PgVectorStoreConfig.metadata_columns (otherwise the store cannot SELECT them).
    """
    top_k: int = 1
    query_prefix: str = "query: "
    where_clause_fields: Dict[str, Sequence[Any]] = field(default_factory=dict)
    metadata_fields: List[str] = field(default_factory=list)


class SimpleMapper(BaseSemanticMapper):
    """
    One-stage semantic mapper that searches the nearest items in a single vector store.

    Components
    ----------
    - embedder: EmbeddingModel
    - store: VectorStore (PgVectorStore or any compatible implementation)

    Output
    ------
    Returns ids, names, optional metadata, and optional distance scores.
    """

    def __init__(
        self,
        embedder: EmbeddingModel,
        store: VectorStore,
        config: Optional[SimpleMapperConfig] = None,
        logger: Optional[Any] = None,
    ):
        self.embedder = embedder
        self.store = store
        self.config = config or SimpleMapperConfig()
        self.logger = logger

        self._validate_store_metadata_fields()

    def _validate_store_metadata_fields(self) -> None:
        """
        Validate that the underlying store is capable of returning the requested metadata fields.

        For PgVectorStore we can inspect PgVectorStoreConfig.metadata_columns and ensure that
        all config.metadata_fields are present there. For other VectorStore implementations
        we skip validation (the store may provide metadata in a different way).
        """
        if not self.config.metadata_fields:
            return

        # Only PgVectorStore exposes an inspectable config with metadata_columns
        if not isinstance(self.store, PgVectorStore):
            if self.logger:
                self.logger.info(
                    "[MAPPER][INIT] store is not PgVectorStore; skipping metadata fields validation."
                )
            return

        cfg = self.store.config
        existing = set(cfg.metadata_columns or [])
        requested = set(self.config.metadata_fields) or set(self.config.where_clause_fields or [])

        missing = requested - existing
        if missing:
            raise ValueError(
                f"Store metadata_columns is missing requested fields {missing}. "
                f"Please add them to PgVectorStoreConfig.metadata_columns."
            )

        if self.logger:
            self.logger.info(
                f"[MAPPER][INIT] metadata fields validation passed; fields present: {requested}"
            )

    def _log(self, stage: str, msg: str) -> None:
        if self.logger:
            self.logger.info(f"[SEMANTIC MAPPER][{stage}] {msg}")

    def _build_queries(self, inputs: Sequence[str]) -> List[str]:
        """
        Apply model-specific prefixing or pre-processing to raw inputs.
        For e5-like models we expect the 'query: <text>' pattern for better performance.
        """
        prefix = self.config.query_prefix
        if not prefix:
            return list(inputs)
        return [f"{prefix}{x}" for x in inputs]

    @contextmanager
    def log_stage(self, stage: str, extra: str = ""):
        """
        Context manager for structured logging of mapping stages.

        Produces:
        [SEMANTIC MAPPER][{STAGE}] start ...
        [SEMANTIC MAPPER][{STAGE}] finished -- time=...
        """
        start = time.time()
        info = f" {extra}" if extra else ""
        self._log(stage, f"start{info}")
        try:
            yield
        finally:
            duration = time.time() - start
            self._log(stage, f"finished -- time={duration:.3f}s")

    def semantic_search(
        self,
        inputs: Sequence[str],
        top_k: int = 1,
        with_distances: bool = False,
    ) -> Dict[str, List[Optional[Any]]]:
        """
        Map input names to canonical names using a single nearest-neighbor search
        in the provided vector store.

        Parameters
        ----------
        inputs : Sequence[str]
            Raw names to be mapped.
        top_k : int, optional
            Number of nearest items per input. Overrides config.top_k if provided.
        with_distances : bool, optional
            If True, include distance scores in the result.

        Returns
        -------
        Dict[str, List[Optional[Any]]]
            Always:
                - "ids":   list[list[str]]
                - "names": list[list[str]]
            If config.metadata_fields is not empty:
                - "metadatas": list[list[dict]]
                  where each dict contains only keys from config.metadata_fields.
            If with_distances=True:
                - "scores": list[list[float]]
        """
        if not inputs:
            out: Dict[str, List[Optional[Any]]] = {"ids": [], "names": []}
            if self.config.metadata_fields:
                out["metadatas"] = []
            if with_distances:
                out["scores"] = []
            return out

        effective_top_k = top_k or self.config.top_k
        queries = self._build_queries(inputs)

        include: List[str] = ["results"]
        if with_distances:
            include.append("distances")
        if self.config.metadata_fields:
            include.append("metadatas")

        with self.log_stage("SIMPLE", f"(n={len(inputs)})"):
            embeddings = self.embedder.embed(queries)

            res: VectorQueryResult = self.store.query(
                query_embeddings=embeddings,
                top_k=effective_top_k,
                where=self.config.where_clause_fields,
                include=include,
            )

        ids_out = res.ids or [[] for _ in inputs]
        names_out = res.results or [[] for _ in inputs]

        out: Dict[str, List[Optional[Any]]] = {
            "ids": ids_out,
            "names": names_out,
        }

        if with_distances:
            out["scores"] = res.distances or [[] for _ in inputs]

        if self.config.metadata_fields:
            # Filter metadata dicts to only requested keys (and keep shape N x top_k)
            filtered: List[List[Dict[str, Any]]] = []
            metas = res.metadatas or [[] for _ in inputs]

            for metas_for_query in metas:
                row: List[Dict[str, Any]] = []
                for meta in metas_for_query:
                    if meta is None:
                        row.append({})
                        continue
                    row.append({k: meta.get(k) for k in self.config.metadata_fields})
                filtered.append(row)

            out["metadatas"] = filtered

        return out
