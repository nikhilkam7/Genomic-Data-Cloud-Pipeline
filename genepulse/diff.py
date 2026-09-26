"""Diff stage - detect what changed between two ClinVar releases, locally.

Input  : two data/processed/variant_summary_<label>.parquet files
Output : data/diffs/changes_<prev>_to_<curr>.parquet   (one row per changed variant)
         data/diffs/changes_<prev>_to_<curr>.manifest.json  (counts + transition matrix)

This runs entirely on your laptop with DuckDB, an in-process SQL engine that
queries Parquet files directly - no database server and no cloud account. The
SQL is deliberately written in the same dialect Athena speaks (standard window
functions, no DuckDB-only syntax), so the exact same logic runs in the cloud
later; see sql/athena/reclassified_variants.sql.

How the change detection works
------------------------------
Stack both releases into one table, then for each variant look at its previous
row with a window function:

    LAG(significance_category) OVER (PARTITION BY variation_id ORDER BY release_label)

* a variant only in the newer release          -> "added"
* a variant only in the older release          -> "removed"
* in both, with a different category           -> "reclassified"
* in both, same category                       -> unchanged (not written out)

CLI:
    python -m genepulse.diff                          # two newest processed releases
    python -m genepulse.diff --prev 2026-07 --curr 2026-08-31
    python -m genepulse.diff --force
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from genepulse import __version__
from genepulse.config import DIFF_DIR, PROCESSED_DIR

# Portable SQL: `variants` is a view over both releases (DuckDB here, an Athena
# table in the cloud). The window functions do all the work.
CHANGES_SQL = """
WITH history AS (
    SELECT
        variation_id,
        gene_symbol,
        release_label,
        significance_category,
        LAG(significance_category) OVER (
            PARTITION BY variation_id ORDER BY release_label
        ) AS prev_category,
        COUNT(*) OVER (PARTITION BY variation_id) AS n_releases
    FROM variants
)
SELECT
    variation_id,
    gene_symbol,
    CASE
        WHEN n_releases = 1 AND release_label = $curr THEN 'added'
        WHEN n_releases = 1 AND release_label = $prev THEN 'removed'
        ELSE 'reclassified'
    END AS change_type,
    CASE WHEN n_releases = 1 AND release_label = $curr
         THEN NULL ELSE COALESCE(prev_category, significance_category) END AS prev_category,
    CASE WHEN n_releases = 1 AND release_label = $prev
         THEN NULL ELSE significance_category END AS curr_category
FROM history
WHERE
    (n_releases = 1)
    OR (release_label = $curr AND prev_category <> significance_category)
ORDER BY change_type, variation_id
"""


@dataclass
class DiffResult:
    prev_release: str
    curr_release: str
    output_file: str
    prev_rows: int
    curr_rows: int
    added: int
    removed: int
    reclassified: int
    vus_resolved: int                         # VUS -> Pathogenic or Benign
    transitions: dict[str, int]               # "Uncertain significance -> Benign": 1234
    diffed_at: str
    tool_version: str

    @property
    def output_path(self) -> Path:
        return DIFF_DIR / self.output_file


def _processed_path(label: str) -> Path:
    return PROCESSED_DIR / f"variant_summary_{label}.parquet"


def _available_releases() -> list[str]:
    """Labels of every processed release, oldest first.

    Labels are "YYYY-MM" (archive) or "YYYY-MM-DD" (current), so plain string
    sorting is also chronological sorting.
    """
    labels = [
        p.name.removeprefix("variant_summary_").removesuffix(".parquet")
        for p in PROCESSED_DIR.glob("variant_summary_*.parquet")
    ]
    return sorted(labels)


def _pick_releases(prev: str | None, curr: str | None) -> tuple[str, str]:
    available = _available_releases()
    if prev is None or curr is None:
        if len(available) < 2:
            raise FileNotFoundError(
                f"Need at least two processed releases in {PROCESSED_DIR} "
                f"(found {len(available)}). Run extract + transform for another month."
            )
        prev = prev or available[-2]
        curr = curr or available[-1]
    for label in (prev, curr):
        if not _processed_path(label).exists():
            raise FileNotFoundError(f"No processed file for release {label!r}. Run transform first.")
    if prev >= curr:
        raise ValueError(f"--prev ({prev}) must be an older release than --curr ({curr})")
    return prev, curr


def _run(prev: str, curr: str) -> tuple[duckdb.DuckDBPyConnection, duckdb.DuckDBPyRelation]:
    con = duckdb.connect()  # in-memory; the Parquet files are the storage
    paths = [str(_processed_path(prev)), str(_processed_path(curr))]
    con.execute(
        "CREATE VIEW variants AS "
        "SELECT variation_id, gene_symbol, release_label, significance_category "
        f"FROM read_parquet({paths!r})"
    )
    return con, con.sql(CHANGES_SQL, params={"prev": prev, "curr": curr})


def diff(prev: str | None = None, curr: str | None = None, *, force: bool = False) -> DiffResult:
    prev, curr = _pick_releases(prev, curr)
    DIFF_DIR.mkdir(parents=True, exist_ok=True)

    stem = f"changes_{prev}_to_{curr}"
    out_path = DIFF_DIR / f"{stem}.parquet"
    manifest_path = DIFF_DIR / f"{stem}.manifest.json"

    if out_path.exists() and manifest_path.exists() and not force:
        print(f"Using cached {out_path.name}", file=sys.stderr)
        return DiffResult(**json.loads(manifest_path.read_text()))

    print(f"Diffing {prev} -> {curr} ...", file=sys.stderr)
    con, changes = _run(prev, curr)
    changes.write_parquet(str(out_path))

    counts = dict(
        con.sql(
            f"SELECT change_type, COUNT(*) FROM read_parquet('{out_path}') GROUP BY 1"
        ).fetchall()
    )
    transitions = {
        f"{a} -> {b}": n
        for a, b, n in con.sql(
            f"SELECT prev_category, curr_category, COUNT(*) FROM read_parquet('{out_path}') "
            "WHERE change_type = 'reclassified' GROUP BY 1, 2 ORDER BY 3 DESC"
        ).fetchall()
    }
    vus_resolved = sum(
        n for k, n in transitions.items()
        if k in ("Uncertain significance -> Pathogenic", "Uncertain significance -> Benign")
    )
    rows = dict(con.sql("SELECT release_label, COUNT(*) FROM variants GROUP BY 1").fetchall())

    result = DiffResult(
        prev_release=prev,
        curr_release=curr,
        output_file=out_path.name,
        prev_rows=int(rows.get(prev, 0)),
        curr_rows=int(rows.get(curr, 0)),
        added=int(counts.get("added", 0)),
        removed=int(counts.get("removed", 0)),
        reclassified=int(counts.get("reclassified", 0)),
        vus_resolved=int(vus_resolved),
        transitions={k: int(v) for k, v in transitions.items()},
        diffed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        tool_version=__version__,
    )
    manifest_path.write_text(json.dumps(asdict(result), indent=2) + "\n")
    print(f"Wrote {out_path}\nWrote {manifest_path}", file=sys.stderr)
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff two processed ClinVar releases locally.")
    parser.add_argument("--prev", metavar="LABEL", help="Older release label. Omit for second-newest.")
    parser.add_argument("--curr", metavar="LABEL", help="Newer release label. Omit for newest.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if cached output exists.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    result = diff(**vars(_parse_args(argv)))
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
