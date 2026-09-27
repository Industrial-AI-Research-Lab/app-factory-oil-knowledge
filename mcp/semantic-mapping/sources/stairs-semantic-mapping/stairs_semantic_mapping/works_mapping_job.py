from protollm_sdk.jobs.job import Job
from protollm_sdk.jobs.job_context import JobContext

from stairs_semantic_mapping.infrastructure import TEIEmbeddingModel, NormalizingEmbeddingModel
from stairs_semantic_mapping.infrastructure import PostgresConnectionProvider, PgVectorStore, PgVectorStoreConfig
from stairs_semantic_mapping.mapping import HierarchicalMapperConfig, HierarchicalMapper
from stairs_semantic_mapping.utils import (InputWorkMappingUnit, OutputWorkMappingUnit,
                                           StandardWorkUnit, OutputWorkMappingResult)
from stairs_semantic_mapping.utils import settings


class WorksMappingJob(Job):
    def run(self, job_id: str, ctx: JobContext,
            works_for_mapping: list[InputWorkMappingUnit],
            top_k: int = 5, **kwargs):
        # Embedding models
        cat_emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_categories, access_token=settings.embedding_access_token)
        cat_emb_model = NormalizingEmbeddingModel(cat_emb_model_base)  # model with L2-normalized embeddings

        names_emb_model_base = TEIEmbeddingModel(host=settings.embedding_host_tasks, access_token=settings.embedding_access_token)
        names_emb_model = NormalizingEmbeddingModel(names_emb_model_base)  # model with L2-normalized embeddings

        # PostgreSQL connection provider
        pg_conn_provider = PostgresConnectionProvider(settings.db_config.model_dump())

        # Prepare config for the access to categories table in db
        # and create category vector store object for semantic queries
        cat_store_config = PgVectorStoreConfig(table_name="granular_category", text_column="category_name")
        cat_vector_store = PgVectorStore(conn_provider=pg_conn_provider, config=cat_store_config)

        # Prepare config for the access to task names table in db
        # and create names vector store object for semantic queries
        names_store_config = PgVectorStoreConfig(table_name="granular_name", text_column="name",
                                                 metadata_columns=["category_id", "category_name", "task_code",
                                                                   "measurement"])
        names_vector_store = PgVectorStore(conn_provider=pg_conn_provider, config=names_store_config)

        # Create mapper config
        mapper_config = HierarchicalMapperConfig(category_id_field="category_id",
                                                 category_name_field="category_name",
                                                 measurement_field="measurement")

        mapper = HierarchicalMapper(category_embedder=cat_emb_model, name_embedder=names_emb_model,
                                    category_store=cat_vector_store, task_store=names_vector_store,
                                    config=mapper_config)

        # Input data preparation
        work_names_for_mapping = [work_info.work_name + ', ' + work_info.work_measurement + ' || ' +
                                  ' || '.join([x for x in [work_info.bwd_name, work_info.ose_name, work_info.occ_name]
                                               if x is not None]) for work_info in works_for_mapping]

        result_dict = mapper.semantic_search(inputs=work_names_for_mapping, top_k=top_k, with_distances=True)

        # Result formatting
        standard_work_units = [tuple([StandardWorkUnit(**{"code": result_dict['ids'][i][j],
                                                          "name": result_dict['names'][i][j],
                                                          "measurement": result_dict['measurements'][i][j],
                                                          "category": result_dict['categories'][i][j]}) for j in
                                      range(top_k)]) for i in range(len(work_names_for_mapping))]
        distances = [tuple(x) for x in result_dict['scores']]

        # Prepare final result
        result = []
        for i in range(len(work_names_for_mapping)):
            result.append(OutputWorkMappingUnit(**works_for_mapping[i].model_dump(),
                                                top_k_units=standard_work_units[i],
                                                top_k_distances=distances[i]))

        # Save result to Redis
        ctx.result_storage.save_dict(job_id, OutputWorkMappingResult(result=result).model_dump())
