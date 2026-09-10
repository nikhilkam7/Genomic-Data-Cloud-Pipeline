"""Tests for the significance-bucketing rules."""

import pytest

from genepulse.classify import SIGNIFICANCE_CATEGORIES, classify_significance


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pathogenic", "Pathogenic"),
        ("Likely pathogenic", "Pathogenic"),
        ("Pathogenic/Likely pathogenic", "Pathogenic"),
        ("Pathogenic/Likely pathogenic; risk factor", "Pathogenic"),
        ("Pathogenic, low penetrance", "Pathogenic"),
        ("Benign", "Benign"),
        ("Likely benign", "Benign"),
        ("Benign/Likely benign", "Benign"),
        ("Uncertain significance", "Uncertain significance"),
        ("Uncertain significance/Uncertain risk allele", "Uncertain significance"),
        ("VUS-high", "Uncertain significance"),
        # "Conflicting..." contains "pathogenic" - must not fall through to Pathogenic
        ("Conflicting classifications of pathogenicity", "Conflicting"),
        ("Conflicting classifications of pathogenicity; risk factor", "Conflicting"),
        ("drug response", "Other"),
        ("risk factor", "Other"),
        ("association", "Other"),
        ("protective", "Other"),
        ("not provided", "Not classified"),
        ("-", "Not classified"),
        ("", "Not classified"),
        (None, "Not classified"),
        ("  Pathogenic  ", "Pathogenic"),
    ],
)
def test_classify_significance(raw, expected):
    assert classify_significance(raw) == expected


def test_every_result_is_a_known_category():
    samples = ["Pathogenic", "weird new label", "", None, "Conflicting data from submitters"]
    for s in samples:
        assert classify_significance(s) in SIGNIFICANCE_CATEGORIES
