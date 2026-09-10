"""Day 3 - Data wrangling for the GenePulse pipeline.

Loads the synthetic variant call set, drops low-confidence calls, and
produces per-(chromosome, variant-type) quality-control summary statistics.

Run directly:  python3 analysis.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

# --- Configuration -------------------------------------------------------
INPUT_FILE = Path("variants.csv")
OUTPUT_DIR = Path("outputs")

# Quality thresholds. These mirror Variant.is_high_quality() in parser.py
# so the OOP layer and the analytics layer agree on "confident" calls.
MIN_QUALITY_SCORE = 30.0
MIN_READ_DEPTH = 10


def load_variants(path: Path = INPUT_FILE) -> pd.DataFrame:
    """Read variants.csv into a DataFrame with explicit dtypes."""
    dtypes = {
        "Sample_ID": "string",
        "Chromosome": "category",
        "Position": "int64",
        "Read_Depth": "int64",
        "Quality_Score": "float64",
        "Variant_Type": "category",
    }
    df = pd.read_csv(path, dtype=dtypes)
    print(f"Loaded {len(df)} rows from {path}")
    return df


def filter_low_confidence(
    df: pd.DataFrame,
    min_quality: float = MIN_QUALITY_SCORE,
    min_depth: int = MIN_READ_DEPTH,
) -> pd.DataFrame:
    """Return only the rows that pass BOTH quality gates.

    A call is 'low confidence' if its Phred-style quality is below
    `min_quality` OR the site was sequenced fewer than `min_depth` times.
    We keep the complement: quality >= threshold AND depth >= threshold.
    """
    keep_mask = (df["Quality_Score"] >= min_quality) & (df["Read_Depth"] >= min_depth)
    kept = df.loc[keep_mask].copy()

    dropped = len(df) - len(kept)
    pct = dropped / len(df) * 100 if len(df) else 0.0
    print(f"Dropped {dropped} low-confidence rows ({pct:.1f}%); {len(kept)} remain")
    return kept


def add_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add engineered columns used later by plots and the ML model."""
    df = df.copy()

    # Support per depth unit - a rough signal-to-effort ratio.
    df["Quality_Per_Depth"] = df["Quality_Score"] / df["Read_Depth"]

    # Within-chromosome quality z-score: how unusual is this call's quality
    # compared with other calls on the same chromosome? (NumPy vectorised.)
    grp = df.groupby("Chromosome", observed=True)["Quality_Score"]
    df["Quality_Z_By_Chrom"] = (df["Quality_Score"] - grp.transform("mean")) / grp.transform("std")

    # Flag statistical outliers (|z| > 2) without a Python loop.
    df["Is_Quality_Outlier"] = np.where(df["Quality_Z_By_Chrom"].abs() > 2, True, False)
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """Group by Chromosome x Variant_Type and compute QC summary stats."""
    summary = (
        df.groupby(["Chromosome", "Variant_Type"], observed=True)
        .agg(
            variant_count=("Sample_ID", "count"),
            mean_quality=("Quality_Score", "mean"),
            mean_depth=("Read_Depth", "mean"),
            median_depth=("Read_Depth", "median"),
            outlier_count=("Is_Quality_Outlier", "sum"),
        )
        .round(2)
        .reset_index()
        .sort_values(["Chromosome", "variant_count"], ascending=[True, False])
    )
    return summary


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    raw = load_variants()
    confident = filter_low_confidence(raw)
    enriched = add_derived_metrics(confident)
    summary = summarize(enriched)

    print("\n=== QC summary (chromosome x variant type) ===")
    print(summary.to_string(index=False))

    clean_path = OUTPUT_DIR / "variants_clean.csv"
    summary_path = OUTPUT_DIR / "qc_summary.csv"
    enriched.to_csv(clean_path, index=False)
    summary.to_csv(summary_path, index=False)
    print(f"\nWrote {clean_path} and {summary_path}")


if __name__ == "__main__":
    main()
