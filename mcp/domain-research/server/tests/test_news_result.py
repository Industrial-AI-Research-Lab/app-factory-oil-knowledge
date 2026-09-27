import hashlib
import json
from copy import deepcopy

import pytest
from app.errors import ToolFailure
from news_test_support import (
    SOURCE_A,
    SOURCE_B,
    NewsObjectTransport,
    bundle_bytes,
    reviewed_decisions,
)


@pytest.mark.asyncio
async def test_finalize_counts_publications_and_events_separately():
    transport = NewsObjectTransport()
    async with transport.client:
        result = await transport.finalize()

    uploaded = transport.uploaded_bytes()
    stored = json.loads(uploaded)
    assert stored["statistics"]["publication_count"] == 2
    assert stored["statistics"]["event_count"] == 1
    assert result.sha256 == hashlib.sha256(uploaded).hexdigest()


@pytest.mark.asyncio
async def test_finalize_rejects_decision_for_source_outside_saved_bundle():
    decisions = reviewed_decisions()
    decisions["publications"][0]["source_id"] = "f" * 64
    transport = NewsObjectTransport()
    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_SOURCE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_excerpt_absent_from_saved_source():
    decisions = reviewed_decisions()
    decisions["publications"][0]["evidence"] = [{"quote": "invented claim"}]
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_EVIDENCE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_included_publication_without_excerpt():
    decisions = reviewed_decisions()
    decisions["publications"][0]["evidence"] = []
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_EVIDENCE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["publication", "event"])
async def test_finalize_requires_limitation_for_uncategorized_included_content(target):
    decisions = reviewed_decisions()
    item = (
        decisions["publications"][0]
        if target == "publication"
        else decisions["events"][0]
    )
    item["categories"] = []
    item["limitations"] = []
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_SCHEMA_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decisions", [None, [], {}, "invalid"])
async def test_finalize_rejects_malformed_decisions_with_stable_error(decisions):
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_SCHEMA_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_finalize_rejects_duplicate_publication_decision_id():
    decisions = reviewed_decisions()
    decisions["publications"][1] = deepcopy(decisions["publications"][0])
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_ID_DUPLICATE"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_finalize_rejects_duplicate_event_id():
    decisions = reviewed_decisions()
    decisions["events"].append(deepcopy(decisions["events"][0]))
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_ID_DUPLICATE"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_finalize_rejects_event_backed_by_excluded_publication():
    decisions = reviewed_decisions()
    decisions["publications"][1]["decision"] = "exclude"
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_EVENT_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_different_events_for_exact_reposts():
    decisions = reviewed_decisions()
    first = decisions["events"][0]
    first["source_ids"] = [SOURCE_A]
    first["evidence"] = [first["evidence"][0]]
    second = deepcopy(first)
    second["event_id"] = "event-2"
    second["source_ids"] = [SOURCE_B]
    second["evidence"] = [{"source_id": SOURCE_B, "quote": "oil recovery"}]
    decisions["events"].append(second)
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_DUPLICATE_EVENT"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_event_excerpt_absent_from_saved_source():
    decisions = reviewed_decisions()
    decisions["events"][0]["evidence"][0]["quote"] = "invented event evidence"
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_EVIDENCE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_event_excerpt_rejection_names_the_event_source_and_quote():
    decisions = reviewed_decisions()
    evidence = decisions["events"][0]["evidence"][0]
    evidence["quote"] = "invented event evidence"
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    message = caught.value.public_message
    assert "event-1" in message
    assert evidence["source_id"][:12] in message
    assert "invented event evidence" in message


@pytest.mark.asyncio
async def test_finalize_rejects_included_publication_without_event():
    decisions = reviewed_decisions()
    decisions["events"][0]["source_ids"] = [SOURCE_A]
    decisions["events"][0]["evidence"] = [decisions["events"][0]["evidence"][0]]
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_EVENT_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_requires_one_decision_for_every_saved_source():
    decisions = reviewed_decisions()
    decisions["publications"] = [decisions["publications"][0]]
    decisions["events"][0]["source_ids"] = [SOURCE_A]
    decisions["events"][0]["evidence"] = [decisions["events"][0]["evidence"][0]]
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert caught.value.code == "NEWS_SOURCE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_critique_target_outside_final_result():
    critique = {
        "correction_cycle": 1,
        "notes": [
            {
                "id": "note-1",
                "source_ids": ["f" * 64],
                "event_ids": ["event-1"],
                "issue": "Source requires another review",
                "action": "Keep the uncertainty visible",
                "resolution": "unresolved",
                "resolution_reason": "Source is not available",
            }
        ],
    }
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(critique=critique)

    assert caught.value.code == "NEWS_CRITIQUE_INVALID"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_finalize_rejects_malformed_bundle_checksum_before_download():
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(
                expected_bundle_sha256="z" * 64,
                bundle_receipt="v1." + "A" * 43,
            )

    assert caught.value.code == "INPUT_INVALID"
    assert transport.requests == []


@pytest.mark.asyncio
async def test_finalize_canonical_bytes_do_not_depend_on_input_order():
    reordered = reviewed_decisions()
    reordered["publications"].reverse()
    reordered["events"][0]["source_ids"].reverse()
    reordered["events"][0]["evidence"].reverse()
    first = NewsObjectTransport()
    second = NewsObjectTransport()

    async with first.client, second.client:
        expected = await first.finalize()
        actual = await second.finalize(decisions=reordered)

    assert actual.sha256 == expected.sha256
    assert second.uploaded_bytes() == first.uploaded_bytes()


@pytest.mark.asyncio
async def test_finalize_rejects_included_source_outside_approved_period():
    bundle = json.loads(bundle_bytes())
    bundle["sources"][0]["published_date"] = "2026-10-01"
    transport = NewsObjectTransport(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    )

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize()

    assert caught.value.code == "NEWS_BRIEF_MISMATCH"
    assert [request.method for request in transport.requests] == ["GET"]


@pytest.mark.asyncio
async def test_rejected_excerpt_names_its_source_and_quote():
    decisions = reviewed_decisions()
    decisions["publications"][0]["evidence"] = [{"quote": "invented claim"}]
    transport = NewsObjectTransport()

    async with transport.client:
        with pytest.raises(ToolFailure) as caught:
            await transport.finalize(decisions=decisions)

    assert "invented claim" in caught.value.public_message
    assert decisions["publications"][0]["source_id"][:12] in caught.value.public_message
