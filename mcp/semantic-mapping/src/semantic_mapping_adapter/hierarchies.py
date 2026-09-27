"""Hierarchies business adapter over the source core and secure foundations.

Implements the source hierarchies-mapping behavior without FastMCP. SQL
and ranking stay in the byte-identical source core; this module owns only
TEI/PgVector wiring and result validation/conversion.

Batch/level/top-k validation lives in the MCP layer.
Inputs and secrets are never logged.
"""

import logging

from ._common import to_distances as _to_distances
from ._common import unwrap_tei_token as _unwrap_token
from ._common import require_top_k as _require_top_k
from .config import RuntimeSettings
from .infrastructure import SecurePostgresConnectionProvider, ValidatingPgVectorStore
from stairs_semantic_mapping.infrastructure.embeddings import (
    NormalizingEmbeddingModel,
    TEIEmbeddingModel,
)
from stairs_semantic_mapping.infrastructure.vector_query import PgVectorStoreConfig
from stairs_semantic_mapping.mapping.simple_mapper import SimpleMapper, SimpleMapperConfig
from stairs_semantic_mapping.utils.mapping_result_model import (
    OutputHierarchyMappingResult,
    OutputHierarchyMappingUnit,
    StandardHierarchyUnit,
)

__all__ = [
    "OutputHierarchyMappingResult",
    "OutputHierarchyMappingUnit",
    "StandardHierarchyUnit",
    "search_hierarchies",
]

logger = logging.getLogger(__name__)


def _build_hierarchies_mapper(
    settings: RuntimeSettings, level: int, top_k: int
) -> SimpleMapper:
    """Build hierarchy SimpleMapper from explicit settings.

    Same wiring as the source job: hierarchy TEI host,
    ``semantic_hierarchical_works`` store, ``code_hierarchical_work_type``
    level filter. Opens no connection.

    Args:
        settings: Explicit settings; no environment read here.
        level: Hierarchy level filter value.
        top_k: Mapper default top-k.

    Returns:
        Configured source SimpleMapper over the secure provider.
    """
    token = _unwrap_token(settings)
    base = TEIEmbeddingModel(
        host=settings.embedding_host_hierarchies,
        batch_size=settings.batch_size_tasks,
        access_token=token,
    )
    provider = SecurePostgresConnectionProvider(settings.db_config)
    store = ValidatingPgVectorStore(
        conn_provider=provider,
        config=PgVectorStoreConfig(
            table_name="semantic_hierarchical_works",
            text_column="name",
            metadata_columns=["code"],
        ),
        required_metadata_fields=["code"],
    )
    config = SimpleMapperConfig(
        top_k=top_k,
        where_clause_fields={"code_hierarchical_work_type": [level]},
        metadata_fields=["code"],
    )
    return SimpleMapper(
        embedder=NormalizingEmbeddingModel(base),
        store=store,
        config=config,
    )


def search_hierarchies(
    hierarchies: list[str], level: int, top_k: int, *, settings: RuntimeSettings
) -> OutputHierarchyMappingResult:
    """Map hierarchy names via source SimpleMapper with level filter.

    Calls the source mapper with inputs unchanged and distances enabled,
    then converts the raw dict to typed result models.

    Args:
        hierarchies: Input hierarchy names in order.
        level: Hierarchy level filter value.
        top_k: Neighbours per input.
        settings: Explicit runtime settings (keyword-only).

    Returns:
        Typed hierarchy mapping result in input order with distances.

    Raises:
        ValueError: ``hierarchy mapping failed`` when the source mapper
            fails (adapter diagnostics intentionally not exposed),
            ``mismatched mapping cardinalities`` for shape/metadata/``None``
            or distance violations. Messages never include inputs, values,
            or secrets.
    """
    top_k = _require_top_k(top_k)
    mapper = _build_hierarchies_mapper(settings, level=level, top_k=top_k)
    logger.info("[SEMANTIC_MCP][ADAPTER] op=search_hierarchies n=%d level=%d top_k=%d -- start", len(hierarchies), level, top_k)
    try:
        res = mapper.semantic_search(inputs=hierarchies, top_k=top_k, with_distances=True)
    except (ValueError, TypeError) as exc:
        logger.error("[SEMANTIC_MCP][ADAPTER] op=search_hierarchies -- failed error_type=%s", type(exc).__name__)
        raise ValueError("hierarchy mapping failed") from None
    metas = res.get("metadatas", [])
    names_lst = res["names"]
    scores_lst = res["scores"]
    if not (
        len(metas) == len(hierarchies)
        and len(names_lst) == len(hierarchies)
        and len(scores_lst) == len(hierarchies)
    ):
        raise ValueError("mismatched mapping cardinalities")
    units: list[OutputHierarchyMappingUnit] = []
    for i, raw in enumerate(hierarchies):
        raw_metas = metas[i]
        raw_names = names_lst[i]
        raw_scores = scores_lst[i]
        if not (len(raw_metas) == len(raw_names) == len(raw_scores)):
            raise ValueError("mismatched mapping cardinalities")
        if any(not isinstance(m, dict) or m.get("code") is None for m in raw_metas):
            raise ValueError("mismatched mapping cardinalities")
        if any(v is None for v in (*raw_names, *raw_scores)):
            raise ValueError("mismatched mapping cardinalities")
        codes = [str(m.get("code")) for m in raw_metas]
        names = [str(v) for v in raw_names]
        scores = _to_distances(raw_scores)
        standard = tuple(
            StandardHierarchyUnit(code=codes[j], name=names[j]) for j in range(len(codes))
        )
        units.append(
            OutputHierarchyMappingUnit(
                hierarchical_work_name=raw,
                top_k_units=standard,
                top_k_distances=tuple(scores),
            )
        )
    logger.info("[SEMANTIC_MCP][ADAPTER] op=search_hierarchies n=%d -- finished", len(hierarchies))
    return OutputHierarchyMappingResult(result=units)
