from pathlib import Path
from sqlalchemy import create_engine, text, Engine

from stairs_semantic_mapping.utils.settings import settings
from stairs_semantic_mapping.loading.works.utils import build_sqlalchemy_db_url

def run_init_sql(engine: Engine, sql_path: str) -> None:
    sql = Path(sql_path).read_text(encoding="utf-8")

    with engine.begin() as conn:
        conn.execute(text(sql))


if __name__ == "__main__":
    db_url = build_sqlalchemy_db_url(settings.db_config.model_dump())
    engine = create_engine(db_url)

    run_init_sql(engine, "hierarchies_init.sql")