"""Secure infrastructure adapters over the byte-identical source core.

Hardening-only wrappers: SQL and ranking stay in the source core.
The source core consistently uses normalized-vector L2 distance (``<->``);
this module builds no SQL and imports no ``psycopg2.sql`` helpers.
"""

import re
import threading
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any, Iterator

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor

from .config import DBConfig
from stairs_semantic_mapping.infrastructure.db_connection_provider import (
    ConnectionProvider,
)
from stairs_semantic_mapping.infrastructure.vector_query import (
    PgVectorStore,
    PgVectorStoreConfig,
    VectorQueryResult,
)

__all__ = ["SecurePostgresConnectionProvider", "ValidatingPgVectorStore"]

# SQL identifiers only: letters, digits, underscore, never starting with a digit.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Result sections the source store can return; anything else is rejected.
_ALLOWED_INCLUDE = frozenset({"ids", "results", "distances", "metadatas"})


def _require_identifier(name: str, value: str) -> None:
    """Validate a SQL identifier against a strict safe pattern.

    Args:
        name: Argument name used in the error message.
        value: Identifier value to check.

    Raises:
        ValueError: If ``value`` is not a string matching
            ``^[A-Za-z_][A-Za-z0-9_]*$``.
    """
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise ValueError(f"{name} must match ^[A-Za-z_][A-Za-z0-9_]*$")


class SecurePostgresConnectionProvider:
    """PostgreSQL cursor provider keeping secrets off instance state."""

    def __init__(self, db_config: DBConfig) -> None:
        """Store a detached frozen copy of the config.

        Args:
            db_config: Validated ``DBConfig`` instance.

        Raises:
            TypeError: If ``db_config`` is not a ``DBConfig`` instance.
        """
        if not isinstance(db_config, DBConfig):
            raise TypeError("db_config must be a DBConfig instance")
        self._db_config = db_config.model_copy()
        self._local = threading.local()

    def __repr__(self) -> str:
        """Return a redacted repr without secret values.

        Returns:
            Redacted string with ``password=SecretStr('**********')``.
        """
        return (
            f"{type(self).__name__}(host={self._db_config.host!r}, "
            f"port={self._db_config.port!r}, "
            f"dbname={self._db_config.dbname!r}, "
            f"user={self._db_config.user!r}, "
            "password=SecretStr('**********'))"
        )

    def __str__(self) -> str:
        """Return the same redacted string as :meth:`__repr__`.

        Returns:
            Redacted string without secret values.
        """
        return self.__repr__()

    @contextmanager
    def get_cursor(self, cursor_factory: Any = RealDictCursor) -> Iterator[Any]:
        """Yield a cursor with automatic commit and cleanup.

        Plaintext credentials live only in local ``kwargs`` between
        ``to_psycopg_kwargs()`` and ``psycopg2.connect``; cleared before
        yielding, never stored or logged.

        Args:
            cursor_factory: Cursor factory. Defaults to RealDictCursor.

        Yields:
            DB cursor returning rows as dicts by default.
        """
        scoped_cursor = getattr(self._local, "cursor", None)
        if scoped_cursor is not None:
            yield scoped_cursor
            return
        with self.request_scope(cursor_factory) as cursor:
            yield cursor

    @contextmanager
    def request_scope(self, cursor_factory: Any = RealDictCursor) -> Iterator[Any]:
        """Share one read-only transaction and cursor within a mapping request.

        Args:
            cursor_factory: Cursor factory. Defaults to RealDictCursor.

        Yields:
            Shared cursor; connection closes when the outermost scope exits.
        """
        if getattr(self._local, "cursor", None) is not None:
            yield self._local.cursor
            return
        kwargs = self._db_config.to_psycopg_kwargs()
        try:
            conn = psycopg2.connect(**kwargs)
        finally:
            kwargs.clear()
        try:
            conn.set_session(readonly=True, autocommit=False)
            with conn:
                with conn.cursor(cursor_factory=cursor_factory) as cur:
                    self._local.cursor = cur
                    try:
                        yield cur
                    finally:
                        del self._local.cursor
        finally:
            conn.close()


