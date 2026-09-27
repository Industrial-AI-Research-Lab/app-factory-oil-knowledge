import logging
import re
import unicodedata
from collections import defaultdict

from command_io import CommandError, digest, required
from news_inputs import array_value, object_value, strings
from news_storage import encoded, read_result, store_result

LOG = logging.getLogger(__name__)


def normalized(value):
    return " ".join(
        unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split()
    )


def terminology(value, brief, terms, taxonomy):
    object_value(value, "terminology_result")
    if value.get("query") != brief["query"]:
        raise CommandError("VERSION_MISMATCH", "Terminology query differs from brief")
    if value.get("dictionary_versions") != {
        "terms": terms["version"],
        "taxonomy": taxonomy["version"],
    }:
        raise CommandError("VERSION_MISMATCH", "Dictionary versions differ")
    required(value, "refined_query")
    strings(value.get("constraints"), "constraints")
    strings(value.get("ambiguities"), "ambiguities")
    term_ids = {term["id"] for term in terms["terms"]}
    category_ids = {category["id"] for category in taxonomy["categories"]}
    for key in ("detected_terms", "expansions"):
        for item in array_value(value.get(key), key):
            object_value(item, key)
            if required(item, "term_id") not in term_ids:
                raise CommandError("UNKNOWN_ID", "Unknown terminology ID")
            required(item, "reason")
            field = "evidence" if key == "detected_terms" else "query_fragment"
            fragment = required(item, field)
            if key == "detected_terms" and fragment not in brief["query"]:
                raise CommandError(
                    "INVALID_EVIDENCE", "Term evidence is absent from query"
                )
    for item in array_value(value.get("exclusions"), "exclusions"):
        object_value(item, "exclusions")
        required(item, "reason")
        if "term_id" not in item or "category_id" not in item:
            raise CommandError("INVALID_INPUT", "Exclusion requires both nullable IDs")
        if item["term_id"] is None and item["category_id"] is None:
            raise CommandError("INVALID_INPUT", "Exclusion requires an ID")
        for field, valid in (("term_id", term_ids), ("category_id", category_ids)):
            if item[field] is not None and required(item, field) not in valid:
                raise CommandError("UNKNOWN_ID", "Unknown exclusion ID", field=field)
    return value


def duplicate_groups(records):
    parents = {record["publication_id"]: record["publication_id"] for record in records}

    def leader(identifier):
        while parents[identifier] != identifier:
            identifier = parents[identifier]
        return identifier

    keys = {}
    links = []
    for record in records:
        identifier = record["publication_id"]
        for kind, key in (
            ("url", record.get("url")),
            ("content", normalized(record.get("text") or "")),
        ):
            if not key or not key.strip():
                continue
            previous = keys.get((kind, key))
            if previous is not None:
                parents[leader(identifier)] = leader(previous)
                links.append((identifier, previous, kind))
            keys[(kind, key)] = identifier
    groups = defaultdict(list)
    for identifier in parents:
        groups[leader(identifier)].append(identifier)
    return [
        {
            "publication_ids": sorted(ids),
            "reason": "Exact archived URL/content identity",
            "links": [
                {"publication_ids": [left, right], "basis": basis}
                for left, right, basis in links
                if left in ids and right in ids
            ],
        }
        for ids in groups.values()
        if len(ids) > 1
    ]


def lexical_matches(record, fragments):
    text = normalized(
        "\n".join(record[key] for key in ("publication_id", "title", "text"))
    )
    tokens = set(re.findall(r"\w+", text))
    matches = []
    for fragment in fragments:
        query = normalized(fragment["query_fragment"])
        words = sorted(
            {word for word in re.findall(r"\w+", query) if len(word) >= 3} & tokens
        )
        if query in text or words:
            matches.append(fragment | {"matched_terms": words})
    return matches


def candidates(request, records, terms, taxonomy, provenance, results):
    reference = request.get("brief_ref")
    brief = read_result(reference, request, results, provenance, "brief")
    approval = object_value(request.get("approval"), "approval")
    for key in ("approval_id", "gate_node_id", "brief_sha256"):
        required(approval, key)
    if approval["brief_sha256"] != reference["sha256"]:
        raise CommandError("VERSION_MISMATCH", "Approval refers to another brief")
    term_result = terminology(request.get("terminology_result"), brief, terms, taxonomy)
    fragments = [{"query_fragment": brief["query"], "term_id": None, "origin": "query"}]
    fragments.extend(
        item | {"origin": "expansion"} for item in term_result["expansions"]
    )
    selected, audit = [], []
    for record in records:
        identifier = record["publication_id"]
        missing = [
            key
            for key in ("text", "url", "source")
            if not (record.get(key) or "").strip()
        ]
        if (
            not brief["period"]["start"]
            <= record["published_date"]
            <= brief["period"]["end"]
        ):
            status, reason = (
                "exclude_period",
                "Publication date is outside the agreed inclusive period",
            )
        elif missing:
            (
                status,
                reason,
            ) = "insufficient_evidence", "Unavailable archived fields: " + ", ".join(
                missing
            )
        else:
            matches = lexical_matches(record, fragments)
            status = "candidate" if matches else "no_match"
            reason = (
                "Matched query/allowed expansion"
                if matches
                else "No lexical match in publication ID/title/text"
            )
            if matches:
                selected.append(record | {"matches": matches})
        audit.append({"publication_id": identifier, "status": status, "reason": reason})
        if status != "candidate":
            LOG.warning(
                "[N02] project_id=%s publication_id=%s status=%s — %s",
                request["project_id"],
                identifier,
                status,
                reason,
            )
    value = {
        "kind": "candidates",
        "project_id": request["project_id"],
        "run_id": request["run_id"],
        "inputs": provenance,
        "brief_ref": reference,
        "brief": brief,
        "approval": approval,
        "terminology_result": term_result,
        "candidates": selected,
        "audit": audit,
        "exact_duplicate_groups": duplicate_groups(records),
        "limitations": [
            "Lexical candidates require semantic review of the complete archived text.",
            "Source URLs describe provenance; they were not fetched during this command.",
        ],
    }
    if not selected:
        value["limitations"].append(
            f'No candidates; archive dates {brief["archive_period"]}'
        )
    name = "candidates-" + digest(encoded(value))[:16]
    LOG.info(
        "[N02] project_id=%s publications=%d candidates=%d — search completed",
        request["project_id"],
        len(records),
        len(selected),
    )
    return value | {"reference": store_result(value, request, results, name)}
