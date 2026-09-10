"""Transform stage - turn a raw ClinVar release into a clean variant table.

Input  : data/raw/variant_summary_<label>.txt.gz   (~9M rows, 43 columns)
Output : data/processed/variant_summary_<label>.parquet  (~4.5M rows, 18 cols)
         data/processed/variant_summary_<label>.transform_manifest.json

Steps: keep only GRCh38 rows, one row per variant, a stable set of renamed
columns, real dates and integers, and a bucketed significance category. The
file is read in chunks so the full 9M rows never sit in memory at once.

CLI:
    python -m genepulse.transform                 # newest raw file
    python -m genepulse.transform --release 2026-07
    python -m genepulse.transform --force
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from genepulse import __version__
from genepulse.classify import SIGNIFICANCE_CATEGORIES, classify_significance
from genepulse.config import PROCESSED_DIR, RAW_DIR

# Source column -> our snake_case name. Anything not listed is dropped.
COLUMN_MAP: dict[str, str] = {
    "VariationID": "variation_id",
    "Type": "variant_type",
    "Name": "name",
    "GeneSymbol": "gene_symbol",
    "GeneID": "gene_id",
    "ClinicalSignificance": "clinical_significance",
    "ClinSigSimple": "clin_sig_simple",
    "LastEvaluated": "last_evaluated",
    "RS# (dbSNP)": "rs_id",
    "PhenotypeList": "phenotype_list",
    "Chromosome": "chromosome",
    "Start": "start",
    "Stop": "stop",
    "ReferenceAllele": "reference_allele",
    "AlternateAllele": "alternate_allele",
    "ReviewStatus": "review_status",
    "NumberSubmitters": "number_submitters",
}
# "Assembly" is read only so we can filter on it, then dropped.
_SOURCE_COLUMNS = [*COLUMN_MAP, "Assembly"]

CHUNK_ROWS = 500_000
_ROW_COUNT_BAND = (3_000_000, 6_000_000)  # sanity check on the cleaned table
_MIN_CLASSIFIED_FRAC = 0.90              # share of rows that must land in a real category


@dataclass
class TransformResult:
    source_file: str
    output_file: str
    release_label: str
    raw_rows: int
    grch38_rows: int
    output_rows: int
    duplicates_dropped: int
    category_counts: dict[str, int]
    transformed_at: str
    tool_version: str

    @property
    def output_path(self) -> Path:
        return PROCESSED_DIR / self.output_file


def _find_raw_file(release: str | None) -> Path:
    """Locate the raw .gz to transform.

    release=None -> the most recently modified raw file
    release="2026-07" -> the file whose name contains that label
    """
    candidates = sorted(RAW_DIR.glob("variant_summary_*.txt.gz"))
    if not candidates:
        raise FileNotFoundError(f"No raw ClinVar files in {RAW_DIR}. Run the extract stage first.")
    if release is None:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    for path in candidates:
        if release in path.name:
            return path
    raise FileNotFoundError(f"No raw file matching release {release!r} in {RAW_DIR}")


def _label_from_path(path: Path) -> str:
    # "variant_summary_2026-08-31.txt.gz" -> "2026-08-31"
    return path.name.removeprefix("variant_summary_").removesuffix(".txt.gz")


def _load_grch38(path: Path) -> tuple[pd.DataFrame, int]:
    """Stream the gzipped TSV, keeping only GRCh38 rows and wanted columns."""
    raw_rows = 0
    kept: list[pd.DataFrame] = []
    reader = pd.read_csv(
        path,
        sep="\t",
        usecols=_SOURCE_COLUMNS,
        dtype="string",          # read everything as text; we cast deliberately below
        chunksize=CHUNK_ROWS,
        compression="gzip",
    )
    for chunk in reader:
        raw_rows += len(chunk)
        is_grch38 = chunk["Assembly"].eq("GRCh38").fillna(False)
        kept.append(chunk.loc[is_grch38])
    df = pd.concat(kept, ignore_index=True)
    return df, raw_rows


def _clean(df: pd.DataFrame, release_label: str) -> tuple[pd.DataFrame, int]:
    """Rename, retype, bucket, and de-duplicate the GRCh38 rows."""
    df = df.rename(columns=COLUMN_MAP).drop(columns=["Assembly"])

    # Integers. ClinVar writes "-1" for "unknown" gene ids.
    df["variation_id"] = pd.to_numeric(df["variation_id"], errors="raise").astype("int64")
    df["gene_id"] = pd.to_numeric(df["gene_id"], errors="coerce").astype("Int64")
    df.loc[df["gene_id"].le(0).fillna(False), "gene_id"] = pd.NA
    for col in ("start", "stop", "number_submitters"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Dates like "Dec 17, 2024"; ~6% are blank -> NaT.
    df["last_evaluated"] = pd.to_datetime(
        df["last_evaluated"], format="%b %d, %Y", errors="coerce"
    )

    # Text tidy-ups. "-" is a real allele (indel) so it is NOT treated as null.
    df["gene_symbol"] = df["gene_symbol"].replace("-", pd.NA)
    df["rs_id"] = df["rs_id"].replace({"-1": pd.NA, "-": pd.NA})

    # The bucketed category + a convenience flag for the biggest group.
    df["significance_category"] = (
        df["clinical_significance"].fillna("").map(classify_significance).astype("string")
    )
    df["is_vus"] = (df["significance_category"] == "Uncertain significance").astype("boolean")

    df["release_label"] = pd.array([release_label] * len(df), dtype="string")

    # One row per variant. Duplicates come from multi-gene mappings and carry
    # an identical significance, so keeping the row with the most submitters is
    # a harmless, deterministic choice.
    before = len(df)
    df = (
        df.sort_values("number_submitters", ascending=False, na_position="last")
        .drop_duplicates("variation_id", keep="first")
        .sort_values("variation_id", ignore_index=True)
    )
    duplicates_dropped = before - len(df)
    return df, duplicates_dropped


def _assert_quality(df: pd.DataFrame) -> None:
    """Fail loudly before writing if the cleaned table looks wrong."""
    assert df["variation_id"].notna().all(), "null variation_id"
    assert df["variation_id"].is_unique, "variation_id not unique after de-dup"

    low, high = _ROW_COUNT_BAND
    assert low <= len(df) <= high, f"row count {len(df):,} outside expected {low:,}-{high:,}"

    unexpected = set(df["significance_category"].dropna()) - set(SIGNIFICANCE_CATEGORIES)
    assert not unexpected, f"unexpected significance categories: {unexpected}"

    classified = float((df["significance_category"] != "Not classified").mean())
    assert classified >= _MIN_CLASSIFIED_FRAC, (
        f"only {classified:.1%} of rows classified - check the mapping"
    )


def transform(release: str | None = None, *, force: bool = False) -> TransformResult:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    src = _find_raw_file(release)
    label = _label_from_path(src)
    out_path = PROCESSED_DIR / f"variant_summary_{label}.parquet"
    manifest_path = PROCESSED_DIR / f"variant_summary_{label}.transform_manifest.json"

    if out_path.exists() and manifest_path.exists() and not force:
        print(f"Using cached {out_path.name}", file=sys.stderr)
        return TransformResult(**json.loads(manifest_path.read_text()))

    print(f"Reading {src.name} ...", file=sys.stderr)
    df, raw_rows = _load_grch38(src)
    grch38_rows = len(df)

    print(f"Cleaning {grch38_rows:,} GRCh38 rows ...", file=sys.stderr)
    df, duplicates_dropped = _clean(df, label)

    _assert_quality(df)

    df.to_parquet(out_path, engine="pyarrow", index=False)

    counts = df["significance_category"].value_counts().to_dict()
    result = TransformResult(
        source_file=src.name,
        output_file=out_path.name,
        release_label=label,
        raw_rows=raw_rows,
        grch38_rows=grch38_rows,
        output_rows=len(df),
        duplicates_dropped=duplicates_dropped,
        category_counts={k: int(v) for k, v in counts.items()},
        transformed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        tool_version=__version__,
    )
    manifest_path.write_text(json.dumps(asdict(result), indent=2) + "\n")
    print(f"Wrote {out_path}\nWrote {manifest_path}", file=sys.stderr)
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean a raw ClinVar release into Parquet.")
    parser.add_argument("--release", metavar="LABEL", help="Raw file label, e.g. 2026-07. Omit for newest.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if cached output exists.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    result = transform(**vars(_parse_args(argv)))
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
