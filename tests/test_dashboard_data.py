"""Tests for dashboard/data.py - pure functions, no Streamlit, no AWS.

Reuses the same synthetic two-release fixture as test_sql_analytics.py so
the dashboard's numbers are checked against the same hand-verified answers.
"""

from __future__ import annotations

import pandas as pd
import pytest

from dashboard import data as dashboard_data

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


def test_release_labels_most_recent_first(two_releases):
    labels = dashboard_data.release_labels(engine="duckdb", processed_dir=two_releases)
    assert labels == [AUGUST, JULY]


def test_latest_snapshot_one_row_per_variant(two_releases):
    snapshot = dashboard_data.latest_snapshot(engine="duckdb", processed_dir=two_releases)
    assert len(snapshot) == 8


def test_category_breakdown_fixed_order_and_totals(two_releases):
    snapshot = dashboard_data.latest_snapshot(engine="duckdb", processed_dir=two_releases)
    breakdown = dashboard_data.category_breakdown(snapshot)

    assert list(breakdown["significance_category"]) == [
        "Pathogenic",
        "Benign",
        "Uncertain significance",
        "Conflicting",
        "Other",
        "Not classified",
    ]
    assert breakdown["count"].sum() == 8
    assert breakdown["share"].sum() == pytest.approx(1.0)

    pathogenic = breakdown.set_index("significance_category").loc["Pathogenic"]
    # variants 1, 2, 7 (Pathogenic in August) plus 5, whose last known state is July's
    # Pathogenic - latest_significance.sql deliberately keeps a removed variant's
    # last-seen row rather than dropping it.
    assert pathogenic["count"] == 4


def test_category_breakdown_handles_empty_snapshot():
    empty = pd.DataFrame({"significance_category": pd.Series(dtype="object")})
    breakdown = dashboard_data.category_breakdown(empty)

    assert breakdown["count"].sum() == 0
    assert (breakdown["share"] == 0.0).all()


def test_change_summary_counts(two_releases):
    changes = dashboard_data.release_changes(engine="duckdb", processed_dir=two_releases)
    summary = dashboard_data.change_summary(changes)

    assert summary == {"added": 2, "removed": 1, "changed": 2}


def test_reclassification_matrix_ranked_and_labeled(two_releases):
    changes = dashboard_data.release_changes(engine="duckdb", processed_dir=two_releases)
    matrix = dashboard_data.reclassification_matrix(changes)

    assert len(matrix) == 2
    assert matrix["count"].is_monotonic_decreasing
    assert set(matrix["transition"]) == {
        "Uncertain significance → Pathogenic",
        "Uncertain significance → Benign",
    }
