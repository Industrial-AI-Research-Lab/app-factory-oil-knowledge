import json
from copy import deepcopy

import pytest
from app.errors import ToolFailure
from news_test_support import (
    SOURCE_A,
    SOURCE_B,
    NewsObjectTransport,
    approved_brief,
    bundle_bytes,
    reviewed_decisions,
)


@pytest.mark.asyncio
async def test_finalize_rejects_non_utf8_source_bundle_with_stable_error():
    transport = NewsObjectTransport(b"\xff")

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize()

    assert caught.value.code == "SOURCE_BUNDLE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_deeply_nested_source_bundle_with_stable_error():
    transport = NewsObjectTransport(b"[" * 2_000 + b"]" * 2_000)

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize()

    assert caught.value.code == "SOURCE_BUNDLE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_bundle_checksum_mismatch_before_upload():
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(expected_bundle_sha256="f" * 64)

    assert caught.value.code == "SOURCE_CHECKSUM_MISMATCH"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_proof",
    [
        {"expected_bundle_sha256": ""},
        {"bundle_receipt": ""},
    ],
)
async def test_finalize_rejects_empty_snapshot_proof(invalid_proof):
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(**invalid_proof)

    assert caught.value.code == "INPUT_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_finalize_rejects_snapshot_from_another_brief_query():
    bundle = json.loads(bundle_bytes())
    bundle["query"] = "unrelated approved query"
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize()

    assert caught.value.code == "NEWS_BRIEF_MISMATCH"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_accepts_search_query_at_length_limit():
    query = "q" * 400
    brief = approved_brief()
    brief["query"] = query
    bundle = json.loads(bundle_bytes())
    bundle["query"] = query
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        result = await transport.finalize(brief=brief)

    assert result.publication_count == 2


@pytest.mark.asyncio
async def test_finalize_rejects_search_query_above_length_limit():
    brief = approved_brief()
    brief["query"] = "q" * 401
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(brief=brief)

    assert caught.value.code == "NEWS_SCHEMA_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("correction_cycle", [False, True])
async def test_finalize_rejects_boolean_correction_cycle(correction_cycle):
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(
                critique={"correction_cycle": correction_cycle, "notes": []}
            )

    assert caught.value.code == "NEWS_SCHEMA_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("include_domains", "exclude_domains"),
    [
        (["example.test"], []),
        (["ource.test"], []),
        (["bad["], []),
        ([], ["test"]),
        (["test"], ["source.test"]),
    ],
)
async def test_finalize_rejects_included_source_outside_domain_filters(
    include_domains,
    exclude_domains,
):
    brief = approved_brief()
    brief["include_domains"] = include_domains
    brief["exclude_domains"] = exclude_domains
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(brief=brief)

    assert caught.value.code == "NEWS_BRIEF_MISMATCH"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_accepts_included_source_from_subdomain():
    brief = approved_brief()
    brief["include_domains"] = ["test"]
    transport = NewsObjectTransport()

    async with transport.client:
        result = await transport.finalize(brief=brief)

    assert result.publication_count == 2


