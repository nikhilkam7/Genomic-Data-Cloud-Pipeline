"""Tests for the local Diff stage - two tiny hand-built releases."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from genepulse import diff

# variation_id -> category, per release
_PREV = {1: "Pathogenic", 2: "Uncertain significance", 3: "Uncertain significance", 4: "Benign", 5: "Conflicting"}
_CURR = {1: "Pathogenic", 2: "Benign", 3: "Pathogenic", 4: "Benign", 6: "Uncertain significance"}
#  1 unchanged | 2 VUS->Benign | 3 VUS->Pathogenic | 4 unchanged | 5 removed | 6 added


def _write_release(processed_dir, label, cats):
    df = pd.DataFrame(
        {
            "variation_id": pd.array(list(cats), dtype="int64"),
            "gene_symbol": [f"GENE{v}" for v in cats],
            "significance_category": list(cats.values()),
            "release_label": label,
        }
    )
    df.to_parquet(processed_dir / f"variant_summary_{label}.parquet", index=False)


@pytest.fixture
def two_releases(tmp_path, monkeypatch):
    processed, diffs = tmp_path / "processed", tmp_path / "diffs"
    processed.mkdir()
    monkeypatch.setattr(diff, "PROCESSED_DIR", processed)
    monkeypatch.setattr(diff, "DIFF_DIR", diffs)
    _write_release(processed, "2026-07", _PREV)
    _write_release(processed, "2026-08-31", _CURR)
    return processed


def test_diff_counts_and_rows(two_releases):
    result = diff.diff()
    assert (result.prev_release, result.curr_release) == ("2026-07", "2026-08-31")
    assert (result.prev_rows, result.curr_rows) == (5, 5)
    assert (result.added, result.removed, result.reclassified) == (1, 1, 2)
    assert result.vus_resolved == 2
    assert result.transitions == {
        "Uncertain significance -> Benign": 1,
        "Uncertain significance -> Pathogenic": 1,
    }

    out = pd.read_parquet(result.output_path).set_index("variation_id")
    assert set(out.index) == {2, 3, 5, 6}                 # unchanged variants are not written
    assert out.loc[5, "change_type"] == "removed" and pd.isna(out.loc[5, "curr_category"])
    assert out.loc[6, "change_type"] == "added" and pd.isna(out.loc[6, "prev_category"])
    assert out.loc[2, "prev_category"] == "Uncertain significance"
    assert out.loc[2, "curr_category"] == "Benign"


def test_diff_writes_manifest_and_caches(two_releases, monkeypatch):
    first = diff.diff()
    manifest = json.loads((first.output_path.with_suffix(".manifest.json")).read_text())
    assert manifest["reclassified"] == 2

    monkeypatch.setattr(diff, "_run", lambda *a: (_ for _ in ()).throw(AssertionError("should be cached")))
    assert diff.diff().output_file == first.output_file


def test_diff_needs_two_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(diff, "PROCESSED_DIR", tmp_path)
    _write_release(tmp_path, "2026-07", _PREV)
    with pytest.raises(FileNotFoundError, match="at least two"):
        diff.diff()


def test_diff_rejects_reversed_order(two_releases):
    with pytest.raises(ValueError, match="older"):
        diff.diff(prev="2026-08-31", curr="2026-07")
