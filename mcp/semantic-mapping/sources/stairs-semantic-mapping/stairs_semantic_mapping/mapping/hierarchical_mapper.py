import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Sequence, Dict, List, Optional, Any, Mapping

import numpy as np

from stairs_semantic_mapping.infrastructure.embeddings import EmbeddingModel
from stairs_semantic_mapping.infrastructure.vector_query import VectorStore, VectorQueryResult, PgVectorStore
from stairs_semantic_mapping.mapping import BaseSemanticMapper


@dataclass
class HierarchicalMapperConfig:
    """
    Configuration for HierarchicalMapper.

    Parameters
    ----------
    category_top_k : int
        How many nearest categories to consider for each input name.
    name_top_k : int
        How many nearest task names to return per input name.
    query_prefix : str
        Optional prefix applied to each raw name before embedding, e.g. "query: ".
        This is useful for instruction-tuned models like e5-large-multilingual.
    """
    category_top_k: int = 5
    name_top_k: int = 1
    query_prefix: str = "query: "
    category_id_field: str = "category_id"  # required metadata field for HierarchicalMapper
    category_name_field: str = "category_name"  # required metadata field for HierarchicalMapper
    measurement_field: str = "measurement"  # required metadata field for HierarchicalMapper
    task_code_field: str = "task_code"  # required metadata field for HierarchicalMapper


