"""Tests for the S3 Load stage - uses moto's fake S3, so no AWS account needed."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from genepulse import load

BUCKET = "genepulse-test"


@pytest.fixture
def processed(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    (tmp_path / "variant_summary_2026-07.parquet").write_bytes(b"older release bytes")
    (tmp_path / "variant_summary_2026-08-31.parquet").write_bytes(b"newer release bytes")
    (tmp_path / "variant_summary_2026-08-31.transform_manifest.json").write_text("{}")
    return tmp_path


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def test_load_uploads_newest_to_partitioned_key(processed, s3):
    result = load.load(bucket=BUCKET, client=s3)
    assert result.uploaded
    assert result.key == "processed/release=2026-08-31/variants.parquet"

    obj = s3.get_object(Bucket=BUCKET, Key=result.key)
    assert obj["Body"].read() == b"newer release bytes"
    assert obj["Metadata"]["sha256"] == result.sha256
    s3.head_object(Bucket=BUCKET, Key="manifests/2026-08-31.transform_manifest.json")


def test_load_skips_identical_upload(processed, s3):
    assert load.load(bucket=BUCKET, client=s3).uploaded
    assert not load.load(bucket=BUCKET, client=s3).uploaded
    assert load.load(bucket=BUCKET, client=s3, force=True).uploaded


def test_load_specific_release(processed, s3):
    result = load.load("2026-07", bucket=BUCKET, client=s3)
    assert result.key == "processed/release=2026-07/variants.parquet"


def test_dry_run_makes_no_aws_calls(processed):
    result = load.load(bucket=BUCKET, dry_run=True, client=object())  # any AWS call would crash
    assert result.dry_run and not result.uploaded


def test_load_requires_bucket(processed, monkeypatch):
    monkeypatch.setattr(load, "S3_BUCKET", None)
    with pytest.raises(RuntimeError, match="GENEPULSE_S3_BUCKET"):
        load.load()
