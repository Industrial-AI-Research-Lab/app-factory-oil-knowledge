"""Direct contracts for shared adapter helpers."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from semantic_mapping_adapter._common import require_top_k, to_distances, unwrap_tei_token  # noqa: E402
from semantic_mapping_adapter import RuntimeSettings  # noqa: E402


def _settings(**ov: Any) -> RuntimeSettings:
    base: dict[str, Any] = {
        "DB_CONFIG": {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"},
    }
    base.update(ov)
    return RuntimeSettings.model_validate(base)


@pytest.mark.parametrize("good", [1, 5, 10])
def test_require_top_k_bounds_ok(good):
    assert require_top_k(good) == good


@pytest.mark.parametrize("bad", [0, 11, -1, True, False, "5", 2.5, None])
def test_require_top_k_rejects(bad):
    with pytest.raises(ValueError, match=r"top_k must be an int"):
        require_top_k(bad)


def test_unwrap_tei_token():
    assert unwrap_tei_token(_settings()) is None
    assert unwrap_tei_token(_settings(EMBEDDING_ACCESS_TOKEN="tok")) == "tok"


def test_to_distances_forms():
    assert to_distances(["0.5", 2]) == (0.5, 2.0)
    assert to_distances((1,)) == (1.0,)
    assert to_distances([]) == ()


@pytest.mark.parametrize("bad", [[None], [object()], ["x"]])
def test_to_distances_rejects(bad):
    with pytest.raises(ValueError, match="cardinalities") as exc:
        to_distances(bad)
    assert "object at" not in str(exc.value)
