-- =============================================================================
-- readmission_analysis.sql
-- Analytical queries: measure, state, hospital-group, peer-group and payment
-- analyses. Uses only ANALYZABLE rows (CMS-published ERR) unless stated.
--
-- Terms
--   ERR                    predicted / expected readmission rate (risk-adjusted by CMS)
--   penalty_indicator      official CMS field (Y = >=25 eligible discharges AND ERR > peer median)
--   is_penalized (derived) payment_adjustment_factor < 1, i.e. any FY2026 payment reduction
--   contribution_pp        percentage points of payment reduction attributable to a measure,
--                          from the CMS formula; NOT dollars
-- =============================================================================

-- ---------------------------------------------------------------- 1. measures
-- @name measure_summary
SELECT f.measure_code,
       m.measure_label,
       COUNT(*)                                                      AS hospitals_with_err,
       ROUND(AVG(f.excess_readmission_ratio), 4)                     AS mean_err,
       ROUND(MEDIAN(f.excess_readmission_ratio), 4)                  AS median_err,
       ROUND(STDDEV(f.excess_readmission_ratio), 4)                  AS sd_err,
       ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY f.excess_readmission_ratio), 4) AS p90_err,
       ROUND(100 * AVG(CASE WHEN f.excess_readmission_ratio > 1 THEN 1 ELSE 0 END), 1) AS pct_err_above_1,
       ROUND(AVG(f.predicted_readmission_rate), 2)                   AS mean_predicted_rate_pct,
       ROUND(AVG(f.expected_readmission_rate), 2)                    AS mean_expected_rate_pct
FROM fact_readmission_measure f
JOIN dim_measure m ON m.measure_code = f.measure_code
WHERE f.record_status = 'ANALYZABLE'
GROUP BY f.measure_code, m.measure_label
ORDER BY sd_err DESC;

-- Penalty indicator rate among hospitals eligible for the measure (>= 25 discharges).
-- @name measure_penalty_rate
SELECT measure_code,
       COUNT(*)                                                       AS eligible_hospitals,
       SUM(CASE WHEN penalty_indicator = 'Y' THEN 1 ELSE 0 END)       AS flagged,
       ROUND(100 * AVG(CASE WHEN penalty_indicator = 'Y' THEN 1 ELSE 0 END), 1) AS pct_flagged
FROM fact_readmission_measure
WHERE eligible_discharges >= 25 AND penalty_indicator IS NOT NULL
GROUP BY measure_code
ORDER BY pct_flagged DESC;

-- ---------------------------------------------------------------- 2. reporting periods
-- Written to generalise: with more program years loaded, each period gets a row.
-- The current CMS release contains a single performance period.
-- @name measure_by_reporting_period
SELECT measure_code,
       TO_CHAR(start_date, 'YYYY-MM-DD') || ' to ' || TO_CHAR(end_date, 'YYYY-MM-DD') AS performance_period,
       COUNT(*)                                     AS hospitals_with_err,
       ROUND(AVG(excess_readmission_ratio), 4)      AS mean_err,
       ROUND(AVG(excess_readmission_ratio) - LAG(AVG(excess_readmission_ratio))
             OVER (PARTITION BY measure_code ORDER BY start_date), 4) AS change_vs_prior_period
FROM fact_readmission_measure
WHERE record_status = 'ANALYZABLE'
GROUP BY measure_code, start_date, end_date
ORDER BY measure_code, start_date;

