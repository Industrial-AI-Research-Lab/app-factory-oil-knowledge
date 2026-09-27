import fastmcp
import pytest
from app.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_service_exposes_domain_research_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert mcp.name == "Domain Research MCP"
    assert {tool.name for tool in tools} == {
        "search_web",
        "extract_web_pages",
        "read_web_fragments",
        "finalize_news_result",
        "index_documents",
        "search_documents",
        "read_document_fragments",
        "validate_ontology",
        "read_ontology",
        "validate_fact_batch",
        "build_collection",
        "inspect_collection",
        "query_collection",
        "validate_calculator_spec",
        "discover_web_sources",
        "snapshot_web_sources",
        "read_web_snapshot",
        "sign_snapshot_quotes",
        "finalize_news_research",
    }


@pytest.mark.asyncio
async def test_news_finalizer_requires_snapshot_proof_and_upload_target():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "finalize_news_result")
    assert set(schema.inputSchema["required"]) >= {
        "brief",
        "decisions",
        "critique",
        "source_bundle_url",
        "expected_bundle_sha256",
        "bundle_receipt",
        "upload_url",
    }


@pytest.mark.asyncio
async def test_news_finalizer_publishes_nested_input_schema():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "finalize_news_result")
    definitions = schema.inputSchema.get("$defs", {})

    def resolve(value):
        reference = value.get("$ref")
        return definitions[reference.rsplit("/", 1)[-1]] if reference else value

    properties = schema.inputSchema["properties"]
    brief = resolve(properties["brief"])
    decisions = resolve(properties["decisions"])
    critique = resolve(properties["critique"])
    assert brief["properties"]["query"]["maxLength"] == 400
    assert set(decisions["properties"]) == {"publications", "events"}
    assert set(critique["properties"]) == {
        "correction_cycle",
        "notes",
        "date_assessments",
        "reviewed_events",
    }


@pytest.mark.asyncio
async def test_document_read_tools_require_index_checksum():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    by_name = {tool.name: tool for tool in tools}
    for name in ("search_documents", "read_document_fragments"):
        assert "expected_index_sha256" in by_name[name].inputSchema["required"]


@pytest.mark.asyncio
async def test_fact_validation_requires_approved_artifact_checksums():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "validate_fact_batch")
    assert set(schema.inputSchema["required"]) >= {
        "index_url",
        "expected_index_sha256",
        "ontology_url",
        "expected_ontology_sha256",
        "facts",
        "upload_url",
    }


@pytest.mark.asyncio
async def test_fact_tool_advertises_entity_relation_and_observation_shapes():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "validate_fact_batch")
    facts = schema.inputSchema["properties"]["facts"]
    array = next(option for option in facts["anyOf"] if option.get("type") == "array")
    variants = array["items"]["oneOf"]
    by_kind = {variant["properties"]["kind"]["const"]: variant for variant in variants}
    assert set(by_kind) == {"entity", "relation", "observation"}
    assert set(by_kind["entity"]["required"]) == {
        "kind",
        "id",
        "type_id",
        "properties",
        "evidence",
    }
    assert set(by_kind["relation"]["required"]) == {
        "kind",
        "id",
        "type_id",
        "source_entity_id",
        "source_type_id",
        "target_entity_id",
        "target_type_id",
        "evidence",
    }
    assert set(by_kind["observation"]["required"]) == {
        "kind",
        "id",
        "type_id",
        "subject_entity_id",
        "subject_type_id",
        "value",
        "unit",
        "conditions",
        "evidence",
    }
    for variant in variants:
        assert set(variant["properties"]["evidence"]["required"]) == {
            "source_id",
            "fragment_id",
            "text",
        }


@pytest.mark.asyncio
async def test_collection_build_requires_approved_artifact_checksums():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "build_collection")
    assert set(schema.inputSchema["required"]) >= {
        "index_url",
        "expected_index_sha256",
        "ontology_url",
        "expected_ontology_sha256",
        "fact_batches",
        "upload_url",
    }


@pytest.mark.asyncio
async def test_collection_build_advertises_exact_batch_reference_fields():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "build_collection")
    references = schema.inputSchema["properties"]["fact_batches"]
    item = references["items"]
    if "$ref" in item:
        item = schema.inputSchema["$defs"][item["$ref"].rsplit("/", 1)[-1]]
    assert set(item["required"]) == {"url", "expected_sha256"}
    assert set(item["properties"]) == {"url", "expected_sha256"}


@pytest.mark.asyncio
async def test_collection_build_advertises_sqlite_upload_content_type():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "build_collection")
    upload_url = schema.inputSchema["properties"]["upload_url"]
    assert "application/x-sqlite3" in upload_url["description"]


@pytest.mark.asyncio
async def test_collection_build_advertises_optional_entity_merge():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "build_collection")
    assert "entity_merge" in schema.inputSchema["properties"]
    assert "entity_merge" not in schema.inputSchema["required"]
    assert "merge_identical" in schema.inputSchema["properties"]["entity_merge"]["description"]


@pytest.mark.asyncio
async def test_fact_tool_returns_stable_error_for_null_input():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "validate_fact_batch",
            {
                "index_url": "https://objects.test/index",
                "expected_index_sha256": "0" * 64,
                "ontology_url": "https://objects.test/ontology",
                "expected_ontology_sha256": "1" * 64,
                "facts": None,
                "upload_url": "https://objects.test/facts-upload",
            },
            raise_on_error=False,
        )

    assert result.is_error is False
    assert result.data["error"]["code"] == "FACT_SCHEMA_INVALID"


@pytest.mark.asyncio
async def test_fact_tool_rejects_object_instead_of_fact_array():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "validate_fact_batch",
            {
                "index_url": "https://objects.test/index",
                "expected_index_sha256": "0" * 64,
                "ontology_url": "https://objects.test/ontology",
                "expected_ontology_sha256": "1" * 64,
                "facts": {"kind": "entity"},
                "upload_url": "https://objects.test/facts-upload",
            },
            raise_on_error=False,
        )

    assert result.is_error is True
    assert "facts" in result.content[0].text


@pytest.mark.asyncio
async def test_ontology_tool_returns_stable_error_for_null_input():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "validate_ontology",
            {
                "ontology": None,
                "upload_url": "https://objects.test/ontology-upload",
            },
            raise_on_error=False,
        )

    assert result.is_error is False
    assert result.data["error"]["code"] == "ONTOLOGY_SCHEMA_INVALID"


def test_container_disables_fastmcp_update_checks():
    assert fastmcp.settings.check_for_updates == "off"
