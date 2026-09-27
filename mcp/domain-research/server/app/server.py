from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from .calculators.tools import register_calculator_tools
from .collection.build import FactBatchReference, build_collection_impl
from .collection.merge import STRICT
from .collection.query import inspect_collection_impl, query_collection_impl
from .collection.security import install_query_guard
from .config import Settings
from .documents.index import index_documents_impl
from .documents.search import (
    read_document_fragments_impl,
    search_documents_impl,
)
from .errors import ToolFailure
from .fact_models import FactInput
from .facts import validate_fact_batch_impl
from .ontology.read import read_ontology_impl
from .ontology.validation import validate_ontology_impl
from .web_broad_tools import register_web_broad_tools
from .web_tools import register_web_tools

mcp = FastMCP(name="Domain Research MCP")
register_web_tools(mcp)
register_web_broad_tools(mcp)
register_calculator_tools(mcp)
logger = logging.getLogger(__name__)


@mcp.tool(name="index_documents")
async def index_documents(
    documents: list[dict[str, Any]],
    upload_url: str,
) -> dict[str, Any]:
    try:
        return {
            "status": "ok",
            **asdict(await index_documents_impl(documents, upload_url)),
        }
    except ToolFailure as exc:
        return exc.as_result()


@mcp.tool(name="search_documents")
async def search_documents(
    index_url: str,
    query: str,
    expected_index_sha256: str,
    source_ids: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    try:
        result = await search_documents_impl(
            index_url,
            query,
            source_ids,
            limit,
            expected_index_sha256=expected_index_sha256,
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        return exc.as_result()


@mcp.tool(name="read_document_fragments")
async def read_document_fragments(
    index_url: str,
    fragment_ids: list[str],
    expected_index_sha256: str,
    max_chars_per_fragment: int = 8_000,
) -> dict[str, Any]:
    try:
        result = await read_document_fragments_impl(
            index_url,
            fragment_ids,
            max_chars_per_fragment,
            expected_index_sha256=expected_index_sha256,
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        return exc.as_result()


@mcp.tool(name="validate_ontology")
async def validate_ontology(
    ontology: Any,
    upload_url: str,
    expected_base_version: str | None = None,
) -> dict[str, Any]:
    try:
        result = await validate_ontology_impl(
            ontology,
            upload_url,
            expected_base_version,
        )
        logger.info(
            "[ONTOLOGY] ontology_id=%s version=%s sha256=%s — validated and uploaded",
            result.ontology_id,
            result.version,
            result.sha256,
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        logger.warning("[ONTOLOGY] code=%s — rejected", exc.code)
        return exc.as_result()


@mcp.tool(name="read_ontology")
async def read_ontology(
    ontology_url: str,
    expected_ontology_sha256: str,
) -> dict[str, Any]:
    try:
        result = await read_ontology_impl(
            ontology_url,
            expected_ontology_sha256,
        )
        logger.info("[ONTOLOGY] operation=read sha256=%s — verified", result.sha256)
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        logger.warning("[ONTOLOGY] operation=read code=%s — rejected", exc.code)
        return exc.as_result()


@mcp.tool(name="validate_fact_batch")
async def validate_fact_batch(
    index_url: str,
    expected_index_sha256: str,
    ontology_url: str,
    expected_ontology_sha256: str,
    facts: FactInput | None,
    upload_url: str,
) -> dict[str, Any]:
    try:
        result = await validate_fact_batch_impl(
            index_url,
            expected_index_sha256,
            ontology_url,
            expected_ontology_sha256,
            facts,
            upload_url,
        )
        logger.info(
            "[FACT_BATCH] facts=%d ontology_id=%s sha256=%s — validated and uploaded",
            result.fact_count,
            result.ontology_id,
            result.sha256,
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        logger.warning("[FACT_BATCH] code=%s — rejected", exc.code)
        return exc.as_result()


@mcp.tool(name="build_collection")
async def build_collection(
    index_url: str,
    expected_index_sha256: str,
    ontology_url: str,
    expected_ontology_sha256: str,
    fact_batches: list[FactBatchReference],
    upload_url: Annotated[
        str,
        Field(
            description=(
                "Presigned PUT URL for a .sqlite3 collection file, signed with "
                "Content-Type application/x-sqlite3."
            )
        ),
    ],
    entity_merge: Annotated[
        str | None,
        Field(
            description=(
                "Optional. 'strict' (default) keeps every fact ID unique across batches. "
                "'merge_identical' folds entity facts that share node_id and type_id into one node "
                "and unions their properties; a property with differing values keeps the value from "
                "the batch with the smallest SHA-256 and is listed in metadata entity_property_conflicts."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    try:
        result = await build_collection_impl(
            index_url,
            expected_index_sha256,
            ontology_url,
            expected_ontology_sha256,
            fact_batches,
            upload_url,
            entity_merge,
        )
        logger.info(
            "[COLLECTION] operation=build nodes=%d relations=%d observations=%d "
            "entity_merge=%s merged_entities=%d property_conflicts=%d sha256=%s — uploaded",
            result.counts["nodes"],
            result.counts["relations"],
            result.counts["observations"],
            entity_merge or STRICT,
            result.counts.get("merged_entities", 0),
            result.counts.get("property_conflicts", 0),
            result.sha256,
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        logger.warning("[COLLECTION] operation=build code=%s — rejected", exc.code)
        return exc.as_result()


@mcp.tool(name="inspect_collection")
async def inspect_collection(
    collection_url: str, expected_collection_sha256: str | None = None
) -> dict[str, Any]:
    try:
        result = await inspect_collection_impl(
            collection_url, expected_collection_sha256
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        logger.warning("[COLLECTION] operation=inspect code=%s — rejected", exc.code)
        return exc.as_result()


@mcp.tool(name="query_collection")
async def query_collection(
    collection_url: str,
    sql: str,
    parameters: list[str | int | float | None] | None = None,
    max_rows: int = 200,
    result_upload_url: str | None = None,
    expected_collection_sha256: str | None = None,
) -> dict[str, Any]:
    try:
        result = await query_collection_impl(
            collection_url,
            sql,
            parameters,
            max_rows,
            result_upload_url,
            expected_collection_sha256,
        )
        logger.info(
            "[COLLECTION] operation=query rows=%d truncated=%s — completed",
            len(result.rows),
            result.truncated,
        )
        return {"status": "ok", **asdict(result)}
    except ToolFailure as exc:
        logger.warning("[COLLECTION] operation=query code=%s — rejected", exc.code)
        return exc.as_result()


def json_each_runs_under_query_guard() -> bool:
    # SQLite before 3.45 declares the json_each virtual table through an authorizer action the
    # read-only guard denies; the analyst label subqueries and the calculator SQL all rely on it.
    connection = sqlite3.connect(":memory:")
    try:
        guard = install_query_guard(connection, 1000, 1000)
        connection.execute("SELECT value FROM json_each('[1]')").fetchall()
        return not guard.denied
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if not json_each_runs_under_query_guard():
        logger.error(
            "[SERVICE] sqlite=%s — refusing to start: json_each is denied by the query guard",
            sqlite3.sqlite_version,
        )
        raise SystemExit(1)
    logger.info(
        "[SERVICE] sqlite=%s — json_each runs under the query guard",
        sqlite3.sqlite_version,
    )
    settings = Settings.from_env()
    logger.info(
        "[SERVICE] name=domain-research-mcp host=%s port=%d — starting",
        settings.host,
        settings.port,
    )
    mcp.run(
        transport="http",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
