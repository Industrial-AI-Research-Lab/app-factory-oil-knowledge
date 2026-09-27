from __future__ import annotations

import pytest
from app.collection.build import build_collection_impl
from app.collection.storage import build_database
from app.errors import ToolFailure
from app.fact_models import parse_facts
from app.facts import validate_fact_batch_impl
from app.ontology.validation import validate_ontology_impl
from document_test_support import build_single_document
from knowledge_test_support import ontology_fixture, sha256, valid_facts


async def _approved_artifacts(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"Sample A contains Component A at 12.4 mN/m and 75 C.",
        content_type="text/plain",
        filename="source.txt",
    )
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology-upload")
    await validate_ontology_impl(
        ontology_fixture(), "https://objects.test/ontology-upload"
    )
    ontology_bytes = httpx_mock.get_requests()[-1].content
    return index_bytes, ontology_bytes


async def _fact_batch(httpx_mock, index_bytes, ontology_bytes):
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        index_path = Path(directory) / "index.sqlite3"
        index_path.write_bytes(index_bytes)
        connection = sqlite3.connect(index_path)
        try:
            fragment_id, text = connection.execute(
                "SELECT fragment_id, text FROM fragments"
            ).fetchone()
        finally:
            connection.close()
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/batch-upload")
    await validate_fact_batch_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        valid_facts(fragment_id, text),
        "https://objects.test/batch-upload",
    )
    return httpx_mock.get_requests()[-1].content


@pytest.mark.asyncio
async def test_build_is_complete_and_logically_idempotent(httpx_mock):
    index_bytes, ontology_bytes = await _approved_artifacts(httpx_mock)
    batch_bytes = await _fact_batch(httpx_mock, index_bytes, ontology_bytes)

    def add_inputs(upload_url):
        httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
        httpx_mock.add_response(
            url="https://objects.test/ontology", content=ontology_bytes
        )
        httpx_mock.add_response(url="https://objects.test/batch", content=batch_bytes)
        httpx_mock.add_response(method="PUT", url=upload_url)

    add_inputs("https://objects.test/collection-one")
    first = await build_collection_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        [{"url": "https://objects.test/batch", "expected_sha256": sha256(batch_bytes)}],
        "https://objects.test/collection-one",
    )
    add_inputs("https://objects.test/collection-two")
    second = await build_collection_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        [{"url": "https://objects.test/batch", "expected_sha256": sha256(batch_bytes)}],
        "https://objects.test/collection-two",
    )

    assert first.counts == second.counts
    assert first.input_fingerprint == second.input_fingerprint
    assert first.counts == {
        "nodes": 2,
        "relations": 1,
        "observations": 1,
        "fragments": 1,
        "source_status": 1,
    }


@pytest.mark.asyncio
async def test_build_rejects_duplicate_fact_across_batches_before_upload(httpx_mock):
    index_bytes, ontology_bytes = await _approved_artifacts(httpx_mock)
    batch_bytes = await _fact_batch(httpx_mock, index_bytes, ontology_bytes)
    for url, payload in (
        ("https://objects.test/index", index_bytes),
        ("https://objects.test/ontology", ontology_bytes),
        ("https://objects.test/batch-one", batch_bytes),
        ("https://objects.test/batch-two", batch_bytes),
    ):
        httpx_mock.add_response(url=url, content=payload)

    with pytest.raises(ToolFailure, match="FACT_ID_DUPLICATE"):
        await build_collection_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [
                {
                    "url": "https://objects.test/batch-one",
                    "expected_sha256": sha256(batch_bytes),
                },
                {
                    "url": "https://objects.test/batch-two",
                    "expected_sha256": sha256(batch_bytes),
                },
            ],
            "https://objects.test/collection",
        )

    assert not any(
        request.url == "https://objects.test/collection"
        for request in httpx_mock.get_requests()
    )


@pytest.mark.asyncio
async def test_build_checks_batch_checksum_before_parsing(httpx_mock):
    index_bytes, ontology_bytes = await _approved_artifacts(httpx_mock)
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(url="https://objects.test/batch", content=b"not-json")

    with pytest.raises(ToolFailure, match="SOURCE_CHECKSUM_MISMATCH"):
        await build_collection_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [{"url": "https://objects.test/batch", "expected_sha256": "0" * 64}],
            "https://objects.test/collection",
        )


@pytest.mark.asyncio
async def test_database_build_rolls_back_schema_when_fact_insert_fails(
    httpx_mock, tmp_path
):
    import sqlite3

    index_bytes, _ = await _approved_artifacts(httpx_mock)
    index_path = tmp_path / "index.sqlite3"
    collection_path = tmp_path / "collection.sqlite3"
    index_path.write_bytes(index_bytes)
    connection = sqlite3.connect(index_path)
    try:
        fragment_id, text = connection.execute(
            "SELECT fragment_id, text FROM fragments"
        ).fetchone()
    finally:
        connection.close()
    facts = parse_facts(valid_facts(fragment_id, text))

    with pytest.raises(ToolFailure, match="COLLECTION_INVALID"):
        build_database(index_path, collection_path, facts + [facts[0]], {})

    connection = sqlite3.connect(collection_path)
    try:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            == []
        )
    finally:
        connection.close()
