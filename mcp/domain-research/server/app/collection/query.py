from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

from ..config import Settings
from ..errors import ToolFailure
from ..http_io import download_bytes, temporary_call_directory, upload_bytes
from . import (
    SCHEMA_VERSION,
    CollectionTable,
    InspectCollectionResult,
    QueryCollectionResult,
)
from .query_inputs import sql_parameters, sql_statement
from .results import source_refs, typed_row
from .security import QueryGuard, install_query_guard, query_failure

_TABLES = (
    CollectionTable(
        "nodes", ["node_id", "type_id", "properties_json", "source_id", "fragment_id"]
    ),
    CollectionTable(
        "relations",
        [
            "relation_id",
            "type_id",
            "source_node_id",
            "source_type_id",
            "target_node_id",
            "target_type_id",
            "source_id",
            "fragment_id",
        ],
    ),
    CollectionTable(
        "observations",
        [
            "observation_id",
            "type_id",
            "subject_node_id",
            "subject_type_id",
            "value_json",
            "unit",
            "conditions_json",
            "source_id",
            "fragment_id",
        ],
    ),
    CollectionTable("fragments", ["fragment_id", "source_id", "locator", "text"]),
    CollectionTable(
        "source_status", ["source_id", "filename", "content_type", "status"]
    ),
)


async def inspect_collection_impl(
    collection_url: str, expected_collection_sha256: str | None = None
) -> InspectCollectionResult:
    settings = Settings.from_env()
    payload = await download_bytes(
        collection_url,
        max_bytes=settings.max_download_bytes,
        expected_sha256=expected_collection_sha256,
    )
    with temporary_call_directory() as directory:
        path = directory / "collection.sqlite3"
        await asyncio.to_thread(path.write_bytes, payload)
        result = await asyncio.to_thread(
            _inspect,
            path,
            settings.sqlite_operation_budget,
            settings.sqlite_max_value_bytes,
        )

    return replace(result, collection_sha256=hashlib.sha256(payload).hexdigest())


async def query_collection_impl(
    collection_url: str,
    sql: object,
    parameters: object = None,
    max_rows: int = 200,
    result_upload_url: str | None = None,
    expected_collection_sha256: str | None = None,
) -> QueryCollectionResult:
    settings = Settings.from_env()
    statement = sql_statement(sql)
    values = sql_parameters(parameters)
    if (
        isinstance(max_rows, bool)
        or not isinstance(max_rows, int)
        or not 1 <= max_rows <= settings.max_query_rows
    ):
        raise ToolFailure(
            "INPUT_INVALID", f"max_rows must be between 1 and {settings.max_query_rows}"
        )
    payload = await download_bytes(
        collection_url,
        max_bytes=settings.max_download_bytes,
        expected_sha256=expected_collection_sha256,
    )
    with temporary_call_directory() as directory:
        path = directory / "collection.sqlite3"
        await asyncio.to_thread(path.write_bytes, payload)
        result = await asyncio.to_thread(
            _query,
            path,
            statement,
            values,
            max_rows,
            settings.sqlite_operation_budget,
            settings.sqlite_max_value_bytes,
        )
    result = replace(
        result,
        collection_sha256=hashlib.sha256(payload).hexdigest(),
        sql=statement,
        parameters=values,
    )
    if result_upload_url is not None:
        document = asdict(result)
        del document["sha256"], document["size_bytes"]
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        uploaded = await upload_bytes(
            result_upload_url, encoded, content_type="application/json"
        )
        result = replace(result, sha256=uploaded.sha256, size_bytes=uploaded.size_bytes)
    return result


def _open_collection(
    path: Path,
    operation_budget: int,
    max_value_bytes: int,
) -> tuple[sqlite3.Connection, QueryGuard]:
    connection: sqlite3.Connection | None = None
    guard: QueryGuard | None = None
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        guard = install_query_guard(connection, operation_budget, max_value_bytes)
        metadata = dict(connection.execute("SELECT key, value_json FROM metadata"))
        if (
            version != SCHEMA_VERSION
            or json.loads(metadata.get("schema_version", "null")) != SCHEMA_VERSION
        ):
            raise ValueError("unsupported collection schema")
        return connection, guard
    except ToolFailure:
        if connection is not None:
            connection.close()
        raise
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        if guard is not None and (failure := query_failure(guard, exc)) is not None:
            raise failure from None
        raise ToolFailure(
            "COLLECTION_INVALID", "Collection is invalid or unsupported"
        ) from None
    except (ValueError, TypeError):
        if connection is not None:
            connection.close()
        if guard is not None and (failure := guard.failure()) is not None:
            raise failure from None
        raise ToolFailure(
            "COLLECTION_INVALID", "Collection is invalid or unsupported"
        ) from None


def _inspect(
    path: Path, operation_budget: int, max_value_bytes: int
) -> InspectCollectionResult:
    connection: sqlite3.Connection | None = None
    guard: QueryGuard | None = None
    try:
        connection, guard = _open_collection(path, operation_budget, max_value_bytes)
        metadata = {
            key: json.loads(value)
            for key, value in connection.execute("SELECT key, value_json FROM metadata")
        }
        counts = {
            table.name: connection.execute(
                f"SELECT COUNT(*) FROM {table.name}"
            ).fetchone()[0]
            for table in _TABLES
        }
        if "entity_merge" in metadata:
            counts["merged_entities"] = metadata["merged_entity_count"]
            counts["property_conflicts"] = len(metadata["entity_property_conflicts"])
        return InspectCollectionResult(
            schema_version=metadata["schema_version"],
            ontology_id=metadata["ontology_id"],
            ontology_version=metadata["ontology_version"],
            input_checksums={
                key: metadata[key]
                for key in (
                    "index_sha256",
                    "ontology_sha256",
                    "fact_batch_sha256s",
                    "input_fingerprint",
                )
            },
            counts=counts,
            tables=list(_TABLES),
        )
    except ToolFailure:
        raise
    except sqlite3.Error as exc:
        if guard is not None and (failure := query_failure(guard, exc)) is not None:
            raise failure from None
        raise ToolFailure(
            "COLLECTION_INVALID", "Collection is invalid or unsupported"
        ) from None
    except (ValueError, TypeError, KeyError):
        if guard is not None and (failure := guard.failure()) is not None:
            raise failure from None
        raise ToolFailure(
            "COLLECTION_INVALID", "Collection is invalid or unsupported"
        ) from None
    finally:
        if connection is not None:
            connection.close()


def _query(
    path: Path,
    sql: str,
    parameters: list[str | int | float | None],
    max_rows: int,
    operation_budget: int,
    max_value_bytes: int,
) -> QueryCollectionResult:
    connection: sqlite3.Connection | None = None
    guard: QueryGuard | None = None

    try:
        connection, guard = _open_collection(path, operation_budget, max_value_bytes)
        cursor = connection.execute(sql, parameters)
        columns = [description[0] for description in cursor.description or ()]
        if len(columns) != len(set(columns)):
            raise ToolFailure(
                "SQLITE_QUERY_INVALID",
                "SQLite query result has duplicate column labels",
            )
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [typed_row(columns, row) for row in fetched[:max_rows]]
        return QueryCollectionResult(
            columns, rows, truncated, source_refs(columns, rows)
        )
    except ToolFailure:
        raise
    except sqlite3.Error as exc:
        if guard is not None and (failure := query_failure(guard, exc)) is not None:
            raise failure from None
        raise ToolFailure(
            "SQLITE_QUERY_INVALID", "SQLite query could not be executed"
        ) from None
    finally:
        if connection is not None:
            connection.close()
