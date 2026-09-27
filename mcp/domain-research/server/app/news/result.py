from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

import httpx

from ..web_source_transport import MAX_BUNDLE_BYTES, upload_bundle
from . import FinalizeNewsResult
from .bundle import download_verified_bundle, index_sources, load_bundle
from .dates import publication_dates
from .rules import validate_news_relationships
from .validation import validate_news_inputs

__all__ = [
    "assemble_news_result",
    "canonical_values",
    "download_verified_bundle",
    "finalize_news_result_impl",
    "index_sources",
    "load_bundle",
    "upload_news_result",
]


async def finalize_news_result_impl(
    *,
    brief: object,
    decisions: object,
    critique: object,
    source_bundle_url: str,
    expected_bundle_sha256: str,
    bundle_receipt: str,
    upload_url: str,
    client: httpx.AsyncClient,
    bundle_receipt_key: str | bytes,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
    timeout_seconds: float = 120,
    request_id: str = "-",
) -> FinalizeNewsResult:
    parsed = validate_news_inputs(brief, decisions, critique)
    brief_value, decisions_value, critique_value = canonical_values(*parsed)
    payload = await download_verified_bundle(
        client,
        source_bundle_url,
        expected_bundle_sha256,
        bundle_receipt,
        bundle_receipt_key=bundle_receipt_key,
        max_bundle_bytes=max_bundle_bytes,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
    )
    sources, failures = index_sources(load_bundle(payload), brief_value["query"])
    value = assemble_news_result(
        brief_value,
        decisions_value,
        critique_value,
        sources,
        failures,
        expected_bundle_sha256,
        bundle_receipt,
        bundle_receipt_key,
    )
    return await upload_news_result(
        value,
        upload_url,
        client,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
    )


def canonical_values(parsed_brief, parsed_decisions, parsed_critique):
    brief_value = parsed_brief.model_dump(mode="json")
    decisions_value = parsed_decisions.model_dump(mode="json")
    critique_value = parsed_critique.model_dump(mode="json")
    _canonicalize_lists(brief_value, decisions_value, critique_value)
    return brief_value, decisions_value, critique_value


def assemble_news_result(
    brief_value,
    decisions_value,
    critique_value,
    sources,
    failures,
    expected_bundle_sha256,
    bundle_receipt,
    bundle_receipt_key,
    *,
    verified_quotes: set[tuple[str, str]] | None = None,
) -> dict:
    dates = publication_dates(
        sources, critique_value, decisions_value, bundle_receipt_key
    )
    validate_news_relationships(
        brief_value,
        decisions_value,
        critique_value,
        sources,
        dates,
        verified_quotes=verified_quotes,
    )
    publications = _publications(
        decisions_value["publications"], decisions_value["events"], sources, dates
    )
    events = sorted(decisions_value["events"], key=lambda item: item["event_id"])
    limitations = [
        note["issue"]
        for note in critique_value["notes"]
        if note["resolution"] == "unresolved"
    ]
    if not sources:
        limitations.append("В сохранённом snapshot нет извлечённых источников.")
    return {
        "schema_version": 1,
        "kind": "news-result",
        "brief": brief_value,
        "source_bundle": {
            "sha256": expected_bundle_sha256.lower(),
            "receipt": bundle_receipt,
            "failures": failures,
        },
        "publications": publications,
        "events": events,
        "critique": critique_value,
        "limitations": limitations,
        "statistics": _statistics(publications, events),
    }


async def upload_news_result(
    value: dict, upload_url: str, client, *, timeout_seconds: float, request_id: str
) -> FinalizeNewsResult:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    await upload_bundle(
        client,
        upload_url,
        payload,
        timeout_seconds=timeout_seconds,
        request_id=request_id,
    )
    statistics = value["statistics"]
    return FinalizeNewsResult(
        schema_version=value["schema_version"],
        source_bundle_sha256=value["source_bundle"]["sha256"],
        publication_count=statistics["publication_count"],
        event_count=statistics["event_count"],
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        publication_dates=[
            {
                key: publication[key]
                for key in (
                    "source_id",
                    "published_date",
                    "search_published_date",
                    "selected_published_date",
                    "date_basis",
                    "date_limitation",
                )
            }
            for publication in value["publications"]
        ],
    )


def _canonicalize_lists(brief: dict, decisions: dict, critique: dict) -> None:
    brief["include_domains"].sort(key=str.casefold)
    brief["exclude_domains"].sort(key=str.casefold)
    brief["formats"].sort()
    for publication in decisions["publications"]:
        publication["categories"].sort()
        publication["limitations"].sort()
        publication["evidence"].sort(key=lambda item: item["quote"])
    for event in decisions["events"]:
        event["categories"].sort()
        event["limitations"].sort()
        event["source_ids"].sort()
        event["evidence"].sort(key=lambda item: (item["source_id"], item["quote"]))
        event["companies"].sort(
            key=lambda item: (item["name"].casefold(), item["role"])
        )
    critique["notes"].sort(key=lambda item: item["id"])
    critique["date_assessments"].sort(key=lambda item: item["source_id"])
    for note in critique["notes"]:
        note["source_ids"].sort()
        note["event_ids"].sort()


def _publications(
    decisions: list[dict],
    events: list[dict],
    sources: dict[str, dict],
    dates: dict[str, dict],
) -> list[dict]:
    memberships: dict[str, list[str]] = defaultdict(list)
    for event in events:
        for source_id in event["source_ids"]:
            memberships[source_id].append(event["event_id"])
    publications = []
    for decision in decisions:
        source_id = decision["source_id"]
        source = sources[source_id]
        publications.append(
            decision
            | dates[source_id]
            | {
                "title": source["title"],
                "url": source["url"],
                "published_date": source["published_date"],
                "event_ids": sorted(memberships[source_id]),
                "limitations": sorted(
                    set(decision["limitations"])
                    | (
                        {dates[source_id]["date_limitation"]}
                        if dates[source_id]["date_limitation"]
                        else set()
                    )
                ),
            }
        )
    return sorted(publications, key=lambda item: item["source_id"])


def _statistics(
    publications: list[dict],
    events: list[dict],
) -> dict:
    included = [item for item in publications if item["decision"] == "include"]
    categories: dict[str, dict[str, int]] = defaultdict(
        lambda: {"publication_count": 0, "event_count": 0}
    )
    for publication in included:
        for category in publication["categories"]:
            categories[category]["publication_count"] += 1
    for event in events:
        for category in event["categories"]:
            categories[category]["event_count"] += 1
    return {
        "publication_count": len(included),
        "event_count": len(events),
        "decisions": dict(Counter(item["decision"] for item in publications)),
        "categories": dict(sorted(categories.items())),
        "evidence_levels": dict(Counter(item["evidence_level"] for item in events)),
    }
