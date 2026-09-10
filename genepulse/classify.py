"""Collapse ClinVar's free-text clinical-significance labels into fixed buckets.

ClinVar's ``ClinicalSignificance`` column is semi-free text. In the Aug 2026
release the GRCh38 data alone contains 100+ distinct values - everything from
plain "Pathogenic" to "Conflicting classifications of pathogenicity; risk
factor" to "Pathogenic, low penetrance". Trend analysis and release-to-release
diffing are only meaningful over a small, stable set of categories, so every
raw label is mapped to exactly one of:

    Pathogenic | Benign | Uncertain significance | Conflicting
    | Other | Not classified

The diff engine (a later stage) reuses this module, which is why it lives on
its own rather than inside transform.py.
"""

from __future__ import annotations

# Order matters for display and for data-quality checks elsewhere.
SIGNIFICANCE_CATEGORIES: tuple[str, ...] = (
    "Pathogenic",
    "Benign",
    "Uncertain significance",
    "Conflicting",
    "Other",
    "Not classified",
)

# Raw labels that mean "no assertion was made".
_NOT_CLASSIFIED = {
    "",
    "-",
    "not provided",
    "no classification for the single variant",
    "no classifications from unflagged records",
    "no interpretation for the single variant",
}


def classify_significance(raw: str | None) -> str:
    """Map one raw ClinicalSignificance string to a fixed category.

    The checks run in priority order, which resolves the compound labels:

    * "Conflicting..." contains the substring "pathogenic", so Conflicting
      must be tested before Pathogenic.
    * "Pathogenic/Likely pathogenic; risk factor" -> Pathogenic (the clinically
      actionable part wins over the trailing modifier).
    * "Benign/Likely benign" -> Benign.
    """
    if not isinstance(raw, str):  # None, pd.NA, NaN
        return "Not classified"

    low = raw.strip().lower()

    if low in _NOT_CLASSIFIED:
        return "Not classified"
    if "conflicting" in low:
        return "Conflicting"
    if "pathogenic" in low:
        return "Pathogenic"
    if "benign" in low:
        return "Benign"
    if "uncertain significance" in low or low in {"vus-high", "vus-mid", "vus-low"}:
        return "Uncertain significance"
    return "Other"
