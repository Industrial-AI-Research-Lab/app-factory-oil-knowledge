import sqlite3


def collection_payload(tmp_path):
    path = tmp_path / "collection.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            PRAGMA user_version = 1;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE nodes (node_id TEXT PRIMARY KEY, source_id TEXT, fragment_id TEXT);
            CREATE TABLE relations (relation_id TEXT PRIMARY KEY, source_node_id TEXT, target_node_id TEXT, source_id TEXT, fragment_id TEXT);
            CREATE TABLE observations (observation_id TEXT PRIMARY KEY);
            CREATE TABLE fragments (fragment_id TEXT PRIMARY KEY, source_id TEXT, locator TEXT, text TEXT);
            CREATE TABLE source_status (source_id TEXT PRIMARY KEY, filename TEXT, content_type TEXT, status TEXT);
            INSERT INTO metadata VALUES ('schema_version', '1');
            INSERT INTO metadata VALUES ('ontology_id', '"demo"');
            INSERT INTO metadata VALUES ('ontology_version', '"1.0.0"');
            INSERT INTO metadata VALUES ('index_sha256', '"a"');
            INSERT INTO metadata VALUES ('ontology_sha256', '"b"');
            INSERT INTO metadata VALUES ('fact_batch_sha256s', '[]');
            INSERT INTO metadata VALUES ('input_fingerprint', '"c"');
            INSERT INTO nodes VALUES ('node:one', 'source-a', 'fragment-a');
            INSERT INTO nodes VALUES ('node:two', 'source-b', 'fragment-b');
            INSERT INTO relations VALUES ('relation:one', 'node:one', 'node:two', 'source-a', 'fragment-a');
            """)
        connection.commit()
    finally:
        connection.close()
    return path.read_bytes()


def recursive_metadata_payload(tmp_path):
    path = tmp_path / "recursive.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            PRAGMA user_version = 1;
            CREATE VIEW metadata AS
                WITH RECURSIVE counter(value) AS (
                    SELECT 1 UNION ALL SELECT value + 1 FROM counter
                )
                SELECT 'schema_version' AS key, '1' AS value_json FROM counter;
            """)
        connection.commit()
    finally:
        connection.close()
    return path.read_bytes()
