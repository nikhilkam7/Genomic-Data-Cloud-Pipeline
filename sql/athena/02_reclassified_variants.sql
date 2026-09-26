-- GenePulse: variants whose significance category changed between two releases.
-- Same window-function logic as genepulse/diff.py (which runs it locally in DuckDB).
-- Replace the two release labels below.

WITH history AS (
    SELECT
        variation_id,
        gene_symbol,
        release_label,
        significance_category,
        LAG(significance_category) OVER (
            PARTITION BY variation_id ORDER BY release_label
        ) AS prev_category
    FROM genepulse.variants
    WHERE "release" IN ('2026-07', '2026-08-31')   -- partition filter keeps the scan small
)
SELECT
    variation_id,
    gene_symbol,
    prev_category,
    significance_category AS curr_category
FROM history
WHERE release_label = '2026-08-31'
  AND prev_category <> significance_category
ORDER BY prev_category, curr_category, variation_id;
