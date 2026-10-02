"""Load stage - publish a processed release into the S3 + Athena data lake.

Input  : data/processed/variant_summary_<label>.parquet   (Transform stage output)
Output : s3://<bucket>/variant_releases/release_label=<label>/*.parquet
         A Glue Data Catalog table "variant_releases" (created on first load,
         updated with one more partition on every later load)
         data/processed/variant_summary_<label>.load_manifest.json

"Load" here is not a warehouse COPY: it is handing the Parquet file to AWS
Data Wrangler's s3.to_parquet(..., dataset=True, partition_cols=["release_label"]),
which uploads the data AND registers/updates the Glue table in one call,
dropping the now-redundant release_label column from the stored file content
since it becomes the Hive partition key instead.

CLI:
    python -m genepulse.load                 # newest processed file
    python -m genepulse.load --release 2026-07
    python -m genepulse.load --force         # re-upload even if already loaded
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import awswrangler as wr
import pandas as pd

from genepulse import __version__
from genepulse.config import ATHENA_DATABASE, AWS_S3_BUCKET, PROCESSED_DIR, SQL_TABLE


@dataclass
class LoadResult:
    source_file: str
    release_label: str
    s3_bucket: str
    s3_prefix: str
    athena_database: str
    athena_table: str
    rows_loaded: int
    loaded_at: str
    tool_version: str
    from_cache: bool

    @property
    def manifest_path(self) -> Path:
        return PROCESSED_DIR / f"variant_summary_{self.release_label}.load_manifest.json"


def _find_processed_file(release: str | None) -> Path:
    """Locate the processed Parquet file to load.

    release=None -> the most recently modified processed file
    release="2026-07" -> the file whose name contains that label
    """
    candidates = sorted(PROCESSED_DIR.glob("variant_summary_*.parquet"))
    if not candidates:
        raise FileNotFoundError(f"No processed files in {PROCESSED_DIR}. Run the transform stage first.")
    if release is None:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    for path in candidates:
        if release in path.name:
            return path
    raise FileNotFoundError(f"No processed file matching release {release!r} in {PROCESSED_DIR}")


def _label_from_path(path: Path) -> str:
    # "variant_summary_2026-08-31.parquet" -> "2026-08-31"
    return path.stem.removeprefix("variant_summary_")


def _assert_ready_to_load(df: pd.DataFrame, label: str) -> None:
    """Fail loudly before touching AWS if the input looks wrong."""
    assert not df.empty, f"{label}: processed file has no rows"
    assert (df["release_label"] == label).all(), f"{label}: release_label column does not match filename"
    assert df["variation_id"].is_unique, f"{label}: duplicate variation_id before load"


def _upload_to_lake(df: pd.DataFrame, *, bucket: str, database: str, table: str) -> dict:
    """Upload one release's partition and register/update the Glue table.

    Passing partition_cols=["release_label"] is what makes awswrangler both
    split the write into s3://bucket/table/release_label=<label>/ and drop
    release_label from the stored column list (standard Hive practice) -
    Athena still exposes it as a normal queryable column via the partition
    key, so sql/analytics/ queries don't need to know it's a partition
    rather than a stored column.
    """
    return wr.s3.to_parquet(
        df=df,
        path=f"s3://{bucket}/{table}/",
        dataset=True,
        database=database,
        table=table,
        partition_cols=["release_label"],
        mode="overwrite_partitions",  # re-running --force replaces just this partition
    )


def load(release: str | None = None, *, force: bool = False) -> LoadResult:
    src = _find_processed_file(release)
    label = _label_from_path(src)
    manifest_path = PROCESSED_DIR / f"variant_summary_{label}.load_manifest.json"

    if manifest_path.exists() and not force:
        print(f"Using cached load for {label}", file=sys.stderr)
        cached = json.loads(manifest_path.read_text())
        cached["from_cache"] = True
        return LoadResult(**cached)

    assert AWS_S3_BUCKET, "GENEPULSE_S3_BUCKET is not set"

    print(f"Reading {src.name} ...", file=sys.stderr)
    df = pd.read_parquet(src)
    _assert_ready_to_load(df, label)

    prefix = f"s3://{AWS_S3_BUCKET}/{SQL_TABLE}/release_label={label}/"
    print(f"Uploading {len(df):,} rows to {prefix} ...", file=sys.stderr)
    _upload_to_lake(df, bucket=AWS_S3_BUCKET, database=ATHENA_DATABASE, table=SQL_TABLE)

    result = LoadResult(
        source_file=src.name,
        release_label=label,
        s3_bucket=AWS_S3_BUCKET,
        s3_prefix=prefix,
        athena_database=ATHENA_DATABASE,
        athena_table=SQL_TABLE,
        rows_loaded=len(df),
        loaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        tool_version=__version__,
        from_cache=False,
    )
    payload = asdict(result)
    payload.pop("from_cache")  # a property of *this run*, not of the file
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {manifest_path}", file=sys.stderr)
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load a processed release into the S3 data lake.")
    parser.add_argument("--release", metavar="LABEL", help="Processed file label, e.g. 2026-07. Omit for newest.")
    parser.add_argument("--force", action="store_true", help="Re-upload even if already loaded.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    result = load(**vars(_parse_args(argv)))
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
