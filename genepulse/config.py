"""Central configuration for the GenePulse pipeline.

Every path and source URL the pipeline needs lives here, so no other module
has to hard-code them. Import what you need:

    from genepulse.config import RAW_DIR, clinvar_url
"""

import os
from pathlib import Path

# --- Project layout -----------------------------------------------------
# __file__ is .../genepulse/config.py ; .parent is genepulse/ ; .parent.parent
# is the repo root. Resolving keeps things correct no matter where you run from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"              # landing zone: untouched source files
PROCESSED_DIR = DATA_DIR / "processed"  # cleaned output of the Transform stage

# --- ClinVar source ---------------------------------------------------
# NCBI publishes one tab-delimited summary file, refreshed monthly, plus an
# archive of every past monthly release.
CLINVAR_BASE_URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited"
CLINVAR_CURRENT_FILENAME = "variant_summary.txt.gz"
CLINVAR_ARCHIVE_SUBDIR = "archive"

# --- Download behaviour ---------------------------------------------------
DOWNLOAD_CHUNK_BYTES = 1024 * 1024   # stream the file 1 MiB at a time
DOWNLOAD_TIMEOUT_SECONDS = 60        # fail fast if the server stops responding

# --- AWS S3 + Athena data lake (used from the Load stage onward) -----------
# No bucket name, database name, or credentials are ever hard-coded or
# committed here. Auth comes from whatever `aws configure` (or
# `aws sso login`) has set up on the machine running load.py - no password
# stored anywhere. DuckDB needs no cloud setup at all: it queries the same
# Parquet files locally, so sql/analytics/ queries are written and tested
# against DuckDB before running unchanged against Athena.
AWS_S3_BUCKET = os.environ.get("GENEPULSE_S3_BUCKET")  # e.g. "genepulse-data"
ATHENA_DATABASE = os.environ.get("GENEPULSE_ATHENA_DATABASE", "genepulse")
SQL_TABLE = "variant_releases"

# --- Query engine (used by genepulse/query.py) ------------------------------
# "duckdb" runs analytics SQL locally against the Parquet files in
# PROCESSED_DIR - no AWS account needed. "athena" runs the exact same SQL
# against the real S3 + Glue data lake. Athena needs somewhere to park its
# query results, which is what ATHENA_S3_STAGING_DIR is for.
QUERY_ENGINE = os.environ.get("GENEPULSE_QUERY_ENGINE", "duckdb")
ATHENA_S3_STAGING_DIR = os.environ.get("GENEPULSE_ATHENA_STAGING_DIR") or (
    f"s3://{AWS_S3_BUCKET}/athena-results/" if AWS_S3_BUCKET else None
)


def clinvar_url(release: str | None = None) -> str:
    """Return the download URL for a ClinVar release.

    release=None            -> the current file
    release="2026-06"       -> archive/variant_summary_2026-06.txt.gz
    """
    if release is None:
        return f"{CLINVAR_BASE_URL}/{CLINVAR_CURRENT_FILENAME}"
    return (
        f"{CLINVAR_BASE_URL}/{CLINVAR_ARCHIVE_SUBDIR}"
        f"/variant_summary_{release}.txt.gz"
    )
