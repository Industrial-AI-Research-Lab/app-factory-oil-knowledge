from protollm_sdk.jobs.job import Job
from protollm_sdk.jobs.job_context import JobContext

from stairs_semantic_mapping.infrastructure import TEIEmbeddingModel, NormalizingEmbeddingModel
from stairs_semantic_mapping.infrastructure import PostgresConnectionProvider, PgVectorStore, PgVectorStoreConfig
from stairs_semantic_mapping.mapping import SimpleMapperConfig, SimpleMapper
from stairs_semantic_mapping.utils import (StandardHierarchyUnit,
                                           OutputHierarchyMappingUnit, OutputHierarchyMappingResult)
from stairs_semantic_mapping.utils import settings


class HierarchiesMappingJob(Job):
    def run(self, job_id: str, ctx: JobContext,
            hierarchies_for_mapping: list[str],
            level: int,
            top_k: int = 5, **kwargs):
        # Embedding model
        emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_hierarchies, access_token=settings.embedding_access_token)
        emb_model = NormalizingEmbeddingModel(emb_model_base)  # model with L2-normalized embeddings

        # PostgreSQL connection provider
        pg_conn_provider = PostgresConnectionProvider(settings.db_config.model_dump())

        # Prepare config for the access to task names table in db
        # and create names vector store object for semantic queries
        hierarchies_store_config = PgVectorStoreConfig(table_name="semantic_hierarchical_works", text_column="name",
                                                       metadata_columns=["code"])
        vector_store = PgVectorStore(conn_provider=pg_conn_provider, config=hierarchies_store_config)

        # Create mapper config
        config = SimpleMapperConfig(top_k=top_k,
                                    where_clause_fields={"code_hierarchical_work_type": [level]},
                                    metadata_fields=["code"])

        mapper = SimpleMapper(embedder=emb_model, store=vector_store, config=config)
        result_dict = mapper.semantic_search(inputs=hierarchies_for_mapping, top_k=top_k, with_distances=True)

        codes_lst = [[unit_code['code'] for unit_code in top_k_units_codes] for top_k_units_codes in
                     result_dict['metadatas']]
        names_lst = result_dict['names']

        # Result formatting
        standard_hier_units = [tuple([StandardHierarchyUnit(**{"code": codes_lst[i][j],
                                                               "name": names_lst[i][j]}) for j in
                                      range(top_k)]) for i in range(len(hierarchies_for_mapping))]

        distances = [tuple(x) for x in result_dict['scores']]

        # Prepare final result
        result = []
        for i in range(len(hierarchies_for_mapping)):
            result.append(OutputHierarchyMappingUnit(hierarchical_work_name=hierarchies_for_mapping[i],
                                                     top_k_units=standard_hier_units[i],
                                                     top_k_distances=distances[i]))

        # Save result to Redis
        ctx.result_storage.save_dict(job_id, OutputHierarchyMappingResult(result=result).model_dump())
