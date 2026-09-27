from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..errors import ToolFailure


@dataclass
class QueryGuard:
    operation_budget: int
    operations: int = 0
    denied: bool = False

    def failure(self) -> ToolFailure | None:
        if self.denied:
            return ToolFailure("SQLITE_QUERY_DENIED", "SQLite operation is not allowed")
        if self.operations > self.operation_budget:
            return ToolFailure(
                "SQLITE_QUERY_BUDGET_EXCEEDED",
                "SQLite query exceeded its operation budget",
            )
        return None


def install_query_guard(
    connection: sqlite3.Connection,
    operation_budget: int,
    max_value_bytes: int,
) -> QueryGuard:
    guard = QueryGuard(operation_budget)
    allowed = {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
    safe_pragmas = {"table_info", "table_xinfo", "table_list"}

    def authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_PRAGMA and first in safe_pragmas:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and second == "load_extension":
            guard.denied = True
            return sqlite3.SQLITE_DENY
        if action not in allowed:
            guard.denied = True
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def progress() -> int:
        guard.operations += 1
        return int(guard.operations > guard.operation_budget)

    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max_value_bytes)
    connection.set_authorizer(authorize)
    connection.set_progress_handler(progress, 1)
    return guard


def query_failure(guard: QueryGuard, error: sqlite3.Error) -> ToolFailure | None:
    if failure := guard.failure():
        return failure
    if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_TOOBIG:
        return ToolFailure(
            "SQLITE_QUERY_VALUE_TOO_LARGE",
            "SQLite query value exceeds the configured limit",
        )
    return None
