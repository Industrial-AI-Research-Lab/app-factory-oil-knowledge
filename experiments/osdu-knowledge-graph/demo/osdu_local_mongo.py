"""Local-only read of the persisted OSDU user-facing answer."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from motor.motor_asyncio import AsyncIOMotorClient


GRAPH_DEMO_HELPERS = Path(__file__).resolve().parents[3] / "docs" / "graph-db-poc" / "demo"
if str(GRAPH_DEMO_HELPERS) not in sys.path:
    sys.path.insert(0, str(GRAPH_DEMO_HELPERS))

from local_api import LocalDemoError
from local_mongo import require_local_mongodb_uri


LOGGER = logging.getLogger(__name__)
USER_ANSWER_KEY = "osdu_user_answer"


async def load_analysis_output(
    project_id: str,
    *,
    environ: Mapping[str, str] = os.environ,
    key: str = USER_ANSWER_KEY,
) -> Any:
    uri = environ.get("MONGODB_URI", "").strip()
    database = environ.get("MONGODB_DATABASE", "").strip()
    if not uri or not database:
        raise LocalDemoError("MONGODB_URI and MONGODB_DATABASE are required")
    require_local_mongodb_uri(uri)
    client = AsyncIOMotorClient(
        uri, serverSelectionTimeoutMS=5000, directConnection=True
    )
    try:
        doc = await client[database].projects.find_one(
            {"project_id": project_id},
            {
                f"context.custom_context.{key}": 1,
                "_id": 0,
            },
        )
    except Exception:
        raise LocalDemoError(
            f"local MongoDB output read failed for project {project_id}"
        ) from None
    finally:
        try:
            client.close()
        except Exception:
            LOGGER.warning("[OSDU_DEMO] mongodb_close_failed project_id=%s", project_id)
    context = doc.get("context") if isinstance(doc, dict) else None
    custom = context.get("custom_context") if isinstance(context, dict) else None
    if not isinstance(custom, dict):
        raise LocalDemoError(f"project {project_id} did not persist {key}")
    output = custom.get(key)
    if output in (None, "", {}):
        raise LocalDemoError(f"project {project_id} did not persist {key}")
    return output
