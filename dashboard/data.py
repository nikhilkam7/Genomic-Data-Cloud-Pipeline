"""Data-shaping for the dashboard - pure functions, no Streamlit import.

Keeping this separate from app.py means it is unit-testable like every other
stage in genepulse, without needing Streamlit installed or a browser running.
app.py is a thin layer that calls these functions and renders the result.

Everything here runs through genepulse.query, so it works unmodified against
either DuckDB (local Parquet files) or Athena (the real data lake).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from genepulse import query
from genepulse.classify import SIGNIFICANCE_CATEGORIES
from genepulse.config import PROCESSED_DIR

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "analytics"


def release_labels(*, engine: str | None = None, processed_dir: Path = PROCESSED_DIR) -> list[str]:
    """Every release label currently queryable, most recent first."""
    df = query.run_query(
        "SELECT DISTINCT release_label FROM variant_releases ORDER BY release_label DESC",
        engine=engine,
        processed_dir=processed_dir,
    )
    return df["release_label"].tolist()


def latest_snapshot(*, engine: str | None = None, processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """Each variant's most recent known classification."""
    return query.run_query_file(_SQL_DIR / "latest_significance.sql", engine=engine, processed_dir=processed_dir)


def category_breakdown(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Counts + share per significance category, in the project's fixed category order.

    Fixed order (not sorted by count) so the breakdown reads the same way
    release to release, and so chart color assignment never shifts underfoot.
    """
    counts = snapshot["significance_category"].value_counts()
    total = len(snapshot)
    rows = [
        {
            "significance_category": category,
            "count": int(counts.get(category, 0)),
            "share": counts.get(category, 0) / total if total else 0.0,
        }
        for category in SIGNIFICANCE_CATEGORIES
    ]
    return pd.DataFrame(rows)


def release_changes(*, engine: str | None = None, processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """The added/removed/reclassified diff between the two most recent releases."""
    return query.run_query_file(_SQL_DIR / "significance_changes.sql", engine=engine, processed_dir=processed_dir)


def change_summary(changes: pd.DataFrame) -> dict[str, int]:
    """How many variants were added, removed, and reclassified."""
    counts = changes["change_type"].value_counts()
    return {
        "added": int(counts.get("added", 0)),
        "removed": int(counts.get("removed", 0)),
        "changed": int(counts.get("changed", 0)),
    }


def reclassification_matrix(changes: pd.DataFrame) -> pd.DataFrame:
    """Every previous -> current category transition, ranked largest first."""
    changed = changes[changes["change_type"] == "changed"]
    matrix = (
        changed.groupby(["previous_significance_category", "current_significance_category"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False, ignore_index=True)
    )
    matrix["transition"] = (
        matrix["previous_significance_category"] + " → " + matrix["current_significance_category"]
    )
    return matrix