-- ---------------------------------------------------------------- 3. states
-- State x measure summary compared with the national mean for the same measure (CTE + window).
-- @name state_measure_summary
WITH state_measure AS (
  SELECT h.state, f.measure_code,
         COUNT(*)                                  AS hospitals,
         AVG(f.excess_readmission_ratio)           AS mean_err,
         SUM(CASE WHEN f.penalty_indicator = 'Y' THEN 1 ELSE 0 END) AS flagged
  FROM fact_readmission_measure f
  JOIN dim_hospital h ON h.facility_id = f.facility_id
  WHERE f.record_status = 'ANALYZABLE'
  GROUP BY h.state, f.measure_code
), national AS (
  SELECT measure_code, AVG(excess_readmission_ratio) AS national_mean_err
  FROM fact_readmission_measure
  WHERE record_status = 'ANALYZABLE'
  GROUP BY measure_code
)
SELECT s.state, s.measure_code, s.hospitals,
       ROUND(s.mean_err, 4)                                   AS state_mean_err,
       ROUND(n.national_mean_err, 4)                          AS national_mean_err,
       ROUND(s.mean_err - n.national_mean_err, 4)             AS diff_vs_national,
       s.flagged,
       CASE WHEN s.hospitals < 5 THEN 'Low n - interpret with caution' END AS reliability_note,
       DENSE_RANK() OVER (PARTITION BY s.measure_code ORDER BY s.mean_err DESC) AS state_rank_highest_err
FROM state_measure s
JOIN national n ON n.measure_code = s.measure_code
ORDER BY s.measure_code, state_rank_highest_err;

-- @name state_payment_summary
WITH st AS (
  SELECT h.state,
         COUNT(*)                                                         AS hospitals,
         AVG(p.payment_reduction_pct)                                     AS mean_reduction_pct,
         MEDIAN(p.payment_reduction_pct)                                  AS median_reduction_pct,
         AVG(CASE WHEN p.payment_adjustment_factor < 1 THEN 1 ELSE 0 END) AS share_penalized,
         SUM(CASE WHEN p.payment_reduction_pct >= 1 THEN 1 ELSE 0 END)    AS hospitals_reduction_ge_1pct
  FROM fact_hospital_payment p
  JOIN dim_hospital h ON h.facility_id = p.facility_id
  GROUP BY h.state
)
SELECT state, hospitals,
       ROUND(mean_reduction_pct, 3)                       AS mean_reduction_pct,
       ROUND(median_reduction_pct, 3)                     AS median_reduction_pct,
       ROUND(100 * share_penalized, 1)                    AS pct_penalized,
       hospitals_reduction_ge_1pct,
       ROUND(mean_reduction_pct - AVG(mean_reduction_pct) OVER (), 3) AS diff_vs_mean_of_states,
       RANK() OVER (ORDER BY mean_reduction_pct DESC)     AS rank_highest_reduction
FROM st
ORDER BY rank_highest_reduction;

-- ---------------------------------------------------------------- 4. hospital groups
-- Mean ERR by hospital characteristic and measure (descriptive; tests are in the notebook).
-- @name err_by_hospital_characteristic
SELECT 'Teaching status' AS characteristic, NVL(teaching_status, 'Unknown') AS grp, measure_code,
       COUNT(*) AS n, ROUND(AVG(excess_readmission_ratio), 4) AS mean_err
FROM v_measure_analysis WHERE record_status = 'ANALYZABLE'
GROUP BY teaching_status, measure_code
UNION ALL
SELECT 'Ownership', NVL(ownership_control, 'Unknown'), measure_code, COUNT(*), ROUND(AVG(excess_readmission_ratio), 4)
FROM v_measure_analysis WHERE record_status = 'ANALYZABLE'
GROUP BY ownership_control, measure_code
UNION ALL
SELECT 'Urban/rural', NVL(urban_rural, 'Unknown'), measure_code, COUNT(*), ROUND(AVG(excess_readmission_ratio), 4)
FROM v_measure_analysis WHERE record_status = 'ANALYZABLE'
GROUP BY urban_rural, measure_code
UNION ALL
SELECT 'Bed size', NVL(bed_size_group, 'Unknown'), measure_code, COUNT(*), ROUND(AVG(excess_readmission_ratio), 4)
FROM v_measure_analysis WHERE record_status = 'ANALYZABLE'
GROUP BY bed_size_group, measure_code
ORDER BY 1, 3, 2;

