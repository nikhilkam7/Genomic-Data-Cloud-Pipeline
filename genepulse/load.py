"""Load stage - publish processed releases to Amazon S3 for querying with Athena.

Input  : data/processed/variant_summary_<label>.parquet (+ its transform manifest)
Output : s3://<bucket>/processed/release=<label>/variants.parquet
         s3://<bucket>/manifests/<label>.transform_manifest.json

This stage is optional - extract -> transform -> diff all run locally. Load is
what moves the same Parquet files into the cloud so Athena can query every
release with SQL. Nothing here creates AWS resources; you create the bucket once
(see README "AWS setup"), and Athena reads whatever lands under processed/.

Uploads are idempotent: each object carries the file's SHA-256 in its S3
metadata, and a release whose hash already matches what is in S3 is skipped.

Credentials are never read from this repo. boto3 finds them itself from
`aws configure` (~/.aws/credentials) or AWS_* environment variables.

CLI:
    python -m genepulse.load                      # newest processed release
    python -m genepulse.load --release 2026-07
    python -m genepulse.load --all                # every processed release
    python -m genepulse.load --dry-run            # show what would upload; no AWS calls
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from genepulse.config import (
    AWS_REGION,
    PROCESSED_DIR,
    S3_BUCKET,
    S3_MANIFEST_PREFIX,
    s3_processed_key,
)

_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass
class LoadResult:
    release_label: str
    bucket: str
    key: str
    size_bytes: int
    sha256: str
    uploaded: bool          # False = skipped because S3 already had identical bytes
    dry_run: bool

    @property
    def s3_uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _processed_releases() -> list[str]:
    return sorted(
        p.name.removeprefix("variant_summary_").removesuffix(".parquet")
        for p in PROCESSED_DIR.glob("variant_summary_*.parquet")
    )


def _s3_client(region: str):
    import boto3  # imported lazily so --dry-run and tests of other stages don't need it

    return boto3.client("s3", region_name=region)


def _remote_sha256(client, bucket: str, key: str) -> str | None:
    """SHA-256 stored on the existing S3 object, or None if there isn't one."""
    from botocore.exceptions import ClientError

    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise  # AccessDenied etc. should be loud, not silently re-uploaded
    return head.get("Metadata", {}).get("sha256")


def load(
    release: str | None = None,
    *,
    bucket: str | None = None,
    region: str = AWS_REGION,
    force: bool = False,
    dry_run: bool = False,
    client=None,
) -> LoadResult:
    """Upload one processed release (default: newest) to S3."""
    bucket = bucket or S3_BUCKET
    if not bucket:
        raise RuntimeError(
            "No S3 bucket configured. Set GENEPULSE_S3_BUCKET (e.g. in .env) "
            "or pass --bucket."
        )

    available = _processed_releases()
    if not available:
        raise FileNotFoundError(f"No processed files in {PROCESSED_DIR}. Run transform first.")
    label = release or available[-1]
    src = PROCESSED_DIR / f"variant_summary_{label}.parquet"
    if not src.exists():
        raise FileNotFoundError(f"No processed file for release {label!r} in {PROCESSED_DIR}")
    manifest = src.with_name(f"variant_summary_{label}.transform_manifest.json")

    key = s3_processed_key(label)
    sha = _sha256_file(src)
    result = LoadResult(label, bucket, key, src.stat().st_size, sha, uploaded=False, dry_run=dry_run)

    if dry_run:
        print(f"[dry-run] would upload {src.name} -> {result.s3_uri}", file=sys.stderr)
        return result

    client = client or _s3_client(region)
    if not force and _remote_sha256(client, bucket, key) == sha:
        print(f"Skipping {label}: identical file already at {result.s3_uri}", file=sys.stderr)
        return result

    print(f"Uploading {src.name} -> {result.s3_uri}", file=sys.stderr)
    client.upload_file(str(src), bucket, key, ExtraArgs={"Metadata": {"sha256": sha}})
    if manifest.exists():
        client.upload_file(
            str(manifest), bucket, f"{S3_MANIFEST_PREFIX}/{label}.transform_manifest.json"
        )
    result.uploaded = True
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload processed ClinVar releases to S3.")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--release", metavar="LABEL", help="Release label, e.g. 2026-07. Omit for newest.")
    which.add_argument("--all", action="store_true", help="Upload every processed release.")
    parser.add_argument("--bucket", help="Override GENEPULSE_S3_BUCKET.")
    parser.add_argument("--force", action="store_true", help="Upload even if S3 already has the same file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without calling AWS.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    releases = _processed_releases() if args.all else [args.release]
    results = [
        load(r, bucket=args.bucket, force=args.force, dry_run=args.dry_run) for r in releases
    ]
    print(json.dumps([{**asdict(r), "s3_uri": r.s3_uri} for r in results], indent=2))


if __name__ == "__main__":
    main()
