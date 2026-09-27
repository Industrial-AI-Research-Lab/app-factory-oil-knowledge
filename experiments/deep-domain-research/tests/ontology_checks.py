"""Validate the v2 ontology and its exact source evidence; no classification."""

import json
import re
from pathlib import Path

RELATIONS = {
    "controls",
    "supports",
    "uses",
    "measured_by",
    "depends_on",
    "distinguishes_from",
    "part_of",
    "related_to",
}
KINDS = {"source_phrase", "abbreviation", "translation", "proposal"}
CONCEPT_TYPES = {
    "process",
    "method",
    "material",
    "property",
    "condition",
    "object",
    "operational_task",
    "representation",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value, label):
    require(isinstance(value, str) and value.strip(), f"{label}: empty string")


def indexed(records, label):
    require(isinstance(records, list) and records, f"{label}: empty list")
    result = {}
    for record in records:
        require(isinstance(record, dict), f"{label}: expected object")
        identifier = record.get("id")
        nonempty(identifier, label)
        require(identifier not in result, f"{label}: duplicate ID {identifier}")
        result[identifier] = record
    return result


def source_texts(sources, terminology, inputs):
    texts = {}
    files = {}
    for source in sources.values():
        for field in ("title", "path", "locator"):
            nonempty(source.get(field), f"source {source['id']}.{field}")
        path = (terminology / source["path"]).resolve()
        require(path.is_relative_to(inputs.resolve()), "Source escapes input corpus")
        if path not in files:
            raw = path.read_text(encoding="utf-8")
            if path.suffix == ".jsonl":
                files[path] = [
                    json.loads(line) for line in raw.splitlines() if line.strip()
                ]
            elif path.suffix == ".json":
                files[path] = json.loads(raw)
            else:
                files[path] = raw
        value = files[path]
        if path.suffix == ".jsonl":
            nonempty(source.get("record_key"), "JSONL source record_key")
            nonempty(source.get("record_id"), "JSONL source record_id")
            matches = [
                row
                for row in value
                if row.get(source["record_key"]) == source["record_id"]
            ]
            require(
                len(matches) == 1, f"Source record is missing/ambiguous: {source['id']}"
            )
            value = matches[0]
        if isinstance(value, dict):
            value = value.get("title", "") + "\n" + value.get("text", "")
        nonempty(value, f"source {source['id']} text")
        texts[source["id"]] = value
    return texts


def citation(item, texts, label):
    require(isinstance(item, dict), f"{label}: citation object required")
    source = item.get("source_id")
    require(source in texts, f"{label}: unknown source {source}")
    nonempty(item.get("quote"), f"{label} quote")
    require(
        item["quote"] in texts[source], f"{label}: quote does not match source {source}"
    )


def common_cards(document, key, terminology, inputs):
    require(document.get("schema_version") == 2, "Expected ontology schema_version=2")
    nonempty(document.get("version"), "Dictionary version")
    sources = indexed(document.get("sources"), "sources")
    texts = source_texts(sources, terminology, inputs)
    cards = indexed(document.get(key), key)
    for identifier, card in cards.items():
        for field in ("name", "definition", "plain_language"):
            nonempty(card.get(field), f"{identifier}.{field}")
        refs = card.get("source_ids")
        require(isinstance(refs, list) and refs, f"{identifier}: no sources")
        require(set(refs) <= sources.keys(), f"{identifier}: unknown source_ids")
        evidence = card.get("evidence")
        require(isinstance(evidence, list) and evidence, f"{identifier}: no evidence")
        for item in evidence:
            citation(item, texts, identifier)
            require(
                item["source_id"] in refs, f"{identifier}: undeclared evidence source"
            )
        keywords = card.get("keywords")
        require(isinstance(keywords, list) and keywords, f"{identifier}: no keywords")
        seen = set()
        for keyword in keywords:
            require(isinstance(keyword, dict), f"{identifier}: keyword object required")
            nonempty(keyword.get("text"), f"{identifier}: keyword text")
            nonempty(keyword.get("context"), f"{identifier}: keyword context")
            require(
                keyword["text"].casefold() not in seen,
                f"{identifier}: repeated keyword",
            )
            seen.add(keyword["text"].casefold())
            require(keyword.get("kind") in KINDS, f"{identifier}: keyword kind")
            if keyword["kind"] != "proposal":
                citation(keyword, texts, identifier)
                require(
                    keyword["source_id"] in refs,
                    f"{identifier}: undeclared keyword source",
                )
                if keyword["kind"] in {"source_phrase", "abbreviation"}:
                    require(
                        re.search(
                            r"(?<!\w)"
                            + re.escape(keyword["text"].casefold())
                            + r"(?!\w)",
                            keyword["quote"].casefold(),
                        ),
                        f"{identifier}: claimed observed keyword is absent from quote",
                    )
        parent = card.get("parent_id")
        ancestors = {identifier}
        while parent is not None:
            require(parent in cards, f"{identifier}: unknown parent")
            require(parent not in ancestors, f"{identifier}: parent cycle")
            ancestors.add(parent)
            parent = cards[parent].get("parent_id")
    return cards