-- Penalty incidence by bed size, with the number of measures each hospital is eligible on.
-- @name penalty_by_bed_size
WITH elig AS (
  SELECT facility_id, COUNT(*) AS eligible_measures
  FROM fact_readmission_measure
  WHERE eligible_discharges >= 25
  GROUP BY facility_id
)
SELECT NVL(h.bed_size_group, 'Unknown')                                    AS bed_size_group,
       COUNT(*)                                                            AS hospitals,
       ROUND(AVG(NVL(e.eligible_measures, 0)), 2)                          AS mean_eligible_measures,
       ROUND(100 * AVG(CASE WHEN p.payment_adjustment_factor < 1 THEN 1 ELSE 0 END), 1) AS pct_penalized,
       ROUND(AVG(p.payment_reduction_pct), 3)                              AS mean_reduction_pct
FROM fact_hospital_payment p
JOIN dim_hospital h ON h.facility_id = p.facility_id
LEFT JOIN elig e    ON e.facility_id = p.facility_id
GROUP BY h.bed_size_group
ORDER BY bed_size_group;

-- ---------------------------------------------------------------- 5. peer groups
-- @name peer_group_analysis
WITH hosp_err AS (
  SELECT facility_id, AVG(excess_readmission_ratio) AS mean_err
  FROM fact_readmission_measure
  WHERE record_status = 'ANALYZABLE'
  GROUP BY facility_id
)
SELECT p.peer_group,
       COUNT(*)                                                             AS hospitals,
       ROUND(MIN(p.dual_proportion), 4)                                     AS dual_min,
       ROUND(MAX(p.dual_proportion), 4)                                     AS dual_max,
       ROUND(AVG(e.mean_err), 4)                                            AS mean_err,
       ROUND(100 * AVG(CASE WHEN p.payment_adjustment_factor < 1 THEN 1 ELSE 0 END), 1) AS pct_penalized,
       ROUND(AVG(p.payment_reduction_pct), 3)                               AS mean_reduction_pct
FROM fact_hospital_payment p
LEFT JOIN hosp_err e ON e.facility_id = p.facility_id
GROUP BY p.peer_group
ORDER BY p.peer_group;

-- ---------------------------------------------------------------- 6. payment attribution
-- CMS FY2026 formula: reduction = min(3, NM x SUM_m[DRG ratio_m x (ERR_m - peer median_m)] x 100)
-- over measures with penalty_indicator = 'Y'. Check that the reconstruction matches the official value.
-- @name payment_reconstruction_check
WITH contrib AS (
  SELECT f.facility_id,
         SUM(CASE WHEN f.penalty_indicator = 'Y'
                  THEN f.drg_payment_ratio * (f.excess_readmission_ratio - f.peer_group_median_err) ELSE 0 END)
           * MAX(p.neutrality_modifier) * 100 AS raw_pp,
         MAX(p.payment_reduction_pct)          AS official_pct
  FROM fact_readmission_measure f
  JOIN fact_hospital_payment p ON p.facility_id = f.facility_id
  GROUP BY f.facility_id
)
SELECT COUNT(*)                                                         AS hospitals,
       ROUND(MAX(ABS(LEAST(raw_pp, 3) - official_pct)), 4)             AS max_abs_diff_pp,
       SUM(CASE WHEN ABS(LEAST(raw_pp, 3) - official_pct) <= 0.01 THEN 1 ELSE 0 END) AS within_0_01_pp
FROM contrib;

