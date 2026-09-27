from contextlib import contextmanager
from typing import Dict, Any, Iterator, Protocol, runtime_checkable

import psycopg2
from psycopg2.extras import RealDictCursor


@runtime_checkable
class ConnectionProvider(Protocol):
    """
    Protocol for objects that can provide database cursors.

    Any implementation must expose a context-managed `get_cursor` method
    that yields a DB cursor and takes care of proper resource handling
    (commit/rollback, closing connection, etc.).

    This protocol is used by PgVectorStore and other infrastructure
    components, so they do not depend on a concrete connection implementation.
    """

    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor) -> Iterator[Any]:
        """
        Yield a DB cursor wrapped in a context manager.

        Parameters
        ----------
        cursor_factory :
            Optional cursor factory; by default RealDictCursor is used
            (or its analogue in concrete implementations)
        """
        ...


class PostgresConnectionProvider:
    """
    Lightweight connection manager for PostgreSQL access.

    Parameters
    ----------
    db_config : Dict[str, Any]
        A dictionary of psycopg2 connection parameters, e.g.:
        {
            "host": "localhost",
            "port": 5432,
            "dbname": "mydb",
            "user": "user",
            "password": "secret",
        }

    Notes
    -----
    - A new connection is opened for each `get_cursor` call.
    - The connection is automatically committed at the end of the context block.
    - Both the cursor and the connection are always closed, even if an error
      occurs inside the `with` block.
    - The cursor uses `RealDictCursor` by default, returning rows as dictionaries.

    This makes the class suitable for small-to-medium workloads, utilities,
    and clean separation between infrastructure and domain logic. If needed,
    it can later be replaced with a pooled implementation without changing
    dependent code.
    """
    def __init__(self, db_config: Dict[str, Any]):
        self._db_config = db_config

    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor) -> Iterator[Any]:
        conn = psycopg2.connect(**self._db_config)
        try:
            with conn:
                with conn.cursor(cursor_factory=cursor_factory) as cur:
                    yield cur
        finally:
            conn.close()
