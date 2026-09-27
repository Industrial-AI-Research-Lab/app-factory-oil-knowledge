import logging
from collections import Counter, defaultdict

from command_io import CommandError, digest
from news_analysis import validate_analysis, validate_critique
from news_inputs import object_value
from news_storage import encoded, read_result, store_result

LOG = logging.getLogger(__name__)


def statistics(publications, events, candidate_count, archive_count):
    included = [row for row in publications if row["decision"] == "include"]
    by_category = defaultdict(lambda: {"publication_count": 0, "event_count": 0})
    for row in included:
        for category in row["categories"]:
            by_category[category]["publication_count"] += 1
    for event in events:
        for category in event["categories"]:
            by_category[category]["event_count"] += 1
    return {
        "publication_count": len(included),
        "event_count": len(events),
        "candidate_count": candidate_count,
        "archive_publication_count": archive_count,
        "decisions": dict(Counter(row["decision"] for row in publications)),
        "categories": dict(by_category),
        "evidence_levels": dict(Counter(event["evidence_level"] for event in events)),
        "source_count": len({(row["source"], row["url"]) for row in included}),
        "publisher_count": len({row["source"] for row in included}),
        "uncategorized_publication_count": sum(
            not row["categories"] for row in included
        ),
        "uncategorized_event_count": sum(not event["categories"] for event in events),
    }


def finalize(request, source_records, taxonomy, provenance, results):
    search = read_result(
        request.get("candidates_ref"), request, results, provenance, "candidates"
    )
    brief = read_result(search["brief_ref"], request, results, provenance, "brief")
    selected = {record["publication_id"] for record in search["candidates"]}
    records = {record["publication_id"]: record for record in source_records}
    analysis = object_value(request.get("analysis"), "analysis")
    initial = object_value(request.get("initial_analysis"), "initial_analysis")
    decisions, memberships = validate_analysis(analysis, selected, records, taxonomy)
    critique = request.get("critique")
    validate_critique(critique, initial, analysis, records)
    for group in search["exact_duplicate_groups"]:
        included = [
            identifier
            for identifier in group["publication_ids"]
            if memberships.get(identifier)
        ]
        if included and any(
            memberships[identifier] != memberships[included[0]]
            for identifier in included
        ):
            raise CommandError(
                "DUPLICATE_EVENT",
                "Exact duplicate articles have inconsistent event mappings",
            )
    publications = []
    for audit in search["audit"]:
        identifier = audit["publication_id"]
        source = records[identifier]
        row = decisions.get(identifier) or {
            "publication_id": identifier,
            "decision": audit["status"],
            "reason": audit["reason"],
            "categories": [],
            "evidence": [],
            "limitations": [],
        }
        publications.append(
            row
            | {
                "published_date": source["published_date"],
                "title": source["title"],
                "source": source.get("source"),
                "url": source.get("url"),
                "event_ids": sorted(memberships.get(identifier, set())),
            }
        )
    sources = {}
    for row in publications:
        if row["decision"] == "include":
            key = row["source"], row["url"]
            entry = sources.setdefault(
                key, {"source": row["source"], "url": row["url"], "publication_ids": []}
            )
            entry["publication_ids"].append(row["publication_id"])
    value = {
        "kind": "news-result",
        "project_id": request["project_id"],
        "run_id": request["run_id"],
        "inputs": provenance,
        "brief": brief,
        "brief_ref": search["brief_ref"],
        "candidates_ref": request["candidates_ref"],
        "approval": search["approval"],
        "terminology_result": search["terminology_result"],
        "publications": publications,
        "events": analysis["events"],
        "exact_duplicate_groups": search["exact_duplicate_groups"],
        "sources": list(sources.values()),
        "critique": critique,
        "initial_analysis": initial,
        "analysis": analysis,
        "initial_analysis_sha256": digest(encoded(initial)),
        "analysis_sha256": digest(encoded(analysis)),
        "limitations": search["limitations"]
        + [
            note["issue"]
            for note in critique["notes"]
            if note["resolution"] == "unresolved"
        ],
        "statistics": statistics(
            publications, analysis["events"], len(selected), len(records)
        ),
    }
    reference = store_result(value, request, results, "news-result")
    LOG.info(
        "[N02] project_id=%s publications=%d events=%d — result saved",
        request["project_id"],
        value["statistics"]["publication_count"],
        len(analysis["events"]),
    )
    return {"result": value, "reference": reference}
