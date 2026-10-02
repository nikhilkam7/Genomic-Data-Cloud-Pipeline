"""Tests for the Query stage (the DuckDB/Athena portability layer).

These never hit AWS. The DuckDB path runs for real (it needs no network or
credentials, just local Parquet files); the Athena path is exercised by
monkeypatching query._run_athena, the one function that would actually call
AWS.
"""

from __future__ import annotations

import pandas as pd
import pytest

from genepulse import query


def _write_parquet(path, rows):
    pd.DataFrame(rows).to_parquet(path)


def test_run_query_duckdb_reads_parquet_glob(tmp_path):
    _write_parquet(tmp_path / "variant_summary_2026-07.parquet", [{"variation_id": 1}, {"variation_id": 2}])
    _write_parquet(tmp_path / "variant_summary_2026-08.parquet", [{"variation_id": 3}])

    df = query.run_query("SELECT COUNT(*) AS n FROM variant_releases", engine="duckdb", processed_dir=tmp_path)

    assert df["n"].iloc[0] == 3


def test_run_query_athena_delegates_to_wrapper(monkeypatch):
    calls = {}

    def fake_run_athena(sql, database):
        calls["sql"] = sql
        calls["database"] = database
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(query, "_run_athena", fake_run_athena)

    df = query.run_query("SELECT 1", engine="athena", database="genepulse")

    assert calls == {"sql": "SELECT 1", "database": "genepulse"}
    assert df["x"].iloc[0] == 1


def test_run_query_file_reads_sql_from_disk(tmp_path, monkeypatch):
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT 1")

    captured = {}

    def fake_run_query(sql, **kwargs):
        captured["sql"] = sql
        return pd.DataFrame()

    monkeypatch.setattr(query, "run_query", fake_run_query)

    query.run_query_file(sql_file)

    assert captured["sql"] == "SELECT 1"


def test_unknown_engine_raises():
    with pytest.raises(ValueError):
        query.run_query("SELECT 1", engine="snowflake")


def test_default_engine_comes_from_config(monkeypatch):
    monkeypatch.setattr(query, "QUERY_ENGINE", "athena")

    calls = {"n": 0}

    def fake_run_athena(sql, database):
        calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(query, "_run_athena", fake_run_athena)

    query.run_query("SELECT 1")

    assert calls["n"] == 1
