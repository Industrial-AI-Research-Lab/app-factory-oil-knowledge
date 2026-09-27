from __future__ import annotations

import sqlite3

import pytest
from app.errors import ToolFailure
from app.facts import validate_fact_batch_impl
from app.ontology.validation import validate_ontology_impl
from knowledge_test_support import (
    fact_fixture,
    indexed_fragment,
    ontology_fixture,
    sha256,
    valid_facts,
)


async def approved_inputs(httpx_mock):
    index_bytes, fragment_id, fragment_text = await indexed_fragment(httpx_mock)
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology-upload")
    await validate_ontology_impl(
        ontology_fixture(),
        "https://objects.test/ontology-upload",
    )
    ontology_bytes = httpx_mock.get_requests()[-1].content
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    return index_bytes, ontology_bytes, fragment_id, fragment_text


def _document_index_bytes(
    tmp_path, text: object, *, include_source: bool
) -> tuple[bytes, str]:
    fragment_id = "a" * 64
    index_path = tmp_path / "index.sqlite3"
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            "CREATE TABLE index_metadata (schema_version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO index_metadata VALUES (1)")
        connection.execute("CREATE TABLE sources (source_id TEXT PRIMARY KEY)")
        if include_source:
            connection.execute("INSERT INTO sources VALUES ('source')")
        connection.execute(
            "CREATE TABLE fragments (fragment_id TEXT, source_id TEXT, text)"
        )
        connection.execute(
            "INSERT INTO fragments VALUES (?, ?, ?)",
            (fragment_id, "source", text),
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()
    return index_path.read_bytes(), fragment_id


@pytest.mark.asyncio
async def test_fact_batch_rejects_unknown_entity_type(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    fact = fact_fixture(fragment_id, fragment_text)
    fact["type_id"] = "missing"

    with pytest.raises(ToolFailure, match="FACT_TYPE_UNKNOWN"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact],
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
async def test_fact_batch_rejects_unknown_entity_property(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    fact = fact_fixture(fragment_id, fragment_text)
    fact["properties"] = {"recipe.missing": "Sample A"}

    with pytest.raises(ToolFailure, match="FACT_TYPE_UNKNOWN"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact],
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
async def test_fact_batch_rejects_unknown_observation_condition(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    fact = valid_facts(fragment_id, fragment_text)[3]
    fact["conditions"] = {"missing": 75}

    with pytest.raises(ToolFailure, match="FACT_TYPE_UNKNOWN"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact],
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
async def test_fact_batch_rejects_missing_required_property(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    fact = fact_fixture(fragment_id, fragment_text)
    fact["properties"] = {}

    with pytest.raises(ToolFailure, match="FACT_SCHEMA_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact],
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
async def test_fact_batch_classifies_empty_evidence(httpx_mock):
    fact = fact_fixture("0" * 64, "")

    with pytest.raises(ToolFailure, match="FACT_EVIDENCE_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            "0" * 64,
            "https://objects.test/ontology",
            "1" * 64,
            [fact],
            "https://objects.test/facts-upload",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_fact_batch_classifies_missing_relation_endpoint(httpx_mock):
    relation = valid_facts("0" * 64, "evidence")[2]
    relation.pop("source_entity_id")

    with pytest.raises(ToolFailure, match="FACT_ENDPOINT_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            "0" * 64,
            "https://objects.test/ontology",
            "1" * 64,
            [relation],
            "https://objects.test/facts-upload",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_fact_batch_classifies_empty_measurement_unit(httpx_mock):
    observation = valid_facts("0" * 64, "evidence")[3]
    observation["unit"] = ""

    with pytest.raises(ToolFailure, match="FACT_MEASUREMENT_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            "0" * 64,
            "https://objects.test/ontology",
            "1" * 64,
            [observation],
            "https://objects.test/facts-upload",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_fact_batch_rejects_index_checksum_before_ontology_download(httpx_mock):
    index_bytes, fragment_id, fragment_text = await indexed_fragment(httpx_mock)
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)

    with pytest.raises(ToolFailure, match="SOURCE_CHECKSUM_MISMATCH"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            "0" * 64,
            "https://objects.test/ontology",
            "1" * 64,
            [fact_fixture(fragment_id, fragment_text)],
            "https://objects.test/facts-upload",
        )

    assert not any(
        request.url == "https://objects.test/ontology"
        for request in httpx_mock.get_requests()
    )


@pytest.mark.asyncio
async def test_fact_batch_rejects_fragment_without_source(httpx_mock, tmp_path):
    fragment_text = "Exact source evidence"
    index_bytes, fragment_id = _document_index_bytes(
        tmp_path,
        fragment_text,
        include_source=False,
    )

    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology-upload")
    await validate_ontology_impl(
        ontology_fixture(),
        "https://objects.test/ontology-upload",
    )
    ontology_bytes = httpx_mock.get_requests()[-1].content
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure, match="FACT_SOURCE_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact_fixture(fragment_id, fragment_text)],
            "https://objects.test/facts-upload",
        )

    assert not any(
        request.url == "https://objects.test/facts-upload"
        for request in httpx_mock.get_requests()
    )


@pytest.mark.asyncio
async def test_fact_batch_rejects_non_text_fragment(httpx_mock, tmp_path):
    fragment_text = "Exact source evidence"
    index_bytes, fragment_id = _document_index_bytes(
        tmp_path,
        fragment_text.encode(),
        include_source=True,
    )
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology-upload")
    await validate_ontology_impl(
        ontology_fixture(),
        "https://objects.test/ontology-upload",
    )
    ontology_bytes = httpx_mock.get_requests()[-1].content
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure, match="DOCUMENT_INDEX_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact_fixture(fragment_id, fragment_text)],
            "https://objects.test/facts-upload",
        )

    assert not any(
        request.url == "https://objects.test/facts-upload"
        for request in httpx_mock.get_requests()
    )
