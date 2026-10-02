-- Each variant's most recent classification across every release loaded so
-- far. "Most recent" is relative to each variant's own history, not the
-- globally latest release: a variant removed from ClinVar after release N
-- still has a "latest" row here, dated N.
--
-- release_label values must be lexically sortable as chronological strings
-- (YYYY-MM or YYYY-MM-DD, as extract.py produces) for ORDER BY ... DESC to
-- pick the true most-recent release.
WITH ranked AS (
    SELECT
        variation_id,
        gene_symbol,
        significance_category,
        release_label,
        ROW_NUMBER() OVER (
            PARTITION BY variation_id
            ORDER BY release_label DESC
        ) AS rn
    FROM variant_releases
)
SELECT
    variation_id,
    gene_symbol,
    significance_category,
    release_label AS latest_release_label
FROM ranked
WHERE rn = 1;
