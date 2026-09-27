"""Secure runtime configuration for the semantic mapping adapter.

Owns ``DBConfig``, ``RuntimeSettings``, and ``load_runtime_settings``.
Importing has no side effects: no settings instantiation, no env reads,
no files, no connections, no FastMCP. Settings load only on explicit call.
"""


import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DBConfig", "RuntimeSettings", "load_runtime_settings"]


class DBConfig(BaseModel):
    """PostgreSQL connection parameters.

    Attributes:
        host: Database host name.
        port: Database TCP port.
        dbname: Database name.
        user: Database user name.
        password: Database password (redacted from repr).
    """

    model_config = ConfigDict(frozen=True)

    host: str = Field(..., min_length=1, description="Database host name.")
    port: int = Field(..., ge=1, le=65535, description="Database TCP port.")
    dbname: str = Field(..., min_length=1, description="Database name.")
    user: str = Field(..., min_length=1, description="Database user name.")
    password: SecretStr = Field(..., description="Database password.")
    connect_timeout: int = Field(default=5, ge=1, le=30, description="Connection timeout in seconds.")
    statement_timeout_ms: int = Field(default=15000, ge=100, le=120000, description="Statement timeout in milliseconds.")

    def to_psycopg_kwargs(self) -> dict[str, Any]:
        """Return psycopg2 connect kwargs, unwrapping password only here.

        Returns:
            Dict with plain password string for ``psycopg2.connect`` only.
            Call immediately at the connect boundary; do not store the result.
        """
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password.get_secret_value(),
            "connect_timeout": self.connect_timeout,
            "options": f"-c statement_timeout={self.statement_timeout_ms}",
        }


class RuntimeSettings(BaseSettings):
    """Immutable runtime configuration bound to explicit env names.

    Attributes:
        db_config: PostgreSQL connection model from ``DB_CONFIG`` JSON.
        embedding_host_categories: TEI endpoint for category embeddings.
        embedding_host_tasks: TEI endpoint for task-name embeddings.
        embedding_host_hierarchies: TEI endpoint for hierarchy embeddings.
        embedding_access_token: Optional TEI bearer token (redacted).
        top_k_names: Default top-k for name/hierarchy results.
        top_k_categories: Default top-k for category candidates.
        category_id_field: Required metadata column for category IDs.
        category_name_field: Required metadata column for category names.
        batch_size_categories: TEI batch size for category embeddings.
        batch_size_tasks: TEI batch size for task/hierarchy embeddings.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
        frozen=True,
    )

    db_config: DBConfig = Field(..., alias="DB_CONFIG", description="PostgreSQL connection model as JSON.")

    @field_validator("db_config", mode="before")
    @classmethod
    def _decode_db_config(cls, value: Any) -> Any:
        """Decode string/bytes JSON for DB_CONFIG without disclosing content.

        Args:
            value: Raw ``DB_CONFIG`` value as model, mapping, or JSON text.

        Returns:
            Validated ``DBConfig`` input as model or mapping.

        Raises:
            ValueError: If the value is not a JSON object.
        """
        if isinstance(value, DBConfig):
            return value
        if isinstance(value, dict):
            return value
        if isinstance(value, (str, bytes, bytearray)):
            try:
                decoded = json.loads(value)
            except (TypeError, UnicodeDecodeError, ValueError):
                raise ValueError("DB_CONFIG must be a JSON object") from None
            if not isinstance(decoded, dict):
                raise ValueError("DB_CONFIG must be a JSON object")
            return decoded
        raise ValueError("DB_CONFIG must be a JSON object")

    embedding_host_categories: str = Field(
        default="http://embedder/embed",
        alias="EMBEDDING_HOST_CATEGORIES",
        min_length=1,
        description="TEI endpoint for category embeddings.",
    )
    embedding_host_tasks: str = Field(
        default="http://embedder/embed",
        alias="EMBEDDING_HOST_TASKS",
        min_length=1,
        description="TEI endpoint for task-name embeddings.",
    )
    embedding_host_hierarchies: str = Field(
        default="http://embedder/embed",
        alias="EMBEDDING_HOST_HIERARCHIES",
        min_length=1,
        description="TEI endpoint for hierarchy embeddings.",
    )

    @field_validator(
        "embedding_host_categories",
        "embedding_host_tasks",
        "embedding_host_hierarchies",
        mode="before",
    )
    @classmethod
    def _validate_tei_endpoint(cls, value: str) -> str:
        """Require http(s) TEI endpoints without echoing values.

        Args:
            value: Candidate endpoint URL.

        Returns:
            Validated endpoint URL.

        Raises:
            ValueError: If the URL does not use http:// or https://.
        """
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            raise ValueError("TEI endpoint must use http:// or https://")
        try:
            parts = urlsplit(value)
        except ValueError:
            raise ValueError("TEI endpoint must use http:// or https://") from None
        if parts.username or parts.password or parts.fragment:
            raise ValueError("TEI endpoint must not include credentials or fragment")
        return value

    embedding_access_token: SecretStr | None = Field(
        default=None,
        alias="EMBEDDING_ACCESS_TOKEN",
        description="Optional bearer token for TEI endpoints.",
    )

    top_k_names: int = Field(
        default=1,
        ge=1,
        le=10,
        alias="TOP_K_NAMES",
        description="Default top-k for name results.",
    )
    top_k_categories: int = Field(
        default=5,
        ge=1,
        le=50,
        alias="TOP_K_CATEGORIES",
        description="Default top-k for category candidates.",
    )

    category_id_field: str = Field(
        default="category_id",
        alias="CATEGORY_ID_FIELD",
        min_length=1,
        description="Metadata column holding category IDs.",
    )
    category_name_field: str = Field(
        default="category_name",
        alias="CATEGORY_NAME_FIELD",
        min_length=1,
        description="Metadata column holding category names.",
    )

    batch_size_categories: int = Field(
        default=32,
        ge=1,
        le=128,
        alias="BATCH_SIZE_CATEGORIES",
        description="TEI batch size for category embeddings.",
    )
    batch_size_tasks: int = Field(
        default=32,
        ge=1,
        le=128,
        alias="BATCH_SIZE_TASKS",
        description="TEI batch size for task embeddings.",
    )


def load_runtime_settings() -> RuntimeSettings:
    """Load immutable runtime settings from the environment.

    Returns:
        A frozen :class:`RuntimeSettings` instance.

    Raises:
        ValidationError: If required env vars are missing or invalid.
            Secret values stay redacted by ``SecretStr``.
    """
    return RuntimeSettings()
