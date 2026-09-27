"""Works business adapter over the source core and secure foundations.

Implements the source works-mapping behavior without FastMCP. SQL and
ranking stay in the byte-identical source core; this module owns only
TEI/PgVector wiring, query formatting, and result validation/conversion.

Batch/top_k validation and error redaction live in the MCP layer.
Inputs and secrets are never logged.
"""

import logging
from contextlib import nullcontext

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
from stairs_semantic_mapping.mapping.hierarchical_mapper import (
    HierarchicalMapper,
    HierarchicalMapperConfig,
)
from stairs_semantic_mapping.utils.mapping_result_model import (
    InputWorkMappingUnit,
    OutputWorkMappingResult,
    OutputWorkMappingUnit,
    StandardWorkUnit,
)

__all__ = [
    "search_works",
    "InputWorkMappingUnit",
    "OutputWorkMappingResult",
    "OutputWorkMappingUnit",
    "StandardWorkUnit",
]

logger = logging.getLogger(__name__)


def _format_work_query(unit: InputWorkMappingUnit) -> str:
    """Format a work unit as a source-compatible query string.

    Same shape as the source job, including the trailing delimiter when
    no hierarchy parts are present.

    Args:
        unit: Validated input work item.

    Returns:
        ``"<name>, <measurement> || <bwd> || <ose> || <occ>"``.
    """
    parts = [p for p in (unit.bwd_name, unit.ose_name, unit.occ_name) if p is not None]
    return f"{unit.work_name}, {unit.work_measurement} || " + " || ".join(parts)


def _build_works_mapper(settings: RuntimeSettings) -> HierarchicalMapper:
    """Build a source ``HierarchicalMapper`` from explicit settings.

    Same wiring as the source job: category/task TEI hosts with batches,
    one secure connection provider, ``granular_category`` and
    ``granular_name`` stores, mapper config. Opens no connection.

    Args:
        settings: Explicit settings; no environment read here.

    Returns:
        Configured two-stage mapper over validating stores.
    """
    token = _unwrap_token(settings)
    cat_base = TEIEmbeddingModel(
        host=settings.embedding_host_categories,
        batch_size=settings.batch_size_categories,
        access_token=token,
    )
    names_base = TEIEmbeddingModel(
        host=settings.embedding_host_tasks,
        batch_size=settings.batch_size_tasks,
        access_token=token,
    )
    provider = SecurePostgresConnectionProvider(settings.db_config)
    cat_store = ValidatingPgVectorStore(
        conn_provider=provider,
        config=PgVectorStoreConfig(
            table_name="granular_category",
            text_column="category_name",
        ),
    )
    names_store = ValidatingPgVectorStore(
        conn_provider=provider,
        config=PgVectorStoreConfig(
            table_name="granular_name",
            text_column="name",
            metadata_columns=[
                settings.category_id_field,
                settings.category_name_field,
                "task_code",
                "measurement",
            ],
        ),
        required_metadata_fields=[
            settings.category_id_field,
            settings.category_name_field,
            "measurement",
            "task_code",
        ],
    )
    config = HierarchicalMapperConfig(
        category_top_k=settings.top_k_categories,
        name_top_k=settings.top_k_names,
        category_id_field=settings.category_id_field,
        category_name_field=settings.category_name_field,
        measurement_field="measurement",
        task_code_field="task_code",
    )
    return HierarchicalMapper(
        category_embedder=NormalizingEmbeddingModel(cat_base),
        name_embedder=NormalizingEmbeddingModel(names_base),
        category_store=cat_store,
        task_store=names_store,
        config=config,
    )


def search_works(
    works: list[InputWorkMappingUnit],
    top_k: int,
    *,
    settings: RuntimeSettings,
) -> OutputWorkMappingResult:
    """Map works via the source two-stage mapper with secure guards.

    Builds query strings, calls the source mapper with distances, then
    enforces outer/inner cardinality, rejects every ``None``, and converts
    rows to typed output.

    Args:
        works: Input work items in request order.
        top_k: Neighbours per input; bounds are validated in the MCP layer.
        settings: Explicit runtime settings; no environment read here.

    Returns:
        Mapped works in input order with distances; empty input returns
        an empty result without building TEI/PG clients.

    Raises:
        ValueError: ``works mapping failed`` when the source mapper fails,
            ``mismatched mapping cardinalities`` for shape/``None``/score
            violations. Messages never include inputs, values, or secrets.
    """
    if not works:
        return OutputWorkMappingResult(result=[])
    top_k = _require_top_k(top_k)
    logger.info("[SEMANTIC_MCP][ADAPTER] op=search_works n=%d top_k=%d -- start", len(works), top_k)
    mapper = _build_works_mapper(settings)
    queries = [_format_work_query(work) for work in works]
    # One read-only cursor for the whole batch; fakes without a provider run bare.
    provider = getattr(getattr(mapper, "category_store", None), "conn_provider", None)
    scope = provider.request_scope() if hasattr(provider, "request_scope") else nullcontext()
    try:
        with scope:
            res = mapper.semantic_search(inputs=queries, top_k=top_k, with_distances=True)
    except (ValueError, TypeError) as exc:
        logger.error("[SEMANTIC_MCP][ADAPTER] op=search_works -- failed error_type=%s", type(exc).__name__)
        raise ValueError("works mapping failed") from None
    if not (
        len(res["ids"]) == len(works)
        and len(res["names"]) == len(works)
        and len(res["measurements"]) == len(works)
        and len(res["categories"]) == len(works)
        and len(res["scores"]) == len(works)
    ):
        raise ValueError("mismatched mapping cardinalities")
    units: list[OutputWorkMappingUnit] = []
    for i, work in enumerate(works):
        ids = res["ids"][i]
        names = res["names"][i]
        meas = res["measurements"][i]
        cats = res["categories"][i]
        scores = res["scores"][i]
        if not (len(ids) == len(names) == len(meas) == len(cats) == len(scores)):
            raise ValueError("mismatched mapping cardinalities")
        if any(v is None for v in (*ids, *names, *meas, *cats, *scores)):
            raise ValueError("mismatched mapping cardinalities")
        standard = tuple(
            StandardWorkUnit(
                code=str(ids[j]),
                name=str(names[j]),
                measurement=str(meas[j]),
                category=str(cats[j]),
            )
            for j in range(len(ids))
        )
        units.append(
            OutputWorkMappingUnit(
                **work.model_dump(),
                top_k_units=standard,
                top_k_distances=_to_distances(scores),
            )
        )
    logger.info("[SEMANTIC_MCP][ADAPTER] op=search_works n=%d -- finished", len(works))
    return OutputWorkMappingResult(result=units)
