from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _origin_key(scheme: str, hostname: str, port: int | None) -> str:
    normalized_host = hostname.encode("idna").decode("ascii").casefold()
    rendered_host = (
        f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    )
    default_port = 443 if scheme == "https" else 80
    port_suffix = "" if port in {None, default_port} else f":{port}"
    return f"{scheme}://{rendered_host}{port_suffix}"


def _validate_object_storage_origins(
    values: tuple[str, ...],
    *,
    allow_insecure_loopback: bool,
    allow_insecure_object_storage: bool,
) -> frozenset[str]:
    origins: set[str] = set()
    for value in values:
        if (
            not value
            or any(character.isspace() or ord(character) < 32 for character in value)
            or "?" in value
            or "#" in value
        ):
            raise ValueError(
                "OBJECT_STORAGE_ALLOWED_ORIGINS contains an invalid origin"
            )
        try:
            parsed = urlsplit(value)
            port = parsed.port
            hostname = parsed.hostname or ""
            origin = _origin_key(parsed.scheme, hostname, port)
        except (UnicodeError, ValueError):
            raise ValueError(
                "OBJECT_STORAGE_ALLOWED_ORIGINS contains an invalid origin"
            ) from None
        scheme_is_allowed = parsed.scheme == "https" or (
            parsed.scheme == "http"
            and hostname
            and (
                allow_insecure_object_storage
                or (_is_loopback(hostname) and allow_insecure_loopback)
            )
        )
        if (
            not scheme_is_allowed
            or not hostname
            or hostname.endswith(".")
            or "*" in hostname
            or port == 0
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "OBJECT_STORAGE_ALLOWED_ORIGINS contains an invalid origin"
            )
        origins.add(origin)
    return frozenset(origins)


def _validate_tavily_base_url(
    value: str,
    *,
    allow_insecure_loopback: bool,
) -> str:
    if (
        not value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or "?" in value
        or "#" in value
    ):
        raise ValueError("TAVILY_BASE_URL must be a valid HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("TAVILY_BASE_URL must be a valid HTTPS URL") from None
    scheme_is_allowed = parsed.scheme == "https" or (
        parsed.scheme == "http"
        and parsed.hostname is not None
        and _is_loopback(parsed.hostname)
        and allow_insecure_loopback
    )
    if (
        not scheme_is_allowed
        or not parsed.hostname
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("TAVILY_BASE_URL must be a valid HTTPS URL")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    tavily_api_key: str = field(repr=False)
    bundle_receipt_key: str = field(repr=False)
    tavily_base_url: str = "https://api.tavily.com"
    request_timeout_seconds: float = 120.0
    query_timeout_seconds: float = 40.0
    discovery_budget_seconds: float = 300.0
    snapshot_budget_seconds: float = 480.0
    max_download_bytes: int = 52_428_800
    max_search_response_bytes: int = 1_048_576
    max_query_rows: int = 200
    sqlite_operation_budget: int = 100_000
    calculator_scan_budget: int = 1_000_000
    sqlite_max_value_bytes: int = 1_048_576
    allow_insecure_loopback: bool = False
    allow_insecure_object_storage: bool = False
    object_storage_allowed_origins: tuple[str, ...] | frozenset[str] = ()

    def __post_init__(self) -> None:
        if not self.tavily_api_key.strip():
            raise ValueError("TAVILY_API_KEY must not be blank")
        if len(self.bundle_receipt_key.encode("utf-8")) < 32:
            raise ValueError("BUNDLE_RECEIPT_KEY must contain at least 32 bytes")
        if not 1 <= self.port <= 65_535:
            raise ValueError("PORT must be between 1 and 65535")
        if (
            not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be positive")
        for name, seconds in (
            ("QUERY_TIMEOUT_SECONDS", self.query_timeout_seconds),
            ("DISCOVERY_BUDGET_SECONDS", self.discovery_budget_seconds),
            ("SNAPSHOT_BUDGET_SECONDS", self.snapshot_budget_seconds),
        ):
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_download_bytes <= 0:
            raise ValueError("MAX_DOWNLOAD_BYTES must be positive")
        if self.max_search_response_bytes <= 0:
            raise ValueError("MAX_SEARCH_RESPONSE_BYTES must be positive")
        if self.max_query_rows <= 0:
            raise ValueError("MAX_QUERY_ROWS must be positive")
        if self.sqlite_operation_budget <= 0:
            raise ValueError("SQLITE_OPERATION_BUDGET must be positive")
        if self.calculator_scan_budget <= 0:
            raise ValueError("CALCULATOR_SCAN_BUDGET must be positive")
        if self.sqlite_max_value_bytes <= 0:
            raise ValueError("SQLITE_MAX_VALUE_BYTES must be positive")
        object.__setattr__(
            self,
            "tavily_base_url",
            _validate_tavily_base_url(
                self.tavily_base_url,
                allow_insecure_loopback=self.allow_insecure_loopback,
            ),
        )
        object.__setattr__(
            self,
            "object_storage_allowed_origins",
            _validate_object_storage_origins(
                tuple(self.object_storage_allowed_origins),
                allow_insecure_loopback=self.allow_insecure_loopback,
                allow_insecure_object_storage=self.allow_insecure_object_storage,
            ),
        )

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8003")),
            tavily_api_key=os.environ["TAVILY_API_KEY"],
            bundle_receipt_key=os.environ["BUNDLE_RECEIPT_KEY"],
            tavily_base_url=os.getenv(
                "TAVILY_BASE_URL",
                "https://api.tavily.com",
            ),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "120")),
            query_timeout_seconds=float(os.getenv("QUERY_TIMEOUT_SECONDS", "40")),
            discovery_budget_seconds=float(
                os.getenv("DISCOVERY_BUDGET_SECONDS", "300")
            ),
            snapshot_budget_seconds=float(os.getenv("SNAPSHOT_BUDGET_SECONDS", "480")),
            max_download_bytes=int(os.getenv("MAX_DOWNLOAD_BYTES", "52428800")),
            max_search_response_bytes=int(
                os.getenv("MAX_SEARCH_RESPONSE_BYTES", "1048576")
            ),
            max_query_rows=int(os.getenv("MAX_QUERY_ROWS", "200")),
            sqlite_operation_budget=int(os.getenv("SQLITE_OPERATION_BUDGET", "100000")),
            calculator_scan_budget=int(os.getenv("CALCULATOR_SCAN_BUDGET", "1000000")),
            sqlite_max_value_bytes=int(os.getenv("SQLITE_MAX_VALUE_BYTES", "1048576")),
            allow_insecure_loopback=(
                os.getenv("ALLOW_INSECURE_LOOPBACK", "false").strip().casefold()
                == "true"
            ),
            allow_insecure_object_storage=(
                os.getenv("ALLOW_INSECURE_OBJECT_STORAGE", "false").strip().casefold()
                == "true"
            ),
            object_storage_allowed_origins=tuple(
                origin.strip()
                for origin in os.getenv("OBJECT_STORAGE_ALLOWED_ORIGINS", "").split(",")
                if origin.strip()
            ),
        )
