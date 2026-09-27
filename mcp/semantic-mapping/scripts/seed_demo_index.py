"""Seed a throwaway local pgvector index for Semantic MCP level-3 checks.

Builds the exact tables ``semantic_mapping_adapter`` queries
(``granular_category``, ``granular_name``, ``semantic_hierarchical_works``)
and fills them with TEI-computed, client-normalized embeddings of 2 demo
domains (concrete vs earthworks). Candidates are deliberately far apart so
nearest-neighbor checks are robust to any reasonable embedding model.

Needs: Postgres with pgvector, a TEI server, ``pip install psycopg2-binary requests``.

Usage:
    DEMO_DSN="postgresql://sem:sem123@127.0.0.1:5433/semantic" \\
    DEMO_TEI="http://127.0.0.1:8081/embed" \\
    python seed_demo_index.py
"""


import math
import os
import sys

DSN = os.environ.get("DEMO_DSN", "postgresql://sem:sem123@127.0.0.1:5433/semantic")
TEI = os.environ.get("DEMO_TEI", "http://127.0.0.1:8081/embed")

CATEGORIES = ["Concrete works", "Earthworks", "Roofing works", "Finishing works"]
NAMES = [
    # (name, category_id, category_name, task_code, measurement)
    ("Foundation pouring", 1, "Concrete works", "T1", "m3"),
    ("Concrete columns", 1, "Concrete works", "T2", "m3"),
    ("Trench excavation", 2, "Earthworks", "T3", "m3"),
    ("Soil compaction", 2, "Earthworks", "T4", "m2"),
    ("Roof framing", 3, "Roofing works", "T5", "m2"),
    ("Roof insulation", 3, "Roofing works", "T6", "m2"),
    ("Wall painting", 4, "Finishing works", "T7", "m2"),
    ("Floor tiling", 4, "Finishing works", "T8", "m2"),
]
HIERARCHIES = [
    # (name, code, type): two levels to exercise the level filter.
    ("Concrete works", "H1", 1),
    ("Earthworks", "H2", 1),
    ("Roofing works", "H3", 2),
    ("Finishing works", "H4", 2),
]


def _embed(texts: list[str]) -> list[list[float]]:
    import requests

    vectors: list[list[float]] = []
    for i in range(0, len(texts), 8):
        resp = requests.post(TEI, json={"inputs": texts[i : i + 8]}, timeout=120)
        resp.raise_for_status()
        vectors.extend(resp.json())
    out = []
    for vec in vectors:
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        out.append([x / norm for x in vec])
    return out


def main() -> None:
    try:
        import psycopg2
    except ImportError:
        print("need psycopg2-binary: pip install psycopg2-binary", file=sys.stderr)
        raise SystemExit(2)
    cat_vecs = _embed(CATEGORIES)
    name_vecs = _embed([n[0] for n in NAMES])
    hier_vecs = _embed([h[0] for h in HIERARCHIES])
    dim = len(cat_vecs[0])
    conn = psycopg2.connect(DSN)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS granular_category "
                    f"(id SERIAL PRIMARY KEY, category_name TEXT, embedding vector({dim}))"
                )
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS granular_name (id SERIAL PRIMARY KEY, name TEXT, "
                    f"category_id INT, category_name TEXT, task_code TEXT, measurement TEXT, "
                    f"embedding vector({dim}))"
                )
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS semantic_hierarchical_works (id SERIAL PRIMARY KEY, "
                    f"name TEXT, code TEXT, code_hierarchical_work_type INT, embedding vector({dim})"
                    f")"
                )
                cur.execute("DELETE FROM granular_category")
                cur.execute("DELETE FROM granular_name")
                cur.execute("DELETE FROM semantic_hierarchical_works")

                def lit(vec: list[float]) -> str:
                    return "[" + ",".join(repr(x) for x in vec) + "]"

                for name, vec in zip(CATEGORIES, cat_vecs):
                    cur.execute(
                        "INSERT INTO granular_category (category_name, embedding) VALUES (%s, %s)",
                        (name, lit(vec)),
                    )
                for (name, cid, cname, code, meas), vec in zip(NAMES, name_vecs):
                    cur.execute(
                        "INSERT INTO granular_name (name, category_id, category_name, task_code, "
                        "measurement, embedding) VALUES (%s, %s, %s, %s, %s, %s)",
                        (name, cid, cname, code, meas, lit(vec)),
                    )
                for (name, code, typ), vec in zip(HIERARCHIES, hier_vecs):
                    cur.execute(
                        "INSERT INTO semantic_hierarchical_works (name, code, "
                        "code_hierarchical_work_type, embedding) VALUES (%s, %s, %s, %s)",
                        (name, code, typ, lit(vec)),
                    )
    finally:
        conn.close()
    print(f"seeded dim={dim} at {DSN.split('@')[-1]}")


if __name__ == "__main__":
    main()
