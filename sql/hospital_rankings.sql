-- =============================================================================
-- hospital_rankings.sql
-- Rankings within state and measure using RANK / DENSE_RANK / PERCENT_RANK.
--
-- Interpretation: a ranking is a SCREENING list for follow-up review, not a
-- verdict on quality. ERRs have statistical uncertainty that CMS does not
-- publish here, and ties or small differences are not meaningful. Only hospitals
-- with >= 25 eligible discharges (CMS reliability threshold) are ranked.
-- =============================================================================

-- Highest ERR within each state and measure (top 3 per state x measure).
-- RANK leaves gaps after ties; DENSE_RANK does not. Both are shown for comparison.
-- @name top3_err_within_state_measure
WITH ranked AS (
  SELECT h.state, f.measure_code, f.facility_id, h.facility_name,
         f.excess_readmission_ratio AS err, f.peer_group_median_err, f.penalty_indicator, f.eligible_discharges,
         RANK()       OVER (PARTITION BY h.state, f.measure_code ORDER BY f.excess_readmission_ratio DESC) AS rnk,
         DENSE_RANK() OVER (PARTITION BY h.state, f.measure_code ORDER BY f.excess_readmission_ratio DESC) AS dense_rnk,
         COUNT(*)     OVER (PARTITION BY h.state, f.measure_code)                                         AS ranked_in_group
  FROM fact_readmission_measure f
  JOIN dim_hospital h ON h.facility_id = f.facility_id
  WHERE f.record_status = 'ANALYZABLE' AND f.eligible_discharges >= 25
)
SELECT state, measure_code, rnk, dense_rnk, ranked_in_group, facility_id, facility_name,
       err, peer_group_median_err, penalty_indicator, eligible_discharges
FROM ranked
WHERE rnk <= 3
ORDER BY state, measure_code, rnk;

-- National percentile of each hospital's ERR within its measure.
-- @name hospital_measure_percentiles
SELECT f.facility_id, h.facility_name, h.state, f.measure_code, f.excess_readmission_ratio AS err,
       ROUND(100 * PERCENT_RANK() OVER (PARTITION BY f.measure_code ORDER BY f.excess_readmission_ratio), 1)
         AS national_percentile,
       NTILE(4) OVER (PARTITION BY f.measure_code ORDER BY f.excess_readmission_ratio) AS err_quartile
FROM fact_readmission_measure f
JOIN dim_hospital h ON h.facility_id = f.facility_id
WHERE f.record_status = 'ANALYZABLE' AND f.eligible_discharges >= 25
ORDER BY f.measure_code, err DESC;

-- Composite: hospitals flagged on several measures, ranked within state by payment reduction.
-- @name multi_measure_flags_within_state
WITH flags AS (
  SELECT facility_id,
         COUNT(CASE WHEN penalty_indicator = 'Y' THEN 1 END)  AS measures_flagged,
         COUNT(CASE WHEN eligible_discharges >= 25 THEN 1 END) AS measures_eligible,
         LISTAGG(CASE WHEN penalty_indicator = 'Y' THEN measure_code END, ', ')
           WITHIN GROUP (ORDER BY measure_code)               AS flagged_measures
  FROM fact_readmission_measure
  GROUP BY facility_id
)
SELECT h.state, h.facility_id, h.facility_name, fl.measures_flagged, fl.measures_eligible, fl.flagged_measures,
       p.payment_reduction_pct,
       RANK() OVER (PARTITION BY h.state ORDER BY p.payment_reduction_pct DESC) AS rank_in_state_by_reduction,
       DENSE_RANK() OVER (ORDER BY p.payment_reduction_pct DESC)                AS national_dense_rank
FROM flags fl
JOIN dim_hospital h          ON h.facility_id = fl.facility_id
JOIN fact_hospital_payment p ON p.facility_id = fl.facility_id
WHERE fl.measures_flagged >= 3
ORDER BY p.payment_reduction_pct DESC, h.state;

-- Count of hospitals per state in the "flagged on >= 3 measures" screening group.
-- @name multi_measure_flags_state_counts
WITH flags AS (
  SELECT facility_id, COUNT(CASE WHEN penalty_indicator = 'Y' THEN 1 END) AS measures_flagged
  FROM fact_readmission_measure GROUP BY facility_id
)
SELECT h.state,
       COUNT(*)                                                   AS hospitals_with_payment_record,
       SUM(CASE WHEN fl.measures_flagged >= 3 THEN 1 ELSE 0 END)  AS flagged_on_3plus,
       ROUND(100 * AVG(CASE WHEN fl.measures_flagged >= 3 THEN 1 ELSE 0 END), 1) AS pct_flagged_on_3plus,
       DENSE_RANK() OVER (ORDER BY AVG(CASE WHEN fl.measures_flagged >= 3 THEN 1 ELSE 0 END) DESC) AS state_rank
FROM flags fl
JOIN dim_hospital h          ON h.facility_id = fl.facility_id
JOIN fact_hospital_payment p ON p.facility_id = fl.facility_id
GROUP BY h.state
ORDER BY state_rank, h.state;

-- Rank of each hospital's payment reduction within its dual-eligible peer group.
-- @name payment_rank_within_peer_group
SELECT p.peer_group, h.facility_id, h.facility_name, h.state, p.payment_reduction_pct,
       RANK() OVER (PARTITION BY p.peer_group ORDER BY p.payment_reduction_pct DESC)                AS rank_in_peer_group,
       ROUND(100 * CUME_DIST() OVER (PARTITION BY p.peer_group ORDER BY p.payment_reduction_pct), 1) AS cume_pct
FROM fact_hospital_payment p
JOIN dim_hospital h ON h.facility_id = p.facility_id
ORDER BY p.peer_group, rank_in_peer_group
FETCH FIRST 100 ROWS ONLY;
