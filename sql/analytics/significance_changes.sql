-- Reclassification diff between the two most recent releases loaded into the
-- lake. Self-contained: "current" and "previous" are discovered via
-- DENSE_RANK over release_label, so the same file runs unmodified every
-- month as new releases are loaded - no hard-coded dates, no parameters.
--
-- Three outcomes:
--   added    - variant has a row in the current release but none in the
--              previous one (new to ClinVar, or new to this lake)
--   removed  - variant had a row in the previous release but none in the
--              current one. A LAG() window function alone cannot surface
--              this case: there is no current-release row for it to attach
--              to. A FULL OUTER JOIN between the two release snapshots is
--              used instead, which naturally also yields "added" and
--              "changed" from the same join.
--   changed  - present in both, significance_category differs.
WITH release_ranks AS (
    SELECT DISTINCT
        release_label,
        DENSE_RANK() OVER (ORDER BY release_label DESC) AS recency_rank
    FROM variant_releases
),
curr AS (
    SELECT variation_id, gene_symbol, significance_category
    FROM variant_releases
    WHERE release_label = (SELECT release_label FROM release_ranks WHERE recency_rank = 1)
),
prev AS (
    SELECT variation_id, gene_symbol, significance_category
    FROM variant_releases
    WHERE release_label = (SELECT release_label FROM release_ranks WHERE recency_rank = 2)
)
SELECT
    COALESCE(curr.variation_id, prev.variation_id) AS variation_id,
    COALESCE(curr.gene_symbol, prev.gene_symbol)   AS gene_symbol,
    prev.significance_category                      AS previous_significance_category,
    curr.significance_category                      AS current_significance_category,
    CASE
        WHEN prev.variation_id IS NULL THEN 'added'
        WHEN curr.variation_id IS NULL THEN 'removed'
        ELSE 'changed'
    END AS change_type
FROM curr
FULL OUTER JOIN prev ON curr.variation_id = prev.variation_id
WHERE
    prev.variation_id IS NULL
    OR curr.variation_id IS NULL
    OR curr.significance_category <> prev.significance_category;
