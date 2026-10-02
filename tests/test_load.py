"""Tests for the Load stage.

These never hit AWS. load._upload_to_lake (the one function that would
actually call awswrangler/boto3) is monkeypatched in every test, the same
way test_extract.py stubs extract._download.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from genepulse import load


def _write_processed(dir_, label, *, n=3, mismatch_label=None, duplicate=False):
    rows = [
        {
            "variation_id": i,
            "gene_symbol": f"GENE{i}",
            "significance_category": "Pathogenic",
            "release_label": mismatch_label or label,
        }
        for i in range(1, n + 1)
    ]
    if duplicate:
        rows.append(rows[0])
    path = dir_ / f"variant_summary_{label}.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path


def test_find_processed_file_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    _write_processed(tmp_path, "2026-06")
    newer = _write_processed(tmp_path, "2026-07")
    newer.touch()  # ensure a distinct, later mtime

    assert load._find_processed_file(None) == newer


def test_find_processed_file_selects_release_by_label(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    _write_processed(tmp_path, "2026-06")
    target = _write_processed(tmp_path, "2026-07")

    assert load._find_processed_file("2026-07") == target


def test_find_processed_file_raises_if_none(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        load._find_processed_file(None)


def test_assert_ready_to_load_rejects_mismatched_release_label(tmp_path):
    path = _write_processed(tmp_path, "2026-07", mismatch_label="2026-06")
    df = pd.read_parquet(path)

    with pytest.raises(AssertionError):
        load._assert_ready_to_load(df, "2026-07")


def test_assert_ready_to_load_rejects_duplicate_variation_id(tmp_path):
    path = _write_processed(tmp_path, "2026-07", duplicate=True)
    df = pd.read_parquet(path)

    with pytest.raises(AssertionError):
        load._assert_ready_to_load(df, "2026-07")


def test_load_missing_bucket_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(load, "AWS_S3_BUCKET", None)
    _write_processed(tmp_path, "2026-07")

    with pytest.raises(AssertionError):
        load.load(release="2026-07")


def test_load_writes_manifest_without_real_aws(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(load, "AWS_S3_BUCKET", "genepulse-data")
    monkeypatch.setattr(load, "ATHENA_DATABASE", "genepulse")
    _write_processed(tmp_path, "2026-07", n=3)

    calls = []

    def fake_upload(df, *, bucket, database, table):
        calls.append({"bucket": bucket, "database": database, "table": table, "rows": len(df)})
        return {}

    monkeypatch.setattr(load, "_upload_to_lake", fake_upload)

    result = load.load(release="2026-07")

    assert len(calls) == 1
    assert calls[0] == {"bucket": "genepulse-data", "database": "genepulse", "table": "variant_releases", "rows": 3}
    assert result.from_cache is False
    assert result.s3_prefix == "s3://genepulse-data/variant_releases/release_label=2026-07/"

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["rows_loaded"] == 3
    assert manifest["tool_version"]
    assert "from_cache" not in manifest  # per-run, not persisted


def test_load_uses_cache_on_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(load, "AWS_S3_BUCKET", "genepulse-data")
    _write_processed(tmp_path, "2026-07")

    calls = {"n": 0}

    def fake_upload(df, *, bucket, database, table):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(load, "_upload_to_lake", fake_upload)

    first = load.load(release="2026-07")
    second = load.load(release="2026-07")

    assert calls["n"] == 1
    assert first.from_cache is False
    assert second.from_cache is True


def test_load_force_reuploads(tmp_path, monkeypatch):
    monkeypatch.setattr(load, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(load, "AWS_S3_BUCKET", "genepulse-data")
    _write_processed(tmp_path, "2026-07")

    calls = {"n": 0}

    def fake_upload(df, *, bucket, database, table):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(load, "_upload_to_lake", fake_upload)

    load.load(release="2026-07")
    second = load.load(release="2026-07", force=True)

    assert calls["n"] == 2
    assert second.from_cache is False
