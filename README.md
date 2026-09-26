# GenePulse

A data pipeline that tracks how ClinVar, NCBI's public database of human genetic variants
and their clinical significance, changes from one monthly release to the next. For example,
it shows which variants were reclassified and how uncertain-significance (VUS) variants
resolved to pathogenic or benign over time.

```
 ClinVar FTP ──► extract ──► transform ──► diff (local, DuckDB)
  (~9M rows)     raw .gz     clean Parquet    changed variants + counts
                 + SHA-256    (~4.5M variants)
                 manifest          │
                                   └──► load ──► Amazon S3 ──► Athena SQL
                                        (optional, cloud)
```

The whole pipeline runs **locally first**. The cloud stage only moves the same Parquet files
into S3 so Athena can query every release, using the same window-function SQL.

## Stages

| Stage | Module | What it does |
|---|---|---|
| Extract | `genepulse/extract.py` | Streams a ClinVar `variant_summary` release to `data/raw/`, with an atomic download and a SHA-256 manifest |
| Transform | `genepulse/transform.py` | Keeps GRCh38 rows, de-duplicates to one row per variant, types the columns, buckets clinical significance into 6 categories, runs data-quality assertions, and writes Parquet |
| Diff | `genepulse/diff.py` | Stacks two releases and uses `LAG() OVER (PARTITION BY variation_id ...)` in DuckDB to find added, removed, and reclassified variants |
| Load | `genepulse/load.py` | Uploads processed releases to `s3://<bucket>/processed/release=<label>/`. The upload is idempotent through a SHA-256 stored in the object metadata |
| Query | `sql/athena/*.sql` | Registers the Athena table (partitioned by release) and runs the reclassification and VUS-resolution queries |

## Quick start (local, no cloud account)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m genepulse.extract --release 2026-07   # an archived month
python -m genepulse.extract                     # the current release
python -m genepulse.transform --release 2026-07
python -m genepulse.transform                   # newest raw file
python -m genepulse.diff                        # two newest processed releases
```

`diff` prints a summary (added, removed, reclassified, VUS resolved, and the category transition
counts) and writes `data/diffs/changes_<prev>_to_<curr>.parquet`.

## AWS setup (S3 + Athena)

S3 is file storage. Athena runs SQL directly on the files stored in S3 and charges about
$5 per TB scanned. The processed releases are small Parquet files, so experimenting costs
cents.

1. **Protect the account:** turn on MFA for the root user, then go to Billing → Budgets and create a $5/month budget with an email alert.
2. **Create an IAM user** (for example `genepulse-dev`) with `AmazonS3FullAccess` and `AmazonAthenaFullAccess`, and create an access key for the CLI.
3. **Configure credentials locally.** They are never stored in this repo:
   ```bash
   aws configure                     # key, secret, region us-east-1
   aws sts get-caller-identity       # confirms who you are
   aws s3 mb s3://genepulse-<yourname>-<random>
   cp .env.example .env              # set GENEPULSE_S3_BUCKET
   ```
4. **Upload the processed releases:**
   ```bash
   python -m genepulse.load --all --dry-run   # preview
   python -m genepulse.load --all
   ```
5. **Query in Athena:** set the query-result location to `s3://<bucket>/athena-results/`, then run
   `sql/athena/01_create_table.sql` (after replacing `YOUR-BUCKET`), followed by `02_…` and `03_…`.

## Tests

```bash
pytest          # no network or AWS needed; S3 is mocked with moto
ruff check .
```

## Roadmap

- [x] Extract, Transform
- [x] Local diff engine (DuckDB)
- [x] S3 load + Athena queries
- [ ] Streamlit dashboard: VUS resolution over time, per-gene reclassification
- [ ] Monthly schedule (cron / GitHub Actions)
