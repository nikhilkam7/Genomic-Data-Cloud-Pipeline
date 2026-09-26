-- GenePulse: register the processed Parquet files in S3 as an Athena table.
-- Run once in the Athena console (replace YOUR-BUCKET). No data is copied:
-- Athena reads the files in place and bills per byte scanned.
-- The Athena console runs one statement at a time: run each block separately.

CREATE DATABASE IF NOT EXISTS genepulse;

CREATE EXTERNAL TABLE IF NOT EXISTS genepulse.variants (
    variation_id          bigint,
    variant_type          string,
    name                  string,
    gene_symbol           string,
    gene_id               bigint,
    clinical_significance string,
    clin_sig_simple       string,
    last_evaluated        timestamp,
    rs_id                 string,
    phenotype_list        string,
    chromosome            string,
    `start`               bigint,
    `stop`                bigint,
    reference_allele      string,
    alternate_allele      string,
    review_status         string,
    number_submitters     bigint,
    significance_category string,
    is_vus                boolean,
    release_label         string
)
-- Each release lives in its own folder: processed/release=2026-08-31/...
-- so a query that filters on `release` only reads (and pays for) those files.
PARTITIONED BY (`release` string)
STORED AS PARQUET
LOCATION 's3://YOUR-BUCKET/processed/';

-- Re-run after every `python -m genepulse.load` to pick up new release folders.
MSCK REPAIR TABLE genepulse.variants;
