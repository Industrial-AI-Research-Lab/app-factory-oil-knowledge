from __future__ import annotations

import hashlib
import json

import pytest
from fastmcp import Client
from knowledge_test_support import (
    fact_fixture,
    indexed_fragment,
    ontology_fixture,
    sha256,
    valid_facts,
)

from app.errors import ToolFailure
from app.facts import validate_fact_batch_impl
from app.ontology.validation import validate_ontology_impl
from app.server import mcp


async def approved_inputs(httpx_mock):
    index_bytes, fragment_id, fragment_text = await indexed_fragment(httpx_mock)
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology-upload")
    await validate_ontology_impl(
        ontology_fixture(),
        "https://objects.test/ontology-upload",
    )
    return (
        index_bytes,
        httpx_mock.get_requests()[-1].content,
        fragment_id,
        fragment_text,
    )


@pytest.mark.asyncio
async def test_fact_batch_rejects_evidence_absent_from_fragment(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, _ = await approved_inputs(httpx_mock)
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure, match="FACT_EVIDENCE_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact_fixture(fragment_id, "not present")],
            "https://objects.test/facts-upload",
        )

    assert not any(
        request.url == "https://objects.test/facts-upload"
        for request in httpx_mock.get_requests()
    )



@pytest.mark.asyncio
async def test_evidence_rejection_names_the_fact_and_its_text(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, _ = await approved_inputs(httpx_mock)
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure) as caught:
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            [fact_fixture(fragment_id, "stitched sentences")],
            "https://objects.test/facts-upload",
        )

    assert "recipe:sample-a" in caught.value.public_message
    assert "stitched sentences" in caught.value.public_message

@pytest.mark.asyncio
async def test_validate_fact_batch_uploads_canonical_wrapper(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/facts-upload")

    result = await validate_fact_batch_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        valid_facts(fragment_id, fragment_text),
        "https://objects.test/facts-upload",
    )

    uploaded = httpx_mock.get_requests()[-1].content
    parsed = json.loads(uploaded)
    assert [fact["kind"] for fact in parsed["facts"]] == [
        "entity",
        "entity",
        "observation",
        "relation",
    ]
    assert parsed["index_sha256"] == sha256(index_bytes)
    assert parsed["ontology_sha256"] == sha256(ontology_bytes)
    assert result.fact_count == 4
    assert result.sha256 == hashlib.sha256(uploaded).hexdigest()
    assert (
        uploaded
        == json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


@pytest.mark.asyncio
async def test_fact_tool_accepts_all_advertised_fact_variants(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/facts-upload")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "validate_fact_batch",
            {
                "index_url": "https://objects.test/index",
                "expected_index_sha256": sha256(index_bytes),
                "ontology_url": "https://objects.test/ontology",
                "expected_ontology_sha256": sha256(ontology_bytes),
                "facts": valid_facts(fragment_id, fragment_text),
                "upload_url": "https://objects.test/facts-upload",
            },
        )

    assert result.data["status"] == "ok"
    assert result.data["fact_count"] == 4
    assert (
        json.loads(httpx_mock.get_requests()[-1].content)["facts"][0]["kind"]
        == "entity"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("facts", [None, []])
async def test_fact_batch_rejects_missing_or_empty_input_before_download(
    httpx_mock, facts
):
    with pytest.raises(ToolFailure, match="FACT_SCHEMA_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            "0" * 64,
            "https://objects.test/ontology",
            "1" * 64,
            facts,
            "https://objects.test/facts-upload",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_fact_batch_rejects_duplicate_fact_id_before_download(httpx_mock):
    fact = fact_fixture("0" * 64, "evidence")

    with pytest.raises(ToolFailure, match="FACT_ID_DUPLICATE"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            "0" * 64,
            "https://objects.test/ontology",
            "1" * 64,
            [fact, fact],
            "https://objects.test/facts-upload",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_fact_batch_rejects_same_batch_endpoint_type_conflict(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    facts = valid_facts(fragment_id, fragment_text)
    facts[2]["target_type_id"] = "recipe"
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure, match="FACT_ENDPOINT_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            facts,
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("value", float("nan")), ("value", True), ("unit", None)],
)
async def test_fact_batch_rejects_invalid_measurement(httpx_mock, field, value):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    facts = valid_facts(fragment_id, fragment_text)
    facts[3][field] = value
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure, match="FACT_MEASUREMENT_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            facts,
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
async def test_fact_batch_accepts_large_finite_integer(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    facts = valid_facts(fragment_id, fragment_text)
    facts[3]["value"] = 10**400
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/facts-upload")

    result = await validate_fact_batch_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        facts,
        "https://objects.test/facts-upload",
    )

    assert result.fact_count == 4


@pytest.mark.asyncio
async def test_fact_batch_rejects_fragment_owned_by_another_source(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    facts = [fact_fixture(fragment_id, fragment_text)]
    facts[0]["evidence"]["source_id"] = "other"
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)

    with pytest.raises(ToolFailure, match="FACT_SOURCE_INVALID"):
        await validate_fact_batch_impl(
            "https://objects.test/index",
            sha256(index_bytes),
            "https://objects.test/ontology",
            sha256(ontology_bytes),
            facts,
            "https://objects.test/facts-upload",
        )


@pytest.mark.asyncio
async def test_fact_batch_preserves_cross_batch_endpoint_reference(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    relation = valid_facts(fragment_id, fragment_text)[2]
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/facts-upload")

    await validate_fact_batch_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        [relation],
        "https://objects.test/facts-upload",
    )

    uploaded = json.loads(httpx_mock.get_requests()[-1].content)
    assert uploaded["facts"][0]["source_entity_id"] == "recipe:sample-a"
    assert uploaded["facts"][0]["target_type_id"] == "component"


@pytest.mark.asyncio
async def test_fact_batch_preserves_exact_decomposed_unicode_evidence(httpx_mock):
    exact_text = "Cafe\u0301 source evidence"
    index_bytes, fragment_id, fragment_text = await indexed_fragment(
        httpx_mock,
        exact_text.encode("utf-8"),
    )
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology-upload")
    await validate_ontology_impl(
        ontology_fixture(),
        "https://objects.test/ontology-upload",
    )
    ontology_bytes = httpx_mock.get_requests()[-1].content
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/facts-upload")

    result = await validate_fact_batch_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        [fact_fixture(fragment_id, fragment_text)],
        "https://objects.test/facts-upload",
    )

    uploaded = json.loads(httpx_mock.get_requests()[-1].content)
    assert result.fact_count == 1
    assert uploaded["facts"][0]["evidence"]["text"] == exact_text


@pytest.mark.asyncio
async def test_fact_batch_preserves_explicit_unicode_unit(httpx_mock):
    index_bytes, ontology_bytes, fragment_id, fragment_text = await approved_inputs(
        httpx_mock
    )
    facts = valid_facts(fragment_id, fragment_text)
    exact_unit = "mN/me\u0301"
    facts[3]["unit"] = exact_unit
    httpx_mock.add_response(url="https://objects.test/index", content=index_bytes)
    httpx_mock.add_response(url="https://objects.test/ontology", content=ontology_bytes)
    httpx_mock.add_response(method="PUT", url="https://objects.test/facts-upload")

    await validate_fact_batch_impl(
        "https://objects.test/index",
        sha256(index_bytes),
        "https://objects.test/ontology",
        sha256(ontology_bytes),
        facts,
        "https://objects.test/facts-upload",
    )

    uploaded = json.loads(httpx_mock.get_requests()[-1].content)
    assert uploaded["facts"][2]["unit"] == exact_unit
