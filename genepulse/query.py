"""Query stage - run analytics SQL against either DuckDB or Athena.

The same .sql file is meant to run unmodified against two different places:
locally via DuckDB (free, no AWS account, queries the Parquet files already
sitting in PROCESSED_DIR) or for real via Amazon Athena (queries the S3 +
Glue data lake that genepulse.load publishes to). Both engines see a table
called "variant_releases" (config.SQL_TABLE), so a query file never needs to
know which one it's actually running against.

Input  : a .sql file, e.g. sql/analytics/significance_changes.sql
Output : a pandas DataFrame (CLI prints it as JSON records)

CLI:
    python -m genepulse.query sql/analytics/significance_changes.sql
    python -m genepulse.query sql/analytics/latest_significance.sql --engine athena
"""

from __future__ import annotations

import argparse
from pathlib import Path

import awswrangler as wr
import duckdb
import pandas as pd

from genepulse.config import (
    ATHENA_DATABASE,
    ATHENA_S3_STAGING_DIR,
    PROCESSED_DIR,
    QUERY_ENGINE,
    SQL_TABLE,
)


def _run_duckdb(sql: str, processed_dir: Path) -> pd.DataFrame:
    """Run SQL against a DuckDB view over every Parquet file in processed_dir.

    The view is named SQL_TABLE so a query written against it is identical
    to one written against the real Glue table. release_label is already a
    column in every row (transform.py stamps it in), so there is no need to
    replicate S3's Hive partition layout locally.
    """
    glob = str(processed_dir / "*.parquet")
    con = duckdb.connect(database=":memory:")
    con.execute(f"CREATE VIEW {SQL_TABLE} AS SELECT * FROM read_parquet('{glob}')")
    return con.execute(sql).df()


def _run_athena(sql: str, database: str) -> pd.DataFrame:
    """Run SQL against the real Athena/Glue data lake. The only AWS call here."""
    return wr.athena.read_sql_query(sql=sql, database=database, s3_output=ATHENA_S3_STAGING_DIR)


def run_query(
    sql: str,
    *,
    engine: str | None = None,
    processed_dir: Path = PROCESSED_DIR,
    database: str = ATHENA_DATABASE,
) -> pd.DataFrame:
    """Run a SQL string against the chosen engine and return the result.

    Parameters
    ----------
    engine : "duckdb" or "athena". Defaults to config.QUERY_ENGINE.
    """
    engine = engine or QUERY_ENGINE
    if engine == "duckdb":
        return _run_duckdb(sql, processed_dir)
    if engine == "athena":
        return _run_athena(sql, database)
    raise ValueError(f"Unknown engine {engine!r}; expected 'duckdb' or 'athena'")


def run_query_file(path: str | Path, **kwargs) -> pd.DataFrame:
    """Read a .sql file and run it. See run_query for keyword arguments."""
    return run_query(Path(path).read_text(), **kwargs)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a portable analytics SQL file.")
    parser.add_argument("sql_file", type=Path, help="Path to a .sql file, e.g. sql/analytics/significance_changes.sql")
    parser.add_argument(
        "--engine",
        choices=("duckdb", "athena"),
        default=QUERY_ENGINE,
        help="Which engine to run the SQL against. Defaults to GENEPULSE_QUERY_ENGINE ('duckdb').",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    df = run_query_file(args.sql_file, engine=args.engine)
    print(df.to_json(orient="records", indent=2))


if __name__ == "__main__":
    main()
