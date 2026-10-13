# GenePulse — ClinVar Variant Reclassification Tracker

GenePulse ingests every monthly release of [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/)
(the public archive of "is this genetic variant disease-causing?" verdicts),
keeps every version, and detects exactly what changed release to release.

## Why this exists

ClinVar verdicts change as evidence accumulates — a variant labeled
"uncertain significance" can later be reclassified "pathogenic" or "benign."
Patients tested years earlier are rarely re-contacted when this happens,
because few clinics systematically watch ClinVar's monthly updates. GenePulse
is a small, automatable version of the monitoring that diagnostic labs
already do internally: download each release, clean it, and flag exactly
which variants moved between categories.

## Status

| Stage | Status | What it does |
|---|---|---|
| Extract | ✅ Built, tested | Streams one ClinVar release into `data/raw/`, with a SHA-256 manifest |
| Transform | ✅ Built, tested | Cleans ~9M raw rows into ~4.5M deduped, typed, classified variants |
| Load | ✅ Built, tested | Idempotently publishes a cleaned release into an S3 + Athena data lake |
| Query / change-detection | ✅ Built, tested | SQL that flags variants added, removed, or reclassified between releases |
| Validation against real data | 🔲 Next up | Confirm the SQL above reproduces real July→August 2026 numbers |
| Real AWS run | 🔲 Not started | Needs an AWS account with S3 + Glue set up (not done automatically) |
| Dashboard | 🔲 Not started | Streamlit app over the query layer |
| Automation | 🔲 Not started | Scheduled monthly run (Lambda-equivalent) |

"Built, tested" means: real code, a real test suite, passing locally,
**never yet run against real AWS**. Every AWS-calling function is mocked in
tests on purpose — nothing in this repo has touched a real cloud account.

## Architecture

```
ClinVar (NCBI)
     │  genepulse.extract
     ▼
data/raw/variant_summary_<label>.txt.gz        (untouched source + manifest)
     │  genepulse.transform
     ▼
data/processed/variant_summary_<label>.parquet (cleaned, deduped, classified)
     │  genepulse.load
     ▼
s3://<bucket>/variant_releases/release_label=<label>/   (partitioned data lake)
     │  genepulse.query  +  sql/analytics/*.sql
     ▼
"what changed between releases" (added / removed / reclassified)
```

`genepulse.query` can point the exact same SQL at two different places:

- **DuckDB** — queries the Parquet files in `data/processed/` directly, no
  AWS account, no network. This is how the SQL gets written and tested.
- **Amazon Athena** — queries the real S3 + Glue data lake that `load.py`
  publishes to. Same SQL, same answer, for real.

## Repository layout

```
genepulse/
├── config.py       all paths, URLs, and cloud settings in one place
├── extract.py      Stage 1 — download a raw ClinVar release
├── transform.py    Stage 2 — clean it into a typed, deduped Parquet table
├── classify.py     maps ClinVar's free-text labels to 6 fixed categories
├── load.py         Stage 3 — publish a cleaned release to the S3 data lake
└── query.py        Stage 4 — run analytics SQL against DuckDB or Athena

sql/analytics/
├── latest_significance.sql    each variant's most recent classification
└── significance_changes.sql   the diff engine: added / removed / reclassified

tests/              60+ tests, all offline — no network or AWS calls
data/               gitignored — everything here is reproducible by rerunning the stages
legacy/             pre-pivot code (an earlier synthetic-data version of this project), kept for reference
```

## The six significance categories

ClinVar's `ClinicalSignificance` column is semi-free text (100+ distinct
values in a single release). Every raw label is mapped to exactly one of:

`Pathogenic | Benign | Uncertain significance | Conflicting | Other | Not classified`

## Setup

```bash
pip install -r requirements.txt
```

Extract and Transform work with no further setup. Load and the Athena side
of Query need an AWS account:

```bash
aws configure                                  # or: aws sso login
export GENEPULSE_S3_BUCKET=your-bucket-name
export GENEPULSE_ATHENA_DATABASE=genepulse     # optional, defaults to "genepulse"
```

Nothing above creates any AWS resource — the S3 bucket and Athena/Glue
database have to exist already (created separately, deliberately, not by
this repo).

## Usage

```bash
# 1. Download a release (current, or an archived month)
python -m genepulse.extract
python -m genepulse.extract --release 2026-07

# 2. Clean it
python -m genepulse.transform

# 3. Publish it to the data lake (needs AWS credentials)
python -m genepulse.load

# 4. Run the change-detection SQL — locally, for free
python -m genepulse.query sql/analytics/significance_changes.sql --engine duckdb

# ...or for real, against Athena
python -m genepulse.query sql/analytics/significance_changes.sql --engine athena
```

Every stage is idempotent: re-running it is a no-op unless the input changed
or you pass `--force`.

## Testing

```bash
pytest tests/ -q
ruff check genepulse/ tests/ sql/
```

The entire suite runs offline — no network access, no AWS account, no cost.
`tests/test_sql_analytics.py` validates the change-detection SQL against a
small, hand-verified synthetic dataset where every expected answer is known
in advance.

## Key numbers

From a real processed August 2026 ClinVar release (measured by Transform,
not estimated):

| Metric | Value |
|---|---|
| Raw rows in the monthly file | 9,049,133 |
| GRCh38 rows | 4,490,877 |
| Unique variants after cleaning | 4,488,519 |
| Distinct genes | 28,562 |
| Uncertain significance (VUS) | 2,336,656 (~52%) |
| Pathogenic / Likely pathogenic | 355,456 |

July→August 2026 release-to-release changes (~29,100 added, ~6,200
reclassified, 299 removed) were originally found with a one-off manual
script, not the SQL engine above — reproducing those numbers with
`significance_changes.sql` against the real data is the next item on the
roadmap.