@pytest.mark.asyncio
async def test_finalize_rejects_identical_events_with_different_ids():
    decisions = reviewed_decisions()
    duplicate = deepcopy(decisions["events"][0])
    duplicate["event_id"] = "event-2"
    decisions["events"].append(duplicate)
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_DUPLICATE_EVENT"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_duplicate_category_before_counting_totals():
    decisions = reviewed_decisions()
    decisions["publications"][0]["categories"] = [
        "polymer-flooding",
        "polymer-flooding",
    ]
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_SCHEMA_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_finalize_keeps_similar_titles_as_distinct_events():
    bundle = json.loads(bundle_bytes())
    bundle["sources"][0].update(
        title="Polymer pilot starts at North Field",
        text="North field began a polymer injection pilot.",
    )
    bundle["sources"][1].update(
        title="Polymer pilot starts at South Field",
        text="South field completed a separate polymer pilot.",
    )
    decisions = reviewed_decisions()
    decisions["publications"][0]["evidence"] = [{"quote": "North field"}]
    decisions["publications"][1]["evidence"] = [{"quote": "South field"}]
    north = decisions["events"][0]
    north.update(
        description="North field began a pilot",
        source_ids=[SOURCE_A],
        evidence=[{"source_id": SOURCE_A, "quote": "North field"}],
        merge_reason="One source describes this event",
    )
    south = deepcopy(north)
    south.update(
        event_id="event-2",
        description="South field completed another pilot",
        source_ids=[SOURCE_B],
        evidence=[{"source_id": SOURCE_B, "quote": "South field"}],
    )
    decisions["events"] = [north, south]
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        result = await transport.finalize(decisions=decisions)

    stored = json.loads(transport.uploaded_bytes())
    assert result.event_count == 2
    assert {item["event_id"] for item in stored["events"]} == {"event-1", "event-2"}


@pytest.mark.asyncio
async def test_finalize_accepts_zero_sources_without_inventing_totals():
    bundle = json.loads(bundle_bytes())
    bundle["sources"] = []
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        result = await transport.finalize(decisions={"publications": [], "events": []})

    stored = json.loads(transport.uploaded_bytes())
    assert result.publication_count == 0
    assert result.event_count == 0
    assert stored["statistics"] == {
        "publication_count": 0,
        "event_count": 0,
        "decisions": {},
        "categories": {},
        "evidence_levels": {},
    }


@pytest.mark.asyncio
async def test_finalize_keeps_unresolved_reviewer_issue_as_limitation():
    critique = {
        "correction_cycle": 1,
        "notes": [
            {
                "id": "note-1",
                "source_ids": [SOURCE_A],
                "event_ids": ["event-1"],
                "issue": "The source does not state the injected concentration",
                "action": "Keep the concentration unknown",
                "resolution": "unresolved",
                "resolution_reason": "The saved text has no concentration value",
            }
        ],
    }
    transport = NewsObjectTransport()

    async with transport.client:
        await transport.finalize(critique=critique)

    stored = json.loads(transport.uploaded_bytes())
    assert stored["limitations"] == [
        "The source does not state the injected concentration"
    ]


@pytest.mark.asyncio
async def test_finalize_preserves_extraction_failures_from_source_bundle():
    bundle = json.loads(bundle_bytes())
    bundle["failures"] = [
        {"url": "https://failed.test/article", "code": "PROVIDER_FAILED"}
    ]
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        await transport.finalize()

    stored = json.loads(transport.uploaded_bytes())
    assert stored["source_bundle"]["failures"] == [
        {"url": "https://failed.test/article", "code": "PROVIDER_FAILED"}
    ]


@pytest.mark.asyncio
async def test_finalize_retains_excluded_publication_without_counting_it():
    bundle = json.loads(bundle_bytes())
    bundle["sources"][1]["text"] = "Surfactant was used only in drilling fluid."
    decisions = reviewed_decisions()
    decisions["publications"][1]["decision"] = "exclude"
    decisions["publications"][1]["reason"] = "The report covers drilling fluid"
    decisions["publications"][1]["categories"] = []
    decisions["publications"][1]["evidence"] = []
    decisions["events"][0]["source_ids"] = [SOURCE_A]
    decisions["events"][0]["evidence"] = [
        item
        for item in decisions["events"][0]["evidence"]
        if item["source_id"] == SOURCE_A
    ]
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        await transport.finalize(decisions=decisions)

    stored = json.loads(transport.uploaded_bytes())
    by_source = {item["source_id"]: item for item in stored["publications"]}
    assert by_source[SOURCE_B]["decision"] == "exclude"
    assert stored["statistics"]["publication_count"] == 1
    assert stored["statistics"]["decisions"] == {"exclude": 1, "include": 1}
