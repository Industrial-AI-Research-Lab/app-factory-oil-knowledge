"""Shared private helpers for the semantic mapping business adapters.

Single home for the duplicated ``_unwrap_token`` / ``_to_distances`` logic
used by :mod:`.works` and :mod:`.hierarchies`. Private to the package:
transport code must keep importing the public API from the package root.
"""

import math

from .config import RuntimeSettings

__all__ = ["unwrap_tei_token", "to_distances", "require_top_k"]


def unwrap_tei_token(settings: RuntimeSettings) -> str | None:
    """Return the TEI bearer token without logging it.

    Args:
        settings: Explicit runtime settings.

    Returns:
        Plain token string or None when not configured.
    """
    token = settings.embedding_access_token
    return None if token is None else token.get_secret_value()


def to_distances(scores: list[object] | tuple[object, ...]) -> tuple[float, ...]:
    """Convert source distance scores to floats without leaking raw values.

    Args:
        scores: Raw source distance values for one input.

    Returns:
        Distances as a float tuple.

    Raises:
        ValueError: With generic ``mismatched mapping cardinalities`` when
            any ``float()`` conversion fails. No raw values in the message.
    """
    try:
        distances = tuple(float(v) for v in scores)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise ValueError("mismatched mapping cardinalities") from None
    if not all(math.isfinite(d) for d in distances):
        raise ValueError("mismatched mapping cardinalities")
    return distances


def require_top_k(top_k: object) -> int:
    """Validate top-k for direct adapter calls (transport already checks).

    Args:
        top_k: Requested neighbours per input.

    Returns:
        Validated value in [1, 10].

    Raises:
        ValueError: If out of bounds, bool, or not an int.
    """
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise ValueError("top_k must be an int in [1, 10]")
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be an int in [1, 10]")
    return top_k
