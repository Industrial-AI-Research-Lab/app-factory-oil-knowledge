from __future__ import annotations

import asyncio
import hashlib
import json

from pydantic import ValidationError
from typing_extensions import TypedDict

from ..config import Settings
from ..errors import ToolFailure
from ..fact_models import Fact, parse_facts
from ..facts import _parse_ontology, _validate_facts
from ..http_io import download_bytes, temporary_call_directory, upload_bytes
from ..ontology.models import OntologyDocument
from . import SCHEMA_VERSION, BuildCollectionResult
from .merge import STRICT, merge_facts, parse_entity_merge
from .storage import build_database


class FactBatchReference(TypedDict):
    url: str
    expected_sha256: str


async def build_collection_impl(
    index_url: str,
    expected_index_sha256: str,
    ontology_url: str,
    expected_ontology_sha256: str,
    fact_batches: object,
    upload_url: str,
    entity_merge: object = None,
) -> BuildCollectionResult:
    policy = parse_entity_merge(entity_merge)
    references = _batch_references(fact_batches)
    settings = Settings.from_env()
    index_bytes = await download_bytes(
        index_url,
        max_bytes=settings.max_download_bytes,
        expected_sha256=expected_index_sha256,
    )
    ontology_bytes = await download_bytes(
        ontology_url,
        max_bytes=settings.max_download_bytes,
        expected_sha256=expected_ontology_sha256,
    )
    batch_bytes = [
        await download_bytes(
            reference["url"],
            max_bytes=settings.max_download_bytes,
            expected_sha256=reference["expected_sha256"],
        )
        for reference in references
    ]
    ontology = _parse_ontology(ontology_bytes)
    parsed = _parse_batches(
        batch_bytes,
        hashlib.sha256(index_bytes).hexdigest(),
        hashlib.sha256(ontology_bytes).hexdigest(),
        ontology,
    )
    merged = merge_facts(parsed, policy)
    batch_sha256s = sorted(
        hashlib.sha256(payload).hexdigest() for payload in batch_bytes
    )
    fingerprint = _input_fingerprint(
        hashlib.sha256(index_bytes).hexdigest(),
        hashlib.sha256(ontology_bytes).hexdigest(),
        batch_sha256s,
    )
    with temporary_call_directory() as directory:
        index_path = directory / "documents.sqlite3"
        collection_path = directory / "collection.sqlite3"
        await asyncio.to_thread(index_path.write_bytes, index_bytes)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "ontology_id": ontology.ontology_id,
            "ontology_version": ontology.version,
            "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
            "ontology_sha256": hashlib.sha256(ontology_bytes).hexdigest(),
            "fact_batch_sha256s": batch_sha256s,
            "input_fingerprint": fingerprint,
        }
        if policy != STRICT:
            metadata["entity_merge"] = policy
            metadata["merged_entity_count"] = merged.merged_entities
            metadata["entity_property_conflicts"] = merged.property_conflicts
        counts = await asyncio.to_thread(
            build_database,
            index_path,
            collection_path,
            merged.facts,
            metadata,
        )
        if policy != STRICT:
            counts = {
                **counts,
                "merged_entities": merged.merged_entities,
                "property_conflicts": len(merged.property_conflicts),
            }
        payload = await asyncio.to_thread(collection_path.read_bytes)
    upload = await upload_bytes(
        upload_url, payload, content_type="application/x-sqlite3"
    )
    return BuildCollectionResult(
        schema_version=SCHEMA_VERSION,
        ontology_id=ontology.ontology_id,
        ontology_version=ontology.version,
        index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        ontology_sha256=hashlib.sha256(ontology_bytes).hexdigest(),
        fact_batch_sha256s=batch_sha256s,
        input_fingerprint=fingerprint,
        counts=counts,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
    )


def _batch_references(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ToolFailure(
            "FACT_SCHEMA_INVALID", "fact_batches must be a non-empty list"
        )
    if any(
        not isinstance(item, dict)
        or set(item) != {"url", "expected_sha256"}
        or not isinstance(item["url"], str)
        or not isinstance(item["expected_sha256"], str)
        for item in value
    ):
        raise ToolFailure("FACT_SCHEMA_INVALID", "Fact batch references are invalid")
    return value


def _parse_batches(
    payloads: list[bytes],
    index_sha256: str,
    ontology_sha256: str,
    ontology: OntologyDocument,
) -> list[tuple[str, list[Fact]]]:
    batches: list[tuple[str, list[Fact]]] = []
    for payload in payloads:
        try:
            value = json.loads(payload.decode("utf-8"))
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "schema_version",
                    "index_sha256",
                    "ontology_sha256",
                    "ontology_id",
                    "ontology_version",
                    "facts",
                }
                or value["schema_version"] != 1
                or value["index_sha256"] != index_sha256
                or value["ontology_sha256"] != ontology_sha256
                or value["ontology_id"] != ontology.ontology_id
                or value["ontology_version"] != ontology.version
            ):
                raise ValueError("batch metadata does not match inputs")
            batch_facts = parse_facts(value["facts"])
            _validate_facts(batch_facts, ontology)
            batches.append((hashlib.sha256(payload).hexdigest(), batch_facts))
        except (UnicodeDecodeError, ValueError, TypeError, ValidationError):
            raise ToolFailure(
                "FACT_SCHEMA_INVALID",
                "Approved fact batch does not match schema version 1",
            ) from None
    return batches


def _input_fingerprint(
    index_sha256: str, ontology_sha256: str, batches: list[str]
) -> str:
    value = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "index_sha256": index_sha256,
            "ontology_sha256": ontology_sha256,
            "fact_batch_sha256s": sorted(batches),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(value).hexdigest()