-- Pareto by measure: share of total payment-reduction percentage points (prioritisation, not dollars).
-- @name pareto_payment_contribution_by_measure
WITH hosp AS (
  SELECT f.facility_id, f.measure_code,
         CASE WHEN f.penalty_indicator = 'Y'
              THEN f.drg_payment_ratio * (f.excess_readmission_ratio - f.peer_group_median_err)
                   * p.neutrality_modifier * 100 ELSE 0 END AS raw_pp
  FROM fact_readmission_measure f
  JOIN fact_hospital_payment p ON p.facility_id = f.facility_id
), capped AS (
  -- scale each hospital's contributions so they sum to the 3% cap where it applies
  SELECT measure_code,
         raw_pp * CASE WHEN SUM(raw_pp) OVER (PARTITION BY facility_id) > 3
                       THEN 3 / SUM(raw_pp) OVER (PARTITION BY facility_id) ELSE 1 END AS contribution_pp
  FROM hosp
), by_measure AS (
  SELECT measure_code, SUM(contribution_pp) AS total_pp,
         SUM(CASE WHEN contribution_pp > 0 THEN 1 ELSE 0 END) AS hospitals_contributing
  FROM capped GROUP BY measure_code
)
SELECT measure_code, hospitals_contributing,
       ROUND(total_pp, 2)                                                          AS total_contribution_pp,
       ROUND(100 * RATIO_TO_REPORT(total_pp) OVER (), 2)                           AS share_pct,
       ROUND(100 * SUM(total_pp) OVER (ORDER BY total_pp DESC ROWS UNBOUNDED PRECEDING)
             / SUM(total_pp) OVER (), 2)                                           AS cumulative_pct,
       RANK() OVER (ORDER BY total_pp DESC)                                        AS pareto_rank
FROM by_measure
ORDER BY pareto_rank;

-- Pareto by measure using the derived excess-readmission estimate (positive excess only).
-- @name pareto_est_excess_readmissions_by_measure
WITH by_measure AS (
  SELECT measure_code,
         SUM(GREATEST(est_excess_readmissions, 0)) AS est_excess,
         COUNT(CASE WHEN est_excess_readmissions > 0 THEN 1 END) AS hospitals_with_excess
  FROM fact_readmission_measure
  WHERE record_status = 'ANALYZABLE'
  GROUP BY measure_code
)
SELECT measure_code, hospitals_with_excess,
       ROUND(est_excess, 1)                                                        AS est_excess_readmissions,
       ROUND(100 * RATIO_TO_REPORT(est_excess) OVER (), 2)                         AS share_pct,
       ROUND(100 * SUM(est_excess) OVER (ORDER BY est_excess DESC ROWS UNBOUNDED PRECEDING)
             / SUM(est_excess) OVER (), 2)                                         AS cumulative_pct
FROM by_measure
ORDER BY est_excess DESC;

-- ---------------------------------------------------------------- 7. performance tiers
-- Derived tiers relative to each hospital's own peer-group median (CASE expression).
-- @name performance_tier_by_measure
SELECT measure_code,
       CASE
         WHEN excess_readmission_ratio IS NULL                         THEN '0 No ERR published'
         WHEN eligible_discharges < 25 OR eligible_discharges IS NULL  THEN '1 Not eligible (<25 discharges)'
         WHEN excess_readmission_ratio <= peer_group_median_err        THEN '2 At or below peer median'
         WHEN excess_readmission_ratio <= peer_group_median_err * 1.05 THEN '3 Up to 5% above peer median'
         ELSE '4 More than 5% above peer median'
       END AS tier,
       COUNT(*) AS hospitals
FROM fact_readmission_measure
GROUP BY measure_code,
       CASE
         WHEN excess_readmission_ratio IS NULL                         THEN '0 No ERR published'
         WHEN eligible_discharges < 25 OR eligible_discharges IS NULL  THEN '1 Not eligible (<25 discharges)'
         WHEN excess_readmission_ratio <= peer_group_median_err        THEN '2 At or below peer median'
         WHEN excess_readmission_ratio <= peer_group_median_err * 1.05 THEN '3 Up to 5% above peer median'
         ELSE '4 More than 5% above peer median'
       END
ORDER BY measure_code, tier;
