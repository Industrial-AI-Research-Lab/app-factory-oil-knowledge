from typing import Union, List, Dict
import chromadb
from chromadb.utils.embedding_functions import HuggingFaceEmbeddingServer
from transformers import AutoTokenizer

from stairs_semantic_mapping.utilities import fix_documents_size
from stairs_semantic_mapping.utils.settings import settings, MappingObjects
from stairs_semantic_mapping.utils.data_loading import get_chroma_collection, get_tokenizer, fill_chroma_collection


class SemanticMapper:
    def __init__(self,
                 mapping_objects: MappingObjects = MappingObjects.TASKS,
                 standard_names: Union[List[Dict[str, Union[str, Dict]]], None] = None,
                 rewrite_collection: bool = False) -> None:
        '''
        Initializes semantic mapper

        :param standard_names: list of dictionaries with the required key 'text' (task or resource name)
            and optional key 'meta' with the dictionary with some additional information
        :param rewrite_collection:  boolean flag, indicating whether to use meta information or not
        '''

        self.tokenizer = AutoTokenizer.from_pretrained(settings.embedding_name)
        self.standard_names_collection = get_chroma_collection(collection_name=mapping_objects.value)

        if standard_names is not None:
            fill_chroma_collection(chroma_collection=self.standard_names_collection,
                                   documents_to_add=standard_names,
                                   tokenizer=self.tokenizer,
                                   rewrite_collection=rewrite_collection)

    def get_standard_names(self,
                           task_names: List[str],
                           n_results: int = 1,
                           with_distances: bool = False):

        # Fix task names' length to fit the embedder requirements
        task_names = fix_documents_size(task_names, settings.embedding_size, self.tokenizer)

        # Split standard names' in batches for the correct processing by embedder
        tasks_by_batches = [task_names[i:i + settings.embedding_batch_size]
                            for i in range(0, len(task_names), settings.embedding_batch_size)]

        standard_names_info = []
        for batch in tasks_by_batches:
            results = self.standard_names_collection.query(
                query_texts=batch,
                n_results=n_results
            )

            for i in range(len(results['documents'])):
                result = [x.split('&&')[0] for x in results['documents'][i]]
                if with_distances:
                    standard_names_info.append(
                        {'standard_names': result, 'distances': results['distances'][i]})
                else:
                    standard_names_info.append({'standard_names': result})

        return standard_names_info
