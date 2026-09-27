from __future__ import annotations

import hashlib
import json

import pytest
from app.errors import ToolFailure
from app.ontology.validation import validate_ontology_impl
from knowledge_test_support import ontology_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("ontology", [None, {}])
async def test_ontology_rejects_missing_or_empty_object(httpx_mock, ontology):
    with pytest.raises(ToolFailure, match="ONTOLOGY_SCHEMA_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_validate_ontology_uploads_canonical_bytes(httpx_mock):
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology")

    result = await validate_ontology_impl(
        ontology_fixture(),
        "https://objects.test/ontology",
    )

    uploaded = httpx_mock.get_request().content
    expected = ontology_fixture()
    expected["entity_types"] = [
        expected["entity_types"][1],
        expected["entity_types"][0],
    ]
    expected["coverage"][0]["schema_element_ids"] = [
        "interfacial_tension",
        "interfacial_tension.temperature_c",
        "recipe",
    ]
    assert (
        uploaded
        == json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    assert result.sha256 == hashlib.sha256(uploaded).hexdigest()
    assert result.ontology_id == "surfactant-knowledge"
    assert result.version == "1.0.0"


@pytest.mark.asyncio
async def test_ontology_rejects_relation_to_missing_type(httpx_mock):
    ontology = ontology_fixture()
    ontology["relation_types"][0]["target_type"] = "missing"

    with pytest.raises(ToolFailure, match="ONTOLOGY_REFERENCE_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_rejects_duplicate_schema_element_id(httpx_mock):
    ontology = ontology_fixture()
    ontology["entity_types"][1]["properties"][0]["id"] = "recipe.name"

    with pytest.raises(ToolFailure, match="ONTOLOGY_ID_DUPLICATE"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_rejects_stale_base_version(httpx_mock):
    ontology = ontology_fixture()
    ontology["base_version"] = "0.9.0"

    with pytest.raises(ToolFailure, match="ONTOLOGY_VERSION_CONFLICT"):
        await validate_ontology_impl(
            ontology,
            "https://objects.test/ontology",
            expected_base_version="0.8.0",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_rejects_unknown_coverage_reference(httpx_mock):
    ontology = ontology_fixture()
    ontology["coverage"][0]["schema_element_ids"].append("missing")

    with pytest.raises(ToolFailure, match="ONTOLOGY_REFERENCE_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_rejects_empty_coverage_row(httpx_mock):
    ontology = ontology_fixture()
    ontology["coverage"][0]["schema_element_ids"] = []

    with pytest.raises(ToolFailure, match="ONTOLOGY_SCHEMA_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_rejects_blank_coverage_reference(httpx_mock):
    ontology = ontology_fixture()
    ontology["coverage"][0]["schema_element_ids"] = [" "]

    with pytest.raises(ToolFailure, match="ONTOLOGY_SCHEMA_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["entity_types", "coverage"])
async def test_ontology_rejects_empty_required_collection(httpx_mock, field):
    ontology = ontology_fixture()
    ontology[field] = []

    with pytest.raises(ToolFailure, match="ONTOLOGY_SCHEMA_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version", ["latest", "1.0", "v1.0.0", "01.0.0", "1.0.0-beta", "1.0.0\n"]
)
async def test_ontology_rejects_non_semantic_version(httpx_mock, version):
    ontology = ontology_fixture()
    ontology["version"] = version

    with pytest.raises(ToolFailure, match="ONTOLOGY_SCHEMA_INVALID"):
        await validate_ontology_impl(ontology, "https://objects.test/ontology")

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_rejects_non_semantic_base_version(httpx_mock):
    ontology = ontology_fixture()
    ontology["base_version"] = "latest"

    with pytest.raises(ToolFailure, match="ONTOLOGY_SCHEMA_INVALID"):
        await validate_ontology_impl(
            ontology,
            "https://objects.test/ontology",
            expected_base_version="latest",
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_base_version", ["latest", "1.0", ""])
async def test_ontology_rejects_non_semantic_expected_base_version(
    httpx_mock, expected_base_version
):
    ontology = ontology_fixture()
    ontology["version"] = "1.1.0"
    ontology["base_version"] = "1.0.0"

    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await validate_ontology_impl(
            ontology,
            "https://objects.test/ontology",
            expected_base_version=expected_base_version,
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_ontology_accepts_semantic_revision_of_approved_version(httpx_mock):
    httpx_mock.add_response(method="PUT", url="https://objects.test/ontology")
    ontology = ontology_fixture()
    ontology["version"] = "1.10.0"
    ontology["base_version"] = "1.9.0"

    result = await validate_ontology_impl(
        ontology,
        "https://objects.test/ontology",
        expected_base_version="1.9.0",
    )

    assert result.version == "1.10.0"
    assert json.loads(httpx_mock.get_request().content)["base_version"] == "1.9.0"
