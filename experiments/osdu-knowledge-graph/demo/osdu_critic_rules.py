"""Deterministic critic accept/revise rules used by unit tests."""

from __future__ import annotations

import json
import re
from typing import Any


REVISE = "revise"
ACCEPT = "accept"

_TOOL_ARG_KEYS = {"graphName", "query", "label", "params", "parameters", "sampleSize"}
_ENGLISH_PROTOCOL = (
    "i could not",
    "i'm sorry",
    "free-form answer is required",
    "could you please",
    "clarify",
    "you'd like to retrieve",
)
_REVISE_LINE = re.compile(r"^REVISE(\b|\s|$)")
_COUNT_NUMBER = re.compile(r"\d(?:[\d\s\u00a0\u202f,]{0,12}\d)?")


def critic_decision(message: Any, *, journal: Any | None = None) -> str:
    """Return accept or revise. Retry limits are owned by the workflow.

    journal is accepted for the future contract (facts must not contradict
    tool results) but form is decided first: a tool-arg dump is never accepted.
    """
    del journal
    if _first_line_is_revise(message):
        return REVISE
    if _looks_like_tool_dump(message):
        return REVISE
    if isinstance(message, str) and message.strip():
        lowered = message.lower()
        if any(token in lowered for token in _ENGLISH_PROTOCOL):
            return REVISE
        if _looks_like_cartesian_counts(message):
            return REVISE
        return ACCEPT
    return REVISE


def is_user_publishable(message: Any) -> bool:
    """True only when the critic output may be shown to the user."""
    if not isinstance(message, str) or not message.strip():
        return False
    return critic_decision(message) == ACCEPT


def _looks_like_cartesian_counts(message: str) -> bool:
    """True when Well, Wellbore, and WellLog are given the same large count."""
    lowered = message.lower()
    if "wellbore" not in lowered or "welllog" not in lowered:
        return False
    if not re.search(r"\bwell\b", lowered):
        return False
    counts = []
    for raw in _COUNT_NUMBER.findall(message):
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue
        value = int(digits)
        if value >= 50:
            counts.append(value)
    return len(counts) >= 3 and len(set(counts)) == 1


def _first_line_is_revise(message: Any) -> bool:
    if not isinstance(message, str) or not message.strip():
        return False
    first = message.lstrip().splitlines()[0].strip()
    return bool(_REVISE_LINE.match(first))


def _looks_like_tool_dump(message: Any) -> bool:
    if isinstance(message, dict):
        keys = {str(key) for key in message}
        if keys & {"query", "label"} and "graphName" in keys:
            return True
        if keys and keys <= _TOOL_ARG_KEYS:
            return bool(keys & {"query", "graphName", "label"})
        return False
    if not isinstance(message, str):
        return False
    text = message.strip()
    if re.search(r"^\s*MATCH\s*\(", text, flags=re.IGNORECASE):
        return True
    if '"query"' in text and "graphName" in text:
        return True
    if '"label"' in text and "graphName" in text:
        return True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return _looks_like_tool_dump(parsed)
    return False
