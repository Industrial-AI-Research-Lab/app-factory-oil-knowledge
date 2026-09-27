from typing import Optional, Iterable
from urllib.parse import quote_plus


def build_sqlalchemy_db_url(db_config: dict) -> str:
    """
    Build SQLAlchemy database URL from DB_CONFIG dict.

    Expected keys:
      - host
      - port
      - dbname
      - user
      - password
    """
    user = quote_plus(db_config["user"])
    password = quote_plus(db_config["password"])
    host = db_config["host"]
    port = db_config["port"]
    dbname = db_config["dbname"]

    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"


def vector_literal(vec: Optional[Iterable[float]]) -> Optional[str]:
    """Convert list/iterable of floats to pgvector literal string: "[...]"."""
    if vec is None:
        return None
    lst = list(vec)
    return "[" + ",".join(str(float(x)) for x in lst) + "]"

