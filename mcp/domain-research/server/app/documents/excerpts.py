from __future__ import annotations

MAX_EXCERPT_CHARS = 600


def document_excerpt(text: str, query_tokens: list[str]) -> str:
    folded = text.casefold()
    positions = [folded.find(token.casefold()) for token in query_tokens]
    matches = [position for position in positions if position >= 0]
    center = min(matches) if matches else 0
    start = max(0, center - MAX_EXCERPT_CHARS // 3)
    end = min(len(text), start + MAX_EXCERPT_CHARS)
    if end - start < MAX_EXCERPT_CHARS:
        start = max(0, end - MAX_EXCERPT_CHARS)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
