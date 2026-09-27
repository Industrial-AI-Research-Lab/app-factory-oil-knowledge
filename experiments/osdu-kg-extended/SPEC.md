# Extended Volve graph

User-approved scope: download the data required for production charts and a map,
build and test repeatable ingestion scripts here, create and populate a new graph.

Target: `osdu-volve-extended` at the existing FalkorDB deployment. Source graph
`osdu-volve` is read-only and its 66 nodes and 55 relationships must be preserved.
No credentials in source files, output reports, download manifests or logs.

Sources: source graph snapshot; Equinor Volve production workbook from a pinned
public archive mirror (explicit provenance); official SODIR wellbore point and
field polygon GeoJSON in EPSG:4326; official field monthly production JSON.
Every remote payload is accepted only when its SHA-256 matches the pinned source
specification. Do not download unrelated countries or multi-terabyte seismic data.

Model: retain Well/Wellbore/WellLog IDs; add Field, ProductionRecord and Dataset.
ProductionRecord is a documented local extension, not a claimed standard OSDU
kind. Every record has an entity, source, reporting interval and stable ID.
Daily wellbore, monthly wellbore and monthly field records remain distinct.
Geographic positions are surface locations, not bottom-hole trajectories.

Pipeline states: downloaded -> prepared -> loading -> verified. A failed stage
must return nonzero and never claim a complete graph. Before any writes validate
all input records and references. Resume uses MERGE on stable IDs; no deletions.
Fail if the target is the source graph or is an unrelated existing graph.

Acceptance:
- Raw downloads have timestamps, URLs and pinned SHA256 checksums; a mismatched
  download never replaces the last validated local copy; offline rebuild works.
- Workbook headers/units and SODIR completeness are validated; NULL != zero.
- NPD IDs, not fuzzy names, connect production and geography to wellbores.
- Coordinates and periods are valid; unknown IDs fail before graph writes.
- Original source values retained; negative corrections are not silently clipped.
- Unit tests cover parsing, malformed input, units, joins, safety and idempotent IDs.
- Live verification compares graph counts, properties, links and volume sums
  against prepared data; repeat load produces the same graph counts.
- README supplies commands, schema, provenance, limitations and example queries.
- Python execution/testing uses an ordinary disposable Docker container; files remain below this folder.
