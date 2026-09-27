import json
from ast import literal_eval
import argparse
from pathlib import Path
import logging
import pandas as pd

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy import create_engine

from stairs_semantic_mapping.loading.utils import parse_args_for_db_loading
from stairs_semantic_mapping.loading.works.utils import build_sqlalchemy_db_url, vector_literal
from stairs_semantic_mapping.utils.settings import settings

from stairs_semantic_mapping.loading.works.works_data_preparation import prepare_data_for_loading
from stairs_semantic_mapping.infrastructure import TEIEmbeddingModel, NormalizingEmbeddingModel
from stairs_semantic_mapping.loading.works.run_works_init_sql import run_init_sql

logger = logging.getLogger(__name__)

SQL_INSERT_HIERARCHIES = """
INSERT INTO semantic_hierarchical_works (code, name, code_hierarchical_work_type, embedding)
VALUES (:code, :name, CAST(:code_hierarchical_work_type AS INT), CAST(:embedding AS vector))
"""


def load_standard_hierarchies_sa(engine: Engine, standard_hierarchies_data: pd.DataFrame, *, chunk_size: int = 2000) -> int:
    if standard_hierarchies_data is None:
        logger.warning("[HIERARCHIES] Hierarchical items list is empty — nothing to load")
        return 0
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1 or chunk_size > 10000:
        raise ValueError("chunk_size must be an int in [1, 10000]")
    logger.info("[HIERARCHIES] Loading %d hierarchical items", len(standard_hierarchies_data))

    params = [
        {
            "code": standard_hierarchies_data.loc[i, "code"],
            "name": standard_hierarchies_data.loc[i, "name"],
            "code_hierarchical_work_type": standard_hierarchies_data.loc[i, "code_hierarchical_work_types"],
            "embedding": vector_literal(standard_hierarchies_data.loc[i, "embeddings"]),
        }
        for i in range(len(standard_hierarchies_data))
    ]

    inserted = 0
    with engine.begin() as conn:
        for start in range(0, len(params), chunk_size):
            chunk = params[start : start + chunk_size]
            conn.execute(text(SQL_INSERT_HIERARCHIES), chunk)
            inserted += len(chunk)

    logger.info("[HIERARCHIES] Inserted %d rows", inserted)
    return inserted


def main():
    args = parse_args_for_db_loading()

    # Load data
    logger.info(f"[LOAD DATA] Reading file: {args.data_file}")

    standard_hierarchies_df = pd.read_csv(args.data_file)
    standard_hierarchies_df["code_hierarchical_work_types"] = standard_hierarchies_df["code_hierarchical_work_types"].apply(str)
    standard_hierarchies_df["embeddings"] = standard_hierarchies_df["embeddings"].apply(literal_eval)
    db_url = build_sqlalchemy_db_url(settings.db_config.model_dump())
    engine = create_engine(db_url)

    if args.create_tables:
        logger.info(f"[TABLES CREATION] Creating tables from init file: {args.create_tables}")
        run_init_sql(engine, args.create_tables)

    logger.info(f"[DATA LOADING] Start loading hierarchies data")
    load_standard_hierarchies_sa(engine, standard_hierarchies_df)
    logger.info(f"[DATA LOADING] Hierarchies data loading finished")

    logger.info(f"[SUMMARY] Data uploaded successfully")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    main()
