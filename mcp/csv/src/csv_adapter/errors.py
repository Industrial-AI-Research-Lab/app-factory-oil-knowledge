"""Typed internal domain errors with the public ValueError contract."""

__all__ = ("CsvDomainError", "ServiceError", "domain_error")


class CsvDomainError(ValueError):
    """A safe, already-classified CSV boundary failure."""


class ServiceError(ValueError):
    """Service-owned domain failure with ValueError semantics."""


def domain_error(code: str, hint: str) -> CsvDomainError:
    """Build a safe classified error without URLs, bodies, or exception text."""
    return CsvDomainError(f"{code}: {hint}")
