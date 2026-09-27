import pytest

from app.config import Settings


def test_settings_loads_safe_defaults_without_exposing_api_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "classified")
    monkeypatch.setenv("BUNDLE_RECEIPT_KEY", "classified-receipt-key-000000000000")
    for name in (
        "HOST",
        "PORT",
        "TAVILY_BASE_URL",
        "REQUEST_TIMEOUT_SECONDS",
        "QUERY_TIMEOUT_SECONDS",
        "DISCOVERY_BUDGET_SECONDS",
        "SNAPSHOT_BUDGET_SECONDS",
        "MAX_DOWNLOAD_BYTES",
        "MAX_SEARCH_RESPONSE_BYTES",
        "MAX_QUERY_ROWS",
        "SQLITE_OPERATION_BUDGET",
        "CALCULATOR_SCAN_BUDGET",
        "SQLITE_MAX_VALUE_BYTES",
        "ALLOW_INSECURE_LOOPBACK",
        "ALLOW_INSECURE_OBJECT_STORAGE",
        "OBJECT_STORAGE_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8003
    assert settings.tavily_base_url == "https://api.tavily.com"
    assert settings.request_timeout_seconds == 120
    assert settings.query_timeout_seconds == 40
    assert settings.discovery_budget_seconds == 300
    assert settings.snapshot_budget_seconds == 480
    assert settings.max_download_bytes == 52_428_800
    assert settings.max_search_response_bytes == 1_048_576
    assert settings.max_query_rows == 200
    assert settings.sqlite_operation_budget == 100_000
    assert settings.calculator_scan_budget == 1_000_000
    assert settings.sqlite_max_value_bytes == 1_048_576
    assert settings.object_storage_allowed_origins == frozenset()
    assert "classified" not in repr(settings)


@pytest.mark.parametrize("receipt_key", ["", "short"])
def test_settings_rejects_weak_bundle_receipt_key(monkeypatch, receipt_key):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("BUNDLE_RECEIPT_KEY", receipt_key)

    with pytest.raises(ValueError, match="BUNDLE_RECEIPT_KEY"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORT", "0"),
        ("PORT", "65536"),
        ("REQUEST_TIMEOUT_SECONDS", "0"),
        ("QUERY_TIMEOUT_SECONDS", "0"),
        ("DISCOVERY_BUDGET_SECONDS", "-1"),
        ("DISCOVERY_BUDGET_SECONDS", "nan"),
        ("SNAPSHOT_BUDGET_SECONDS", "0"),
        ("SNAPSHOT_BUDGET_SECONDS", "inf"),
        ("MAX_DOWNLOAD_BYTES", "0"),
        ("MAX_SEARCH_RESPONSE_BYTES", "0"),
        ("MAX_QUERY_ROWS", "0"),
        ("SQLITE_OPERATION_BUDGET", "0"),
        ("CALCULATOR_SCAN_BUDGET", "0"),
        ("SQLITE_MAX_VALUE_BYTES", "0"),
    ],
)
def test_settings_rejects_invalid_runtime_bound(monkeypatch, name, value):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_rejects_blank_api_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "   ")

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        Settings.from_env()


def test_settings_requires_bundle_receipt_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.delenv("BUNDLE_RECEIPT_KEY", raising=False)

    with pytest.raises(KeyError, match="BUNDLE_RECEIPT_KEY"):
        Settings.from_env()


def test_settings_rejects_remote_plain_http_tavily_endpoint(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("TAVILY_BASE_URL", "http://provider.internal")

    with pytest.raises(ValueError, match="TAVILY_BASE_URL"):
        Settings.from_env()


@pytest.mark.parametrize(
    "endpoint",
    [
        "provider.internal",
        "ftp://provider.internal",
        "https://user:secret@provider.internal",
        "https://provider.internal?region=test",
        "https://provider.internal#fragment",
        "https://provider.internal?",
        "https://provider.internal#",
        "https://provider.internal/path\nsecond-line",
        "https://provider.internal:0",
        "https://provider.internal:invalid",
    ],
)
def test_settings_rejects_unsafe_tavily_endpoint(monkeypatch, endpoint):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("TAVILY_BASE_URL", endpoint)

    with pytest.raises(ValueError, match="TAVILY_BASE_URL"):
        Settings.from_env()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8080/provider/",
        "http://127.0.0.1:8080/provider/",
        "http://[::1]:8080/provider/",
    ],
)
def test_settings_allows_explicit_insecure_loopback_tavily_endpoint(
    monkeypatch,
    endpoint,
):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("TAVILY_BASE_URL", endpoint)
    monkeypatch.setenv("ALLOW_INSECURE_LOOPBACK", "true")

    settings = Settings.from_env()

    assert settings.tavily_base_url == endpoint.rstrip("/")


def test_settings_normalizes_exact_object_storage_origins(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv(
        "OBJECT_STORAGE_ALLOWED_ORIGINS",
        "https://OBJECTS.TEST:443, https://cdn.test:8443/",
    )

    settings = Settings.from_env()

    assert settings.object_storage_allowed_origins == frozenset(
        {"https://objects.test", "https://cdn.test:8443"}
    )


def test_settings_allows_opted_in_loopback_object_storage_origin(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_LOOPBACK", "true")
    monkeypatch.setenv(
        "OBJECT_STORAGE_ALLOWED_ORIGINS",
        "http://127.0.0.1:9000",
    )

    settings = Settings.from_env()

    assert settings.object_storage_allowed_origins == frozenset(
        {"http://127.0.0.1:9000"}
    )


def test_settings_allows_opted_in_plain_http_object_storage_origin(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_OBJECT_STORAGE", "true")
    monkeypatch.setenv(
        "OBJECT_STORAGE_ALLOWED_ORIGINS",
        "http://minio:9000",
    )

    settings = Settings.from_env()

    assert settings.object_storage_allowed_origins == frozenset({"http://minio:9000"})


@pytest.mark.parametrize(
    "origin",
    [
        "http://objects.test",
        "https://user:secret@objects.test",
        "https://objects.test/path",
        "https://objects.test?region=test",
        "https://objects.test#fragment",
        "https://objects.test./",
        "https://*.objects.test",
        "https://objects.test:0",
        "https://objects.test:invalid",
    ],
)
def test_settings_rejects_invalid_object_storage_origin(monkeypatch, origin):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("OBJECT_STORAGE_ALLOWED_ORIGINS", origin)

    with pytest.raises(ValueError, match="OBJECT_STORAGE_ALLOWED_ORIGINS"):
        Settings.from_env()


def test_settings_read_discovery_time_limits_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("DISCOVERY_BUDGET_SECONDS", "90")

    settings = Settings.from_env()

    assert settings.query_timeout_seconds == 12.5
    assert settings.discovery_budget_seconds == 90


def test_settings_read_snapshot_budget_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    monkeypatch.setenv("SNAPSHOT_BUDGET_SECONDS", "600.5")

    assert Settings.from_env().snapshot_budget_seconds == 600.5
