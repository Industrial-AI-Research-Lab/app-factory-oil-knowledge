from __future__ import annotations

from typing import Any

from pydantic import ValidationError


class ToolFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        public_message: str,
        path: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.public_message = public_message
        self.path = path
        self.detail = detail
        super().__init__(f"{code}: {public_message}")

    def as_result(self) -> dict[str, Any]:
        error = {"code": self.code, "message": self.public_message}
        if self.path is not None:
            error["path"] = self.path
        if self.detail is not None:
            error["detail"] = self.detail
        return {"status": "error", "error": error}


def json_path(location: tuple[int | str, ...]) -> str:
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in location
    )


def input_failure(error: ValidationError) -> ToolFailure:
    first = error.errors(include_url=False)[0]
    return ToolFailure("INPUT_INVALID", first["msg"], path=json_path(first["loc"]))
