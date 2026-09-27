from __future__ import annotations

from collections import defaultdict

import httpx

from ..errors import ToolFailure
from .quote_match import matchable_quotes


def validate_news_relationships(
    brief: dict,
    decisions: dict,
    critique: dict,
    sources: dict[str, dict],
    dates: dict[str, dict],
    *,
    verified_quotes: set[tuple[str, str]] | None = None,
) -> None:
    """`verified_quotes` holds (source_id, quote) pairs already proved by a
    quote receipt against the raw text; they skip the normalised substring check."""
    verified = verified_quotes or set()
    decisions, sources = matchable_quotes(decisions, sources)
    publication_ids = {item["source_id"] for item in decisions["publications"]}
    if publication_ids != set(sources):
        raise ToolFailure(
            "NEWS_SOURCE_INVALID",
            "Every saved source requires one publication decision",
        )
    for publication in decisions["publications"]:
        source = sources[publication["source_id"]]
        if publication["decision"] == "include":
            _validate_included_source_period(
                brief, dates[publication["source_id"]]["selected_published_date"]
            )
            _validate_included_source_domains(brief, source)
            if not publication["evidence"]:
                raise ToolFailure(
                    "NEWS_EVIDENCE_INVALID",
                    "Included publications require a saved-source excerpt",
                )
        missing = next(
            (
                evidence.get("shown_quote", evidence["quote"])
                for evidence in publication["evidence"]
                if evidence["quote"] not in source["text"]
                and evidence["quote"] not in source["match_title"]
                and (publication["source_id"], evidence["shown_quote"]) not in verified
            ),
            None,
        )
        if missing is not None:
            raise ToolFailure(
                "NEWS_EVIDENCE_INVALID",
                "Publication excerpt is absent from the saved source "
                f"{publication['source_id'][:12]}: {missing[:80]!r}. "
                "Quote text returned by read_web_fragments, not search snippets",
            )
    _validate_duplicate_events(decisions["events"])
    _validate_event_sources(decisions)
    _validate_event_evidence(decisions["events"], sources, verified)
    _validate_exact_reposts(decisions, sources)
    _validate_critique(
        critique,
        set(sources),
        {event["event_id"] for event in decisions["events"]},
    )


def _validate_duplicate_events(events: list[dict]) -> None:
    contents = []
    for event in events:
        content = {key: value for key, value in event.items() if key != "event_id"}
        if content in contents:
            raise ToolFailure(
                "NEWS_DUPLICATE_EVENT",
                "Identical event content cannot use multiple IDs",
            )
        contents.append(content)


def _validate_included_source_period(brief: dict, published: str | None) -> None:
    start = brief["start_date"]
    end = brief["end_date"]
    if (start or end) and (
        published is None
        or (start is not None and published < start)
        or (end is not None and published > end)
    ):
        raise ToolFailure(
            "NEWS_BRIEF_MISMATCH",
            "Included source is outside the approved publication period",
        )


def _validate_included_source_domains(brief: dict, source: dict) -> None:
    host = httpx.URL(source["url"]).host
    included = brief["include_domains"]
    excluded = brief["exclude_domains"]
    if (
        included and not any(_domain_matches(host, value) for value in included)
    ) or any(_domain_matches(host, value) for value in excluded):
        raise ToolFailure(
            "NEWS_BRIEF_MISMATCH",
            "Included source is outside the approved domain filters",
        )


def _domain_matches(host: str, value: str) -> bool:
    domain = httpx.URL(f"https://{value}").host
    return host == domain or host.endswith(f".{domain}")


def _validate_event_sources(decisions: dict) -> None:
    included = {
        publication["source_id"]
        for publication in decisions["publications"]
        if publication["decision"] == "include"
    }
    referenced = {
        source_id for event in decisions["events"] for source_id in event["source_ids"]
    }
    if not referenced.issubset(included):
        raise ToolFailure(
            "NEWS_EVENT_INVALID",
            "Events may reference only included publications",
        )
    if referenced != included:
        raise ToolFailure(
            "NEWS_EVENT_INVALID",
            "Every included publication must support an event",
        )


def _validate_event_evidence(
    events: list[dict],
    sources: dict[str, dict],
    verified: set[tuple[str, str]],
) -> None:
    for event in events:
        evidence_ids = [item["source_id"] for item in event["evidence"]]
        if set(evidence_ids) != set(event["source_ids"]) or len(evidence_ids) != len(
            set(evidence_ids)
        ):
            raise ToolFailure(
                "NEWS_EVIDENCE_INVALID",
                "Every event source requires one excerpt",
            )
        if any(
            item["source_id"] not in event["source_ids"]
            for item in _attribute_citations(event)
        ):
            raise ToolFailure(
                "NEWS_EVIDENCE_INVALID",
                "Company and adoption excerpts must cite an event source",
            )
        missing = next(
            (
                item
                for item in (*event["evidence"], *_attribute_citations(event))
                if item["source_id"] not in sources
                or (
                    item["quote"] not in sources[item["source_id"]]["text"]
                    and (item["source_id"], item["shown_quote"]) not in verified
                )
            ),
            None,
        )
        if missing is not None:
            raise ToolFailure(
                "NEWS_EVIDENCE_INVALID",
                f"Event {event['event_id']} excerpt is absent from the saved source "
                f"{missing['source_id'][:12]}: "
                f"{missing.get('shown_quote', missing['quote'])[:80]!r}. "
                "Quote text returned by read_web_fragments, not search snippets",
            )


def _attribute_citations(event: dict) -> list[dict]:
    adoption = event["adoption"]
    return [*event["companies"], *([adoption] if adoption is not None else [])]


def _validate_exact_reposts(decisions: dict, sources: dict[str, dict]) -> None:
    included = {
        publication["source_id"]
        for publication in decisions["publications"]
        if publication["decision"] == "include"
    }
    memberships: dict[str, list[str]] = defaultdict(list)
    for event in decisions["events"]:
        for source_id in event["source_ids"]:
            memberships[source_id].append(event["event_id"])
    content_groups: dict[str, list[str]] = defaultdict(list)
    for source in sources.values():
        content_groups[source["text"]].append(source["source_id"])
    for source_ids in content_groups.values():
        compared = [source_id for source_id in source_ids if source_id in included]
        if len(compared) > 1 and any(
            sorted(memberships[source_id]) != sorted(memberships[compared[0]])
            for source_id in compared[1:]
        ):
            raise ToolFailure(
                "NEWS_DUPLICATE_EVENT",
                "Exact reposts must use the same event grouping",
            )


def _validate_critique(
    critique: dict,
    source_ids: set[str],
    event_ids: set[str],
) -> None:
    reviewed = critique.get("reviewed_events", [])
    reviewed_ids = {event["event_id"] for event in reviewed}
    if any(not set(event["source_ids"]).issubset(source_ids) for event in reviewed):
        raise ToolFailure(
            "NEWS_CRITIQUE_INVALID", "Reviewed events reference unknown sources"
        )
    if any(
        not set(note["source_ids"]).issubset(source_ids)
        or not set(note["event_ids"]).issubset(event_ids | reviewed_ids)
        or (
            not set(note["event_ids"]).issubset(event_ids)
            and (note["resolution"] != "resolved" or critique["correction_cycle"] != 1)
        )
        for note in critique["notes"]
    ):
        raise ToolFailure(
            "NEWS_CRITIQUE_INVALID",
            "Critique references must belong to reviewed events; removed events require a resolved correction",
        )