class HierarchicalMapper(BaseSemanticMapper):
    """
    Hierarchical semantic mapper that first maps each task name with hierarchical information to a small set
    of candidate activities' categories, and then searches for the best task names restricted to those categories.

    The mapper uses two embedding models and two vector stores:

    - category_embedder + category_store  - for category-level search
    - name_embedder     + task_store      - for task-name-level search

    Both stores are expected to be backed by pgvector via PgVectorStore,
    but any implementation of `VectorStore` is supported.
    """

    def __init__(
        self,
        category_embedder: EmbeddingModel,
        name_embedder: EmbeddingModel,
        category_store: VectorStore,
        task_store: VectorStore,
        config: Optional[HierarchicalMapperConfig] = None,
        logger: Optional[Any] = None,
    ):
        self.category_embedder = category_embedder
        self.name_embedder = name_embedder
        self.category_store = category_store
        self.task_store = task_store
        self.config = config or HierarchicalMapperConfig()
        self.logger = logger

        self._validate_task_store_metadata()  # validate required columns in task_store metadata to include


    def _validate_task_store_metadata(self) -> None:
        """
            Validate that the task_store is capable of returning required metadata
            fields that the hierarchical mapper relies on.

            Required fields:
                - config.category_id_field
                - config.category_name_field
                - config.measurement_field

            This validation ensures that:
              1) task_store is a PgVectorStore (the only implementation where we can
                 reliably inspect metadata_columns);
              2) PgVectorStoreConfig.metadata_columns contains all required fields.

            If metadata is incomplete, the mapper cannot function correctly,
            because these fields must appear inside VectorQueryResult.metadatas.

            Raises
            ------
            ValueError:
                If metadata_columns does not contain all required fields.
            """
        from stairs_semantic_mapping.infrastructure.vector_query import PgVectorStore

        # If task_store is not an instance of PgVectorStore - skip validation
        if not isinstance(self.task_store, PgVectorStore):
            if self.logger:
                self.logger.info(
                    "[MAPPER][INIT] task_store is not PgVectorStore; "
                    "skipping metadata validation."
                )
            return

        cfg = self.task_store.config
        existing = set(cfg.metadata_columns or [])

        required = {
            self.config.category_id_field,
            self.config.category_name_field,
            self.config.measurement_field,
        }

        missing = required - existing
        if missing:
            raise ValueError(
                f"Task store metadata_columns is missing required fields {missing}. "
                f"Please add them to PgVectorStoreConfig.metadata_columns"
            )

        if self.logger:
            self.logger.info(
                f"[MAPPER][INIT] metadata validation passed; fields present: {required}"
            )


    def _log(self, stage: str, msg: str) -> None:
        if self.logger:
            self.logger.info(f"[SEMANTIC MAPPER][{stage}] {msg}")


    def _build_queries(self, names: Sequence[str]) -> List[str]:
        """
        Apply model-specific prefixing or pre-processing to raw names.
        For e5-like models we expected "query: <text>" pattern for better performance.
        """
        prefix = self.config.query_prefix
        if not prefix:
            return list(names)
        return [f"{prefix}{name}" for name in names]


    @contextmanager
    def log_stage(self, stage: str, extra: str = ""):
        """
        Context manager for structured logging of mapping stages.

        Automatically produces:
        [MAPPER][{STAGE}] start ...
        [MAPPER][{STAGE}] finished -- time=...
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
        Map input names to canonical task names using a two-stage
        categories -> names search.

        Parameters
        ----------
        inputs : Sequence[str]
            Raw task names to be mapped.
        top_k : int, optional
            Number of nearest task names per input name.
            Overrides config.name_top_k if provided.
        with_distances : bool, optional
            If True, include distance scores in the result.

        Returns
        -------
        Dict[str, List[Optional[Any]]]
            Keys:
                - "ids":        list[list[str]] or list[None]
                - "names":      list[list[str]] or list[None]
                - "categories": list[list[str]] or list[None]
            If with_distances=True:
                - "scores":     list[list[float]]
        """
        if not inputs:
            return {
                "ids": [],
                "names": [],
                "categories": [],
                **({"scores": []} if with_distances else {}),
            }

        effective_top_k = top_k or self.config.name_top_k
        queries = self._build_queries(inputs)  # for e5-large-multilingual

        # Stage 1: Category mapping
        with self.log_stage("CATEGORIES", f"(n={len(inputs)})"):
            # Create embeddings using categories embedder
            cat_embeddings = self.category_embedder.embed(queries)

            # Execute similarity search
            cat_result: VectorQueryResult = self.category_store.query(
                query_embeddings=cat_embeddings,
                top_k=self.config.category_top_k,
                include=["ids"],  # category ids are enough for the further filtering during names mapping
            )

        # Stage 2: Name mapping with category filter
        with self.log_stage("NAMES", f"(n={len(inputs)})"):
            # Create embeddings using names embedder
            name_embeddings = self.name_embedder.embed(queries)

            # Pre-allocate result containers
            ids_lst: List[Optional[List[str]]] = []
            names_lst: List[Optional[List[str]]] = []
            measurements_lst: List[Optional[List[str]]] = []
            categories_lst: List[Optional[List[str]]] = []
            scores_lst: List[Optional[List[float]]] = []

            # Get info about category_id and category_name field names in database from config
            cat_id_key = self.config.category_id_field
            cat_name_key = self.config.category_name_field
            measurement_key = self.config.measurement_field
            task_code_field = self.config.task_code_field

            for i, emb in enumerate(name_embeddings):
                # Get top-k categories for the i-th query name
                raw_cat_ids = cat_result.ids[i] if cat_result.ids else []
                cat_ids = [int(cid) for cid in raw_cat_ids if cid is not None]  # convert to int for filtering

                where_clause = {cat_id_key: cat_ids} if cat_ids else None  # data for filtering

                name_res = self.task_store.query(
                    query_embeddings=emb.reshape(1, -1),
                    top_k=effective_top_k,
                    where=where_clause,
                    include=["results", "distances", "metadatas"],
                )

                q_ids = name_res.ids[0] if name_res.ids else []
                q_names = name_res.results[0] if name_res.results else []
                q_scores = name_res.distances[0] if name_res.distances else []
                q_metas = name_res.metadatas[0] if name_res.metadatas else []

                # Extract categories names and measurements from metadata after mapping
                q_cat_names = []
                q_measurements = []
                q_codes = []
                for query_meta in q_metas:
                    if query_meta is None:
                        continue
                    if cat_name_key in query_meta:
                        q_cat_names.append(str(query_meta[cat_name_key]))
                    if measurement_key in query_meta:
                        q_measurements.append(str(query_meta[measurement_key]))
                    if task_code_field in query_meta:
                        q_codes.append(str(query_meta[task_code_field]))

                # Add results to the global arrays
                ids_lst.append(q_codes)
                names_lst.append(q_names)
                measurements_lst.append(q_measurements)
                categories_lst.append(q_cat_names)
                scores_lst.append(q_scores)


        # Stage 3: Prepare result
        result: Dict[str, List[Optional[Any]]] = {
            "ids": ids_lst,
            "names": names_lst,
            "measurements": measurements_lst,
            "categories": categories_lst
        }

        if with_distances:
            result["scores"] = scores_lst

        return result
