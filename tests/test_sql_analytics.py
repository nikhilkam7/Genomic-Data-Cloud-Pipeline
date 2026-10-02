"""Tests for the SQL change-detection queries in sql/analytics/.

Runs the real .sql files against a tiny synthetic two-release fixture via
DuckDB - no AWS, no credentials, no network. This is the fast regression
suite for the diff logic itself: get it right here, against a fixture where
every expected answer is known by hand, before it ever runs against real
ClinVar data.

Fixture (8 variants across two releases):
    1  Pathogenic             -> Pathogenic              unchanged
    2  Uncertain significance -> Pathogenic               changed (VUS->Pathogenic)
    3  Uncertain significance -> Benign                   changed (VUS->Benign)
    4  Benign                 -> Benign                   unchanged
    5  Pathogenic             -> (absent)                 removed
    6  Conflicting            -> Conflicting               unchanged
    7  (absent)               -> Pathogenic                added
    8  (absent)               -> Uncertain significance    added
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from genepulse import query

SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "analytics"

JULY = "2026-07-31"
AUGUST = "2026-08-31"

JULY_ROWS = [
    {"variation_id": 1, "gene_symbol": "GENE1", "significance_category": "Pathogenic", "release_label": JULY},
    {"variation_id": 2, "gene_symbol": "GENE2", "significance_category": "Uncertain significance", "release_label": JULY},
    {"variation_id": 3, "gene_symbol": "GENE3", "significance_category": "Uncertain significance", "release_label": JULY},
    {"variation_id": 4, "gene_symbol": "GENE4", "significance_category": "Benign", "release_label": JULY},
    {"variation_id": 5, "gene_symbol": "GENE5", "significance_category": "Pathogenic", "release_label": JULY},
    {"variation_id": 6, "gene_symbol": "GENE6", "significance_category": "Conflicting", "release_label": JULY},
]

AUGUST_ROWS = [
    {"variation_id": 1, "gene_symbol": "GENE1", "significance_category": "Pathogenic", "release_label": AUGUST},
    {"variation_id": 2, "gene_symbol": "GENE2", "significance_category": "Pathogenic", "release_label": AUGUST},
    {"variation_id": 3, "gene_symbol": "GENE3", "significance_category": "Benign", "release_label": AUGUST},
    {"variation_id": 4, "gene_symbol": "GENE4", "significance_category": "Benign", "release_label": AUGUST},
    {"variation_id": 6, "gene_symbol": "GENE6", "significance_category": "Conflicting", "release_label": AUGUST},
    {"variation_id": 7, "gene_symbol": "GENE7", "significance_category": "Pathogenic", "release_label": AUGUST},
    {"variation_id": 8, "gene_symbol": "GENE8", "significance_category": "Uncertain significance", "release_label": AUGUST},
]


@pytest.fixture
def two_releases(tmp_path):
    pd.DataFrame(JULY_ROWS).to_parquet(tmp_path / "variant_summary_2026-07-31.parquet")
    pd.DataFrame(AUGUST_ROWS).to_parquet(tmp_path / "variant_summary_2026-08-31.parquet")
    return tmp_path


def _run(sql_filename, processed_dir):
    return query.run_query_file(SQL_DIR / sql_filename, engine="duckdb", processed_dir=processed_dir)


def test_latest_significance_one_row_per_variant(two_releases):
    df = _run("latest_significance.sql", two_releases)

    assert len(df) == 8
    assert df["variation_id"].is_unique

    removed_variant = df[df["variation_id"] == 5].iloc[0]
    assert removed_variant["latest_release_label"] == JULY


def test_significance_changes_counts(two_releases):
    df = _run("significance_changes.sql", two_releases)

    # 2 added + 1 removed + 2 changed; the 3 unchanged variants are excluded.
    assert len(df) == 5


def test_significance_changes_added(two_releases):
    df = _run("significance_changes.sql", two_releases)

    added = set(df.loc[df["change_type"] == "added", "variation_id"])
    assert added == {7, 8}


def test_significance_changes_removed(two_releases):
    df = _run("significance_changes.sql", two_releases)

    removed = df[df["change_type"] == "removed"]
    assert set(removed["variation_id"]) == {5}
    assert removed["current_significance_category"].isna().all()
    assert (removed["previous_significance_category"] == "Pathogenic").all()


def test_significance_changes_changed_pairs(two_releases):
    df = _run("significance_changes.sql", two_releases)
    changed = df[df["change_type"] == "changed"].set_index("variation_id")

    assert changed.loc[2, "previous_significance_category"] == "Uncertain significance"
    assert changed.loc[2, "current_significance_category"] == "Pathogenic"
    assert changed.loc[3, "previous_significance_category"] == "Uncertain significance"
    assert changed.loc[3, "current_significance_category"] == "Benign"


def test_significance_changes_matrix_matches_known_shape(two_releases):
    df = _run("significance_changes.sql", two_releases)
    changed = df[df["change_type"] == "changed"]

    matrix = changed.groupby(
        ["previous_significance_category", "current_significance_category"]
    ).size()

    assert matrix[("Uncertain significance", "Pathogenic")] == 1
    assert matrix[("Uncertain significance", "Benign")] == 1
