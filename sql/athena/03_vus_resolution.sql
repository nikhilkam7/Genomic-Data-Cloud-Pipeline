-- GenePulse: how uncertain-significance (VUS) variants resolved, release to release.
-- One row per (release, from -> to) transition out of VUS, across every loaded release.

WITH history AS (
    SELECT
        variation_id,
        release_label,
        significance_category,
        LAG(significance_category) OVER (
            PARTITION BY variation_id ORDER BY release_label
        ) AS prev_category
    FROM genepulse.variants
)
SELECT
    release_label,
    significance_category AS resolved_to,
    COUNT(*)              AS n_variants
FROM history
WHERE prev_category = 'Uncertain significance'
  AND significance_category <> 'Uncertain significance'
GROUP BY release_label, significance_category
ORDER BY release_label, n_variants DESC;
