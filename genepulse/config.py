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

# Optional: pick up settings from a local .env file (never committed).
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # python-dotenv not installed - plain env vars still work
    pass

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

DIFF_DIR = DATA_DIR / "diffs"          # output of the local Diff stage

# --- AWS: S3 + Athena (used by the Load stage) ------------------------------
# No bucket name or credentials are ever hard-coded or committed here. The
# bucket comes from the environment, and auth comes from whatever
# `aws configure` (i.e. ~/.aws/credentials, or AWS_* env vars) has set up on
# the machine running load.py. boto3 finds those credentials on its own.
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("GENEPULSE_S3_BUCKET")
S3_PROCESSED_PREFIX = "processed"   # s3://<bucket>/processed/release=<label>/variants.parquet
S3_MANIFEST_PREFIX = "manifests"    # s3://<bucket>/manifests/<label>.transform_manifest.json
ATHENA_DATABASE = os.environ.get("GENEPULSE_ATHENA_DATABASE", "genepulse")
ATHENA_TABLE = "variants"


def s3_processed_key(release_label: str) -> str:
    """S3 object key for one processed release.

    The ``release=<label>`` folder is a Hive-style partition: Athena reads the
    label from the path, so a query filtered to one or two releases only scans
    those files (and only costs for those bytes).
    """
    return f"{S3_PROCESSED_PREFIX}/release={release_label}/variants.parquet"


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