def validate_ontology(terms_doc, taxonomy, inputs):
    inputs = Path(inputs)
    root = inputs / "terminology"
    terms = common_cards(terms_doc, "terms", root, inputs)
    categories = common_cards(taxonomy, "categories", root, inputs)
    for identifier, term in terms.items():
        nonempty(term.get("source"), f"{identifier}: B04 source")
        require(
            term.get("concept_type") in CONCEPT_TYPES, f"{identifier}: concept type"
        )
        require(
            term.get("domain_scope") in {"news", "knowledge", "both"},
            f"{identifier}: domain",
        )
        require(
            term.get("evidence_status") in {"source_grounded", "proposal"},
            f"{identifier}: evidence status",
        )
        aliases = term.get("synonyms")
        require(isinstance(aliases, list), f"{identifier}: synonyms type")
        require(
            all(isinstance(x, str) and x.strip() for x in aliases),
            f"{identifier}: empty alias",
        )
        require(
            len(set(x.casefold() for x in aliases)) == len(aliases),
            f"{identifier}: duplicate alias",
        )
        require(
            isinstance(term.get("relations"), list), f"{identifier}: relations type"
        )
        for relation in term["relations"]:
            require(isinstance(relation, dict), f"{identifier}: relation object")
            require(
                relation.get("target_id") in terms, f"{identifier}: relation target"
            )
            require(relation["target_id"] != identifier, f"{identifier}: self relation")
            require(relation.get("type") in RELATIONS, f"{identifier}: relation type")
            nonempty(relation.get("reason"), f"{identifier}: relation reason")
        require(
            isinstance(term.get("distinguish_from"), list),
            f"{identifier}: distinctions type",
        )
        for other in term["distinguish_from"]:
            require(isinstance(other, dict), f"{identifier}: distinction object")
            require(
                other.get("term_id") in terms and other["term_id"] != identifier,
                f"{identifier}: distinction target",
            )
            nonempty(other.get("reason"), f"{identifier}: distinction reason")
    for identifier, category in categories.items():
        for field in ("include", "exclude", "examples", "decision_questions"):
            values = category.get(field)
            require(isinstance(values, list) and values, f"{identifier}: empty {field}")
            for value in values:
                nonempty(value, f"{identifier}.{field}")
        require(
            isinstance(category.get("concept_ids"), list), f"{identifier}: concept_ids"
        )
        require(
            set(category["concept_ids"]) <= terms.keys(),
            f"{identifier}: unknown concept",
        )
    require(
        set(taxonomy["case2_category_ids"]) <= categories.keys(),
        "Unknown case2 category",
    )
    for facet in ("value_chain", "statement_type", "adoption_stage", "evidence_status"):
        values = indexed(taxonomy["facets"][facet]["values"], facet)
        require("unknown" in values, f"{facet}: unknown value missing")
        for value in values.values():
            nonempty(value.get("name"), f"{facet}: name")
            nonempty(value.get("definition"), f"{facet}: definition")
    for document, cards in ((terms_doc, terms), (taxonomy, categories)):
        changes = document.get("migration")
        require(isinstance(changes, list) and changes, "Migration missing")
        seen = set()
        for change in changes:
            require(isinstance(change, dict), "Migration object required")
            if change.get("status") == "added":
                require(change.get("old_id") is None, "Added migration has old ID")
            else:
                nonempty(change.get("old_id"), "Migration old ID")
                require(change["old_id"] not in seen, "Duplicate migration old ID")
                seen.add(change["old_id"])
            require(
                isinstance(change.get("replacement_ids"), list)
                and change["replacement_ids"],
                "Migration targets missing",
            )
            require(
                set(change["replacement_ids"]) <= cards.keys(),
                "Migration target missing",
            )
            nonempty(change.get("status"), "Migration status")
            nonempty(change.get("reason"), "Migration reason")
    return terms, categories
