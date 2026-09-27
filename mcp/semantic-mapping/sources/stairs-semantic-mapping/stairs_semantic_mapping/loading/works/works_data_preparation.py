import argparse
import json
from pathlib import Path
import logging
import pandas as pd

from stairs_semantic_mapping.infrastructure import TEIEmbeddingModel, NormalizingEmbeddingModel
from stairs_semantic_mapping.loading.utils import parse_args_for_data_preparation
from stairs_semantic_mapping.utils.settings import settings


logger = logging.getLogger(__name__)


def prepare_data_for_loading(standard_tasks_df: pd.DataFrame,
                             names_emb_model: NormalizingEmbeddingModel,
                             cat_emb_model: NormalizingEmbeddingModel) -> dict[str, list[dict]]:


    # Prepare list of categories names
    categories_list = sorted(set(standard_tasks_df['Category']))

    # Create dict with categories names and codes
    category_codes_lst = list(range(1, len(categories_list) + 1))
    categories_dict = dict(zip(categories_list, category_codes_lst))

    category_embeddings = cat_emb_model.embed(['passage: ' + name for name in categories_list])
    granular_categories_data = []
    for category_name, embedding in zip(categories_list, category_embeddings):
        granular_categories_data.append({'category_code': categories_dict[category_name],
                                         'category_name': category_name,
                                         'category_embedding': [str(x) for x in embedding]})

    # Prepare full info about task names, categories and two embedding arrays (with category and name models)
    granular_names_data = []
    task_texts = [
        'passage: ' + row['Work_name'] + ', ' + row['Measurement'] + ' || ' + row['Category']
        for _, row in standard_tasks_df.iterrows()
    ]
    task_embeddings = names_emb_model.embed(task_texts)
    for i, embedding in enumerate(task_embeddings):
        granular_names_data.append({'task_code': str(standard_tasks_df.loc[i, 'id']),
                                    'task_name': standard_tasks_df.loc[i, 'Work_name'],
                                    'measurement': standard_tasks_df.loc[i, 'Measurement'],
                                    'category_code': categories_dict[standard_tasks_df.loc[i, 'Category']],
                                    'category_name': standard_tasks_df.loc[i, 'Category'],
                                    'task_embedding': [str(x) for x in embedding]})
    data_for_upload = {'categories': granular_categories_data,
                       'names': granular_names_data}

    return data_for_upload


def main():
    args = parse_args_for_data_preparation()

    # Load data
    logger.info(f"[LOAD DATA] Reading CSV file: {args.data_files[0]}")
    standard_tasks_df = pd.read_csv(args.data_files[0], sep=';')

    # Create embedders for categories and tasks names
    cat_emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_categories, access_token=settings.embedding_access_token)
    cat_emb_model = NormalizingEmbeddingModel(cat_emb_model_base)  # model with L2-normalized embeddings

    names_emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_tasks, access_token=settings.embedding_access_token)
    names_emb_model = NormalizingEmbeddingModel(names_emb_model_base)  # model with L2-normalized embeddings

    # Prepare data with embeddings for upload in JSON format
    logger.info(f"[DATA PREPARATION] Categories and names embeddings creation started")
    data_for_upload = prepare_data_for_loading(standard_tasks_df, names_emb_model, cat_emb_model)
    logger.info(f"[DATA PREPARATION] Categories and names embeddings creation finished")

    if args.save_to:
        filename = args.save_to
    else:
        filename = 'data_for_loading.json'

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data_for_upload, f)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    main()