class ValidatingPgVectorStore:
    """Validating composition wrapper over source ``PgVectorStore``."""

    def __init__(
        self,
        conn_provider: ConnectionProvider,
        config: PgVectorStoreConfig,
        required_metadata_fields: Sequence[str] | None = None,
    ) -> None:
        """Validate config and compose the source store.

        Args:
            conn_provider: Cursor provider exposing ``get_cursor``.
            config: Source store configuration.
            required_metadata_fields: Optional required metadata keys.

        Raises:
            TypeError: If ``conn_provider`` lacks ``get_cursor`` or
                ``config`` is not a ``PgVectorStoreConfig``.
            ValueError: If identifiers or ``metadata_columns`` are invalid.
        """
        if not hasattr(conn_provider, "get_cursor"):
            raise TypeError("conn_provider must expose get_cursor")
        if not isinstance(config, PgVectorStoreConfig):
            raise TypeError("config must be PgVectorStoreConfig")
        _require_identifier("table_name", config.table_name)
        _require_identifier("id_column", config.id_column)
        _require_identifier("embedding_column", config.embedding_column)
        if config.text_column is not None:
            _require_identifier("text_column", config.text_column)
        if config.metadata_columns is not None:
            if not isinstance(config.metadata_columns, list) or not config.metadata_columns:
                raise ValueError("metadata_columns must be a non-empty list or None")
            for col in config.metadata_columns:
                _require_identifier("metadata column", col)
        if required_metadata_fields is None:
            required: tuple[str, ...] = ()
        else:
            if isinstance(required_metadata_fields, (str, bytes, bytearray)):
                raise ValueError("required_metadata_fields must be a sequence of strings")
            try:
                fields = tuple(required_metadata_fields)
            except TypeError:
                raise ValueError(
                    "required_metadata_fields must be a sequence of strings"
                ) from None
            for field in fields:
                _require_identifier("required_metadata_field", field)
            required = fields
        self._required_metadata_fields = required
        self.conn_provider = conn_provider
        self.config = config
        self._store = PgVectorStore(conn_provider, config)

    def query(
        self,
        query_embeddings: np.ndarray | Sequence[Sequence[float]],
        top_k: int,
        where: dict[str, Sequence[Any]] | None = None,
        include: list[str] | None = None,
    ) -> VectorQueryResult:
        """Validate inputs, delegate SQL, then validate result shape.

        Args:
            query_embeddings: 2D array-like of shape ``(N, dim)`` with
                non-zero ``dim``; coerced to float.
            top_k: Positive non-bool number of neighbours per query.
            where: Optional mapping of column to allowed-values sequence.
                Keys must be safe identifiers; values must be non-string
                sequences (empty lists are allowed and ignored by source).
            include: Optional subset of ``["ids", "results", "distances",
                "metadatas"]``.

        Returns:
            The same ``VectorQueryResult`` instance returned by the source
            store, after post-validation.

        Raises:
            ValueError: If ``top_k``, embeddings, ``where``, ``include``,
                outer cardinality, or required metadata checks fail.
        """
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
            raise ValueError("top_k must be a positive int")
        try:
            arr = np.asarray(query_embeddings, dtype=float)
        except (ValueError, TypeError):
            raise ValueError(
                "query_embeddings must be convertible to float"
            ) from None
        if arr.ndim != 2:
            raise ValueError("query_embeddings must be 2D (N, dim)")
        if arr.shape[1] == 0:
            raise ValueError("query_embeddings must have non-zero dimension")
        if not np.isfinite(arr).all():
            raise ValueError("query_embeddings must contain only finite values")
        if where is not None:
            if not isinstance(where, dict):
                raise ValueError("where must be a dict or None")
            for col_name, values in where.items():
                _require_identifier("where column", col_name)
                if isinstance(values, (str, bytes, bytearray)):
                    raise ValueError("where values must be a non-string sequence")
                if not isinstance(values, Sequence):
                    raise ValueError("where values must be a non-string sequence")
        if include is not None:
            if isinstance(include, (str, bytes, bytearray)) or not isinstance(
                include, Sequence
            ):
                raise ValueError("invalid include")
            for entry in include:
                if entry not in _ALLOWED_INCLUDE:
                    raise ValueError("invalid include")
        result = self._store.query(query_embeddings=arr, top_k=top_k, where=where, include=include)
        nrows = int(arr.shape[0])
        if len(result.ids) != nrows or len(result.results) != nrows:
            raise ValueError(
                f"result outer cardinality {len(result.ids)}/"
                f"{len(result.results)} != nqueries {nrows}"
            )
        if include is not None and "distances" in include:
            if len(result.distances) != nrows:
                raise ValueError("result distances outer cardinality mismatch")
        if include is not None and "metadatas" in include:
            if len(result.metadatas) != nrows:
                raise ValueError("result metadatas outer cardinality mismatch")
        if self._required_metadata_fields and (
            include is not None and "metadatas" in include
        ):
            if not result.metadatas or len(result.metadatas) != nrows:
                raise ValueError("required metadatas are missing")
            for per_query in result.metadatas:
                for meta in per_query:
                    if not isinstance(meta, dict):
                        raise ValueError("metadata entry must be a dict")
                    for field in self._required_metadata_fields:
                        if field not in meta or meta[field] is None:
                            raise ValueError("required metadata is missing or None")
        return result
