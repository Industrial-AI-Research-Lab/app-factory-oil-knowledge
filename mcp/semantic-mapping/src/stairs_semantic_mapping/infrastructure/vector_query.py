from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Sequence, Protocol
from psycopg2 import sql
import numpy as np

from stairs_semantic_mapping.infrastructure import ConnectionProvider


@dataclass
class PgVectorStoreConfig:
    table_name: str
    id_column: str = "id"
    text_column: Optional[str] = None
    embedding_column: str = "embedding"
    metadata_columns: Optional[List[str]] = None


class VectorQueryResult:
    def __init__(
        self,
        ids: List[List[str]],
        results: List[List[str]],
        distances: Optional[List[List[float]]] = None,
        metadatas: Optional[List[List[Dict[str, Any]]]] = None,
    ):
        self.ids = ids
        self.results = results
        self.distances = distances or []
        self.metadatas = metadatas or []


class VectorStore(Protocol):
    """
    Protocol for a read-only vector storage backend.

    Any implementation must provide a ``query`` method that performs
    similarity search over stored embeddings with optional filtering
    and returns a ``VectorQueryResult``.
    """
    def query(
        self,
        query_embeddings,
        top_k: int,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[List[str]] = None,
    ) -> VectorQueryResult: ...


class PgVectorStore:
    def __init__(
        self,
        conn_provider: ConnectionProvider,
        config: PgVectorStoreConfig,
    ):
        self.conn_provider = conn_provider
        self.config = config

    def _build_where_clause(
        self,
        where: Optional[Dict[str, Sequence[Any]]],
    ) -> (sql.SQL, List[Any]):
        """
            Builds an SQL WHERE clause using simple equality filters with ANY().

            Each entry in ``where`` is expected to be:
                column_name -> list of allowed values

            Example:
                {"cat_id": [1, 2, 3], "project_id": [10]}

            Resulting SQL:
                WHERE cat_id = ANY(%s) AND project_id = ANY(%s)

            Empty lists are ignored. If all values are empty or ``where`` is None,
            no WHERE clause is added.

            Returns
            -------
            tuple (where_sql, params)
                where_sql : psycopg2.sql.SQL
                    Composed SQL fragment (empty if no conditions)
                params : list
                    Parameter list containing the value arrays for each column
            """
        if not where:
            return sql.SQL(""), []

        clauses: List[sql.SQL] = []
        params: List[Any] = []

        for col_name, values in where.items():
            if not values:
                continue

            clauses.append(
                sql.SQL("{} = ANY(%s)").format(sql.Identifier(col_name))
            )
            params.append(list(values))

        if not clauses:
            return sql.SQL(""), []

        where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
        return where_sql, params

    def query(
            self,
            query_embeddings: np.ndarray | Sequence[Sequence[float]],
            top_k: int,
            where: Optional[Dict[str, Sequence[Any]]] = None,
            include: Optional[List[str]] = None,
    ) -> VectorQueryResult:
        """
        Executes a similarity search for a batch of query embeddings
        over a table with a pgvector extension.

        Parameters
        ----------
        query_embeddings : array-like of shape (N, dim)
            One or multiple embedding vectors. Scalars are reshaped to (1, dim).

        top_k : int
            Number of nearest results to return per query.

        where : dict[str, Sequence[Any]] | None
            Optional filtering conditions. Each key is a column name and each
            value is a list of allowed values. Every condition is translated into:
                column = ANY(%s)
            Multiple filters are combined using AND.
            Example:
                {"cat_id": [1, 2, 3], "project_id": [10]}

        include : list[str] | None
            Controls which additional fields to return:
                "results"   -> return text_column values
                "distances" -> return embedding <-> query distances
                "metadatas" -> return metadata columns from config

        Returns
        -------
        VectorQueryResult
            Structured result containing:
                - ids:      list[list[str]]
                - results:  list[list[str]]
                - distances (optional)
                - metadatas (optional)

        Notes
        -----
        • One SQL query is executed per query vector.
        • Embeddings are passed as pgvector-compatible Python lists.
        • WHERE conditions and extra columns are composed dynamically
          based on store configuration and 'include' flags.
        """
        # Convert input ndarray to list with float types (compatible with PostgreSQL)
        queries = [x.astype(float).tolist() for x in query_embeddings]

        include = include or []

        ids_out: List[List[str]] = []
        results_out: List[List[str]] = []
        dists_out: List[List[float]] = []
        metas_out: List[List[Dict[str, Any]]] = []

        # Build SELECT columns
        select_cols = [sql.Identifier(self.config.id_column)]

        if self.config.text_column and "results" in include:
            select_cols.append(sql.Identifier(self.config.text_column))  # add text_column to select

        # Note:
        # We intentionally use the L2 operator (<->) instead of the cosine operator (<=>).
        # Since all embeddings (both stored and query-time) are L2-normalized, similarity search
        # under L2 distance is mathematically equivalent to cosine similarity search.
        # (The proof of this equivalence is left to the reader as an exercise.)
        if "distances" in include:
            select_cols.append(
                sql.SQL(
                    "{emb} <=> %s::vector AS distance"
                ).format(emb=sql.Identifier(self.config.embedding_column))
            )

        if self.config.metadata_columns and "metadatas" in include:
            for col in self.config.metadata_columns:
                select_cols.append(sql.Identifier(col))

        base_query = (
                sql.SQL("SELECT ")
                + sql.SQL(", ").join(select_cols)
                + sql.SQL(" FROM ")
                + sql.Identifier(self.config.table_name)
        )

        # WHERE clause
        where_sql, where_params = self._build_where_clause(where)

        order_limit_sql = sql.SQL(
            " ORDER BY {emb} <-> %s::vector LIMIT %s"
        ).format(
            emb=sql.Identifier(self.config.embedding_column)
        )

        full_query = base_query + where_sql + order_limit_sql

        # Execute one SQL call per embedding
        with self.conn_provider.get_cursor() as cur:
            for emb in queries:
                params: List[Any] = []

                if "distances" in include:
                    params.append(list(emb))  # distance in SELECT

                params.extend(where_params)  # WHERE conditions

                params.append(list(emb))  # distance in ORDER BY
                params.append(top_k)  # LIMIT

                cur.execute(full_query, params)
                rows = cur.fetchall()

                batch_ids: List[str] = []
                batch_results: List[str] = []
                batch_dists: List[float] = []
                batch_metas: List[Dict[str, Any]] = []

                for row in rows:
                    batch_ids.append(str(row[self.config.id_column]))

                    if self.config.text_column and "results" in include:
                        batch_results.append(str(row[self.config.text_column]))

                    if "distances" in include:
                        batch_dists.append(float(row["distance"]))

                    if "metadatas" in include and self.config.metadata_columns:
                        meta = {col: row[col] for col in self.config.metadata_columns}
                        batch_metas.append(meta)

                ids_out.append(batch_ids)
                results_out.append(batch_results)

                if "distances" in include:
                    dists_out.append(batch_dists)
                if "metadatas" in include:
                    metas_out.append(batch_metas)

        return VectorQueryResult(
            ids=ids_out,
            results=results_out,
            distances=dists_out if dists_out else None,
            metadatas=metas_out if metas_out else None,
        )
