"""Tests for the Transform stage - no real ClinVar file, a tiny fake one."""

from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from genepulse import transform

# The columns transform.py asks for via usecols (order irrelevant).
_HEADER = [
    "VariationID", "Type", "Name", "GeneSymbol", "GeneID",
    "ClinicalSignificance", "ClinSigSimple", "LastEvaluated", "RS# (dbSNP)",
    "PhenotypeList", "Chromosome", "Start", "Stop", "ReferenceAllele",
    "AlternateAllele", "ReviewStatus", "NumberSubmitters", "Assembly",
]

# variation_id, significance, gene, geneid, lasteval, rs, start, submitters, assembly
_ROWS = [
    (1, "Pathogenic", "BRCA1", "672", "Dec 17, 2024", "397704705", "43044295", "5", "GRCh38"),
    (1, "Pathogenic", "RPL5", "6125", "Dec 17, 2024", "397704705", "43044295", "2", "GRCh38"),  # dup variant, multi-gene
    (2, "Pathogenic", "TTN", "7273", "Jan 01, 2020", "111", "1000", "4", "GRCh37"),             # wrong assembly
    (3, "Uncertain significance", "MSH2", "4436", "Jun 29, 2010", "-1", "47637253", "1", "GRCh38"),
    (4, "Conflicting classifications of pathogenicity", "APC", "324", "Mar 03, 2023", "222", "112175", "3", "GRCh38"),
    (5, "Likely benign", "-", "-1", "", "-", "55221", "2", "GRCh38"),                            # no gene, no date
    (6, "not provided", "CFTR", "1080", "Feb 02, 2022", "333", "117530", "1", "GRCh38"),         # -> Not classified
    (7, "Benign", "F8", "2157", "Apr 04, 2021", "444", "154250", "6", "GRCh38"),
]


@pytest.fixture
def fake_raw(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    monkeypatch.setattr(transform, "RAW_DIR", raw_dir)
    monkeypatch.setattr(transform, "PROCESSED_DIR", processed_dir)
    # Relax the production sanity bands so 6 rows are allowed.
    monkeypatch.setattr(transform, "_ROW_COUNT_BAND", (1, 100))
    monkeypatch.setattr(transform, "_MIN_CLASSIFIED_FRAC", 0.5)

    path = raw_dir / "variant_summary_2026-08-31.txt.gz"
    lines = ["\t".join(_HEADER)]
    for vid, sig, gene, gid, le, rs, start, subs, asm in _ROWS:
        rec = {
            "VariationID": vid, "Type": "single nucleotide variant", "Name": f"NM_x:c.{vid}A>G",
            "GeneSymbol": gene, "GeneID": gid, "ClinicalSignificance": sig, "ClinSigSimple": "1",
            "LastEvaluated": le, "RS# (dbSNP)": rs, "PhenotypeList": "Condition A|Condition B",
            "Chromosome": "17", "Start": start, "Stop": start, "ReferenceAllele": "A",
            "AlternateAllele": "G", "ReviewStatus": "criteria provided", "NumberSubmitters": subs,
            "Assembly": asm,
        }
        lines.append("\t".join(str(rec[c]) for c in _HEADER))
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    return path


def test_transform_produces_clean_table(fake_raw):
    result = transform.transform()
    df = pd.read_parquet(result.output_path)

    assert result.raw_rows == 8
    assert result.grch38_rows == 7
    assert result.output_rows == 6          # variant 2 dropped (GRCh37), one dup collapsed
    assert result.duplicates_dropped == 1

    # de-dup kept the higher-submitter row -> BRCA1, not RPL5
    row1 = df.loc[df["variation_id"] == 1].iloc[0]
    assert row1["gene_symbol"] == "BRCA1"
    assert row1["number_submitters"] == 5

    assert df["variation_id"].is_unique
    assert set(df["significance_category"]) <= {
        "Pathogenic", "Benign", "Uncertain significance", "Conflicting", "Other", "Not classified"
    }
    assert bool(df.loc[df["variation_id"] == 3, "is_vus"].iloc[0]) is True
    assert bool(df.loc[df["variation_id"] == 1, "is_vus"].iloc[0]) is False
    assert df.loc[df["variation_id"] == 4, "significance_category"].iloc[0] == "Conflicting"
    assert df.loc[df["variation_id"] == 6, "significance_category"].iloc[0] == "Not classified"

    # cleaning: "-" gene and "-1"/"-" rs id become null; blank date becomes NaT
    assert pd.isna(df.loc[df["variation_id"] == 5, "gene_symbol"].item())
    assert pd.isna(df.loc[df["variation_id"] == 3, "rs_id"].item())
    assert pd.isna(df.loc[df["variation_id"] == 5, "last_evaluated"].item())


def test_transform_writes_manifest_with_counts(fake_raw):
    result = transform.transform()
    manifest_path = result.output_path.parent / f"variant_summary_{result.release_label}.transform_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["release_label"] == "2026-08-31"
    assert sum(manifest["category_counts"].values()) == 6


def test_transform_is_cached_on_second_run(fake_raw, monkeypatch):
    first = transform.transform()

    def boom(*a, **k):
        raise AssertionError("_load_grch38 should not run when cached output exists")

    monkeypatch.setattr(transform, "_load_grch38", boom)
    second = transform.transform()
    assert second.output_file == first.output_file


def test_transform_selects_release_by_label(fake_raw):
    # add an older file; --release must pick the matching one
    older = fake_raw.parent / "variant_summary_2026-07.txt.gz"
    older.write_bytes(fake_raw.read_bytes())
    result = transform.transform(release="2026-07")
    assert result.release_label == "2026-07"
