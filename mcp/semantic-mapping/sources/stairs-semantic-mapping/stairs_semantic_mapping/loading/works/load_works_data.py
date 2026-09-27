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

SQL_INSERT_CATEGORIES = """
INSERT INTO granular_category (id, category_name, embedding)
VALUES (:id, :category_name, CAST(:embedding AS vector))
"""

SQL_INSERT_NAMES = """
INSERT INTO granular_name (task_code, name, measurement, category_id, category_name, embedding)
VALUES (:task_code, :name, :measurement, :category_id, :category_name, CAST(:embedding AS vector))
"""


def load_granular_categories_sa(engine: Engine, granular_categories_data: list[dict], *, chunk_size: int = 2000) -> int:
    if granular_categories_data is None:
        logger.warning("[CATEGORIES] Granular categories list is empty — nothing to load")
        return 0
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1 or chunk_size > 10000:
        raise ValueError("chunk_size must be an int in [1, 10000]")
    logger.info("[CATEGORIES] Loading %d categories", len(granular_categories_data))
    if not granular_categories_data:
        logger.warning("[CATEGORIES] Granular categories list is empty — nothing to load")
        return 0

    params = [
        {
            "id": item.get("category_code"),
            "category_name": item.get("category_name"),
            "embedding": vector_literal(item.get("category_embedding")),
        }
        for item in granular_categories_data
    ]

    inserted = 0
    with engine.begin() as conn:
        for start in range(0, len(params), chunk_size):
            chunk = params[start : start + chunk_size]
            conn.execute(text(SQL_INSERT_CATEGORIES), chunk)
            inserted += len(chunk)

    logger.info("[CATEGORIES] Inserted %d rows", inserted)
    return inserted


def load_granular_names_sa(engine: Engine, granular_names_data: list[dict], *, chunk_size: int = 2000) -> int:
    if granular_names_data is None:
        logger.warning("[NAMES] Granular names list is empty — nothing to load")
        return 0
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size < 1 or chunk_size > 10000:
        raise ValueError("chunk_size must be an int in [1, 10000]")
    logger.info("[NAMES] Loading %d names", len(granular_names_data))
    if not granular_names_data:
        logger.warning("[NAMES] Granular names list is empty — nothing to load")
        return 0

    params = [
        {
            "task_code": item.get("task_code"),
            "name": item.get("task_name"),
            "measurement": item.get("measurement"),
            "category_id": item.get("category_code"),
            "category_name": item.get("category_name"),
            "embedding": vector_literal(item.get("task_embedding")),
        }
        for item in granular_names_data
    ]

    inserted = 0
    with engine.begin() as conn:
        for start in range(0, len(params), chunk_size):
            chunk = params[start : start + chunk_size]
            conn.execute(text(SQL_INSERT_NAMES), chunk)
            inserted += len(chunk)

    logger.info("[NAMES] Inserted %d rows", inserted)
    return inserted


def main():
    args = parse_args_for_db_loading()

    # Load data
    logger.info(f"[LOAD DATA] Reading file: {args.data_file}")

    if args.data_file.suffix.lower() == ".csv":
        standard_tasks_df = pd.read_csv(args.data_file, sep=';')

        # Create embedders for categories and tasks names
        cat_emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_categories, access_token=settings.embedding_access_token)
        cat_emb_model = NormalizingEmbeddingModel(cat_emb_model_base)  # model with L2-normalized embeddings

        names_emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_tasks, access_token=settings.embedding_access_token)
        names_emb_model = NormalizingEmbeddingModel(names_emb_model_base)  # model with L2-normalized embeddings

        # Prepare data with embeddings for upload in JSON format
        logger.info(f"[DATA PREPARATION] Categories and names embeddings creation started")
        data_for_upload = prepare_data_for_loading(standard_tasks_df, names_emb_model, cat_emb_model)
        logger.info(f"[DATA PREPARATION] Categories and names embeddings creation finished")
    elif args.data_file.suffix.lower() == ".json":
        with open(args.data_file) as json_file:
            data_for_upload = json.load(json_file)
    else:
        logger.error(f"[LOAD DATA] Unsupported file type: {args.data_file.suffix}")
        return

    granular_categories_lst = data_for_upload.get("categories", [])
    granular_names_lst = data_for_upload.get("names", [])

    for cat_info in granular_categories_lst:
        cat_info['category_embedding'] = [literal_eval(x) for x in cat_info['category_embedding']]
    for task_info in granular_names_lst:
        task_info['task_embedding'] = [literal_eval(x) for x in task_info['task_embedding']]

    db_url = build_sqlalchemy_db_url(settings.db_config.model_dump())
    engine = create_engine(db_url)

    if args.create_tables:
        logger.info(f"[TABLES CREATION] Creating tables from init file: {args.create_tables}")
        run_init_sql(engine, args.create_tables)

    logger.info(f"[DATA LOADING] Start loading works categories data")
    load_granular_categories_sa(engine, granular_categories_lst)
    logger.info(f"[DATA LOADING] Works categories data loading finished")

    logger.info(f"[DATA LOADING] Start loading works names data")
    load_granular_names_sa(engine, granular_names_lst)
    logger.info(f"[DATA LOADING] Works names data loading finished")

    logger.info(f"[SUMMARY] Data uploaded successfully")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    main()
