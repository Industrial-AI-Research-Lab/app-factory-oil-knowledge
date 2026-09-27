import chromadb
from chromadb.utils.embedding_functions import HuggingFaceEmbeddingServer
from transformers import AutoTokenizer
from typing import List, Dict, Union
import pandas as pd

from stairs_semantic_mapping.utilities import fix_documents_size
from stairs_semantic_mapping.utils.settings import settings, MappingObjects


def get_chroma_collection(collection_name: str,
                          chroma_host: str = settings.chroma_host,
                          chroma_port: int = settings.chroma_port,
                          embedding_host: str = settings.embedding_host,
                          distance_fn: str = settings.distance_fn):

    chroma_client = chromadb.HttpClient(host=chroma_host, port=chroma_port)
    embedding_function = HuggingFaceEmbeddingServer(url=embedding_host)

    standard_names_collection = chroma_client.get_or_create_collection(collection_name,
                                                                       metadata={
                                                                           "hnsw:space": distance_fn},
                                                                       embedding_function=embedding_function)

    return standard_names_collection


def get_tokenizer(embedding_name: str):
    return AutoTokenizer.from_pretrained(embedding_name)


def fill_chroma_collection(chroma_collection: chromadb.Collection,
                           documents_to_add: List[Dict[str, Union[str, Dict]]],
                           tokenizer,
                           embedding_size: int = settings.embedding_size,
                           embedding_batch_size: int = settings.embedding_batch_size,
                           rewrite_collection: bool = False) -> None:
    # Prepare information for standard task names (with meta if necessary)
    documents_texts = []
    for document in documents_to_add:
        if 'meta' in document:
            document_text = document['text'] + '&&' + document['meta']
        else:
            document_text = document['text']
        documents_texts.append(document_text)

    # Fix standard names' length (with meta info) to fit the embedder requirements
    documents_texts = fix_documents_size(documents_texts, embedding_size, tokenizer)

    # Split standard names' in batches for the correct processing by embedder
    docs_by_batches = [documents_texts[i:i + embedding_batch_size]
                       for i in range(0, len(documents_texts), embedding_batch_size)]

    # Add documents to collection
    if rewrite_collection:
        document_ids = chroma_collection.get()['ids']
        if len(document_ids) > 0:
            chroma_collection.delete(document_ids)
        iteration = 0
    else:
        iteration = chroma_collection.count()
    for batch in docs_by_batches:
        chroma_collection.add(
            documents=batch,
            ids=[str(iteration * embedding_batch_size + x) for x in range(len(batch))]
        )
        iteration += 1


def update_names_collection(collection_name: str,
                            standard_names_to_upload: List[Dict[str, Union[str, Dict]]]):
    chroma_collection = get_chroma_collection(collection_name)
    fill_chroma_collection(chroma_collection=chroma_collection,
                           documents_to_add=standard_names_to_upload,
                           tokenizer=get_tokenizer(settings.embedding_name),
                           rewrite_collection=True)


def update_task_names_collection(standard_names_to_upload: List[Dict[str, Union[str, Dict]]],
                                 collection_name: str = MappingObjects.TASKS.value):
    return update_names_collection(collection_name, standard_names_to_upload)


def update_resource_names_collection(standard_names_to_upload: List[Dict[str, Union[str, Dict]]],
                                     collection_name: str = MappingObjects.RESOURCES.value):
    return update_names_collection(collection_name, standard_names_to_upload)


def update_measurement_names_collection(standard_names_to_upload: List[Dict[str, Union[str, Dict]]],
                                        collection_name: str = MappingObjects.MEASUREMENTS.value):
    return update_names_collection(collection_name, standard_names_to_upload)


if __name__ == "__main__":
    # Get standard task names from file
    with open('../../data/standard_task_names_v050724.txt', encoding='utf-8') as f:
        standard_names = f.readlines()
    standard_names = [x.replace('\n', '') for x in standard_names]

    standard_task_names = [{'text': standard_name} for standard_name in standard_names]
    update_task_names_collection(standard_task_names, collection_name='test_task_names_collection')
