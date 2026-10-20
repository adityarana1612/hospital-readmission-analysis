-- =============================================================================
-- data_quality_checks.sql
-- Missing-value, duplicate, validity and reconciliation checks.
-- Each query is labelled "-- @name <id>". The Python runner saves its result to
-- reports/sql_results/data_quality_checks__<id>.csv. In SQL Developer, run with
-- F5 (Run Script) or run single queries with Ctrl+Enter.
-- Convention: checks named *_violations should return 0 rows / a count of 0.
-- =============================================================================

-- @name row_counts
SELECT 'STG_HRRP_HOSPITAL' AS table_name, COUNT(*) AS row_count FROM stg_hrrp_hospital
UNION ALL SELECT 'DIM_HOSPITAL', COUNT(*) FROM dim_hospital
UNION ALL SELECT 'DIM_MEASURE', COUNT(*) FROM dim_measure
UNION ALL SELECT 'FACT_HOSPITAL_PAYMENT', COUNT(*) FROM fact_hospital_payment
UNION ALL SELECT 'FACT_READMISSION_MEASURE', COUNT(*) FROM fact_readmission_measure;

-- ---------------------------------------------------------------- staging (raw)
-- @name stg_missing_values
SELECT COUNT(*)                                                             AS total_rows,
       SUM(CASE WHEN facility_id IS NULL THEN 1 ELSE 0 END)                 AS missing_facility_id,
       SUM(CASE WHEN excess_readmission_ratio IS NULL OR excess_readmission_ratio = 'N/A' THEN 1 ELSE 0 END) AS missing_err,
       SUM(CASE WHEN number_of_discharges IS NULL OR number_of_discharges = 'N/A' THEN 1 ELSE 0 END) AS missing_discharges,
       SUM(CASE WHEN number_of_readmissions = 'Too Few to Report' THEN 1 ELSE 0 END) AS readmissions_suppressed,
       SUM(CASE WHEN number_of_readmissions IS NULL OR number_of_readmissions = 'N/A' THEN 1 ELSE 0 END) AS missing_readmissions
FROM stg_hrrp_hospital;

-- @name stg_invalid_ccn_violations
SELECT facility_id, facility_name, COUNT(*) AS rows_affected
FROM stg_hrrp_hospital
WHERE facility_id IS NULL OR NOT REGEXP_LIKE(facility_id, '^[0-9]{2}[0-9A-Z][0-9]{3}$')
GROUP BY facility_id, facility_name;

-- @name stg_duplicate_key_violations
SELECT facility_id, measure_name, start_date, end_date, COUNT(*) AS copies
FROM stg_hrrp_hospital
GROUP BY facility_id, measure_name, start_date, end_date
HAVING COUNT(*) > 1;

-- VALIDATE_CONVERSION (12.2+) finds text that cannot be converted to a number/date.
-- 'N/A' is CMS's documented missing-value token, so it is excluded here and counted above.
-- @name stg_unparseable_value_violations
SELECT 'EXCESS_READMISSION_RATIO' AS column_name, excess_readmission_ratio AS raw_value, COUNT(*) AS n
FROM stg_hrrp_hospital
WHERE excess_readmission_ratio NOT IN ('N/A') AND VALIDATE_CONVERSION(excess_readmission_ratio AS NUMBER) = 0
GROUP BY excess_readmission_ratio
UNION ALL
SELECT 'NUMBER_OF_READMISSIONS', number_of_readmissions, COUNT(*)
FROM stg_hrrp_hospital
WHERE number_of_readmissions NOT IN ('Too Few to Report', 'N/A')
  AND VALIDATE_CONVERSION(number_of_readmissions AS NUMBER) = 0
GROUP BY number_of_readmissions
UNION ALL
SELECT 'START_DATE', start_date, COUNT(*)
FROM stg_hrrp_hospital
WHERE VALIDATE_CONVERSION(start_date AS DATE, 'MM/DD/YYYY') = 0
GROUP BY start_date;

-- @name stg_footnotes_on_missing_err
SELECT NVL(footnote, '(none)') AS footnote, COUNT(*) AS rows_without_err
FROM stg_hrrp_hospital
WHERE excess_readmission_ratio IS NULL OR excess_readmission_ratio = 'N/A'
GROUP BY footnote
ORDER BY rows_without_err DESC;

-- @name stg_reporting_periods
SELECT start_date, end_date, COUNT(*) AS rows_in_period, COUNT(DISTINCT facility_id) AS hospitals
FROM stg_hrrp_hospital
GROUP BY start_date, end_date;

-- ---------------------------------------------------------------- reconciliation
-- @name stage_to_fact_reconciliation
SELECT s.stg_rows, f.fact_rows, s.stg_rows - f.fact_rows AS difference,
       s.stg_with_err, f.fact_analyzable
FROM (SELECT COUNT(*) AS stg_rows,
             SUM(CASE WHEN excess_readmission_ratio <> 'N/A' THEN 1 ELSE 0 END) AS stg_with_err
      FROM stg_hrrp_hospital) s
CROSS JOIN
     (SELECT COUNT(*) AS fact_rows,
             SUM(CASE WHEN record_status = 'ANALYZABLE' THEN 1 ELSE 0 END) AS fact_analyzable
      FROM fact_readmission_measure) f;

-- Values must survive the Python cleaning unchanged (text -> number).
-- @name err_value_mismatch_violations
SELECT COUNT(*) AS mismatches
FROM stg_hrrp_hospital s
JOIN dim_measure m
  ON s.measure_name = 'READM-30-' || REPLACE(m.measure_code, '_', '-') || '-HRRP'
JOIN fact_readmission_measure f
  ON f.facility_id = s.facility_id AND f.measure_code = m.measure_code
 AND f.start_date = TO_DATE(s.start_date, 'MM/DD/YYYY')
WHERE NVL(TO_NUMBER(s.excess_readmission_ratio DEFAULT NULL ON CONVERSION ERROR), -1)
      <> NVL(f.excess_readmission_ratio, -1);

-- ---------------------------------------------------------------- fact checks
-- @name fact_missing_by_status
SELECT record_status, COUNT(*) AS n_rows,
       SUM(CASE WHEN excess_readmission_ratio IS NULL THEN 1 ELSE 0 END) AS null_err,
       SUM(CASE WHEN eligible_discharges IS NULL THEN 1 ELSE 0 END)      AS null_eligible_discharges,
       SUM(CASE WHEN penalty_indicator IS NULL THEN 1 ELSE 0 END)        AS null_penalty_indicator
FROM fact_readmission_measure
GROUP BY record_status;

-- @name readmissions_exceed_discharges_violations
SELECT facility_id, measure_code, number_of_readmissions, number_of_discharges
FROM fact_readmission_measure
WHERE number_of_readmissions > number_of_discharges;

-- Predicted / expected must equal ERR (CMS definition), allowing for 4-dp rounding.
-- @name err_definition_violations
SELECT facility_id, measure_code, excess_readmission_ratio,
       ROUND(predicted_readmission_rate / expected_readmission_rate, 4) AS recomputed_err
FROM fact_readmission_measure
WHERE record_status = 'ANALYZABLE'
  AND ABS(excess_readmission_ratio - predicted_readmission_rate / expected_readmission_rate) > 0.0005;

-- CMS rule: penalty = 'Y' iff eligible discharges >= 25 AND ERR > peer-group median ERR.
-- @name penalty_rule_consistency
SELECT CASE WHEN f.eligible_discharges >= 25 AND f.excess_readmission_ratio > f.peer_group_median_err
            THEN 'Y' ELSE 'N' END AS rule_result,
       f.penalty_indicator,
       COUNT(*) AS n
FROM fact_readmission_measure f
WHERE f.penalty_indicator IS NOT NULL AND f.excess_readmission_ratio IS NOT NULL
  AND f.peer_group_median_err IS NOT NULL
GROUP BY CASE WHEN f.eligible_discharges >= 25 AND f.excess_readmission_ratio > f.peer_group_median_err
              THEN 'Y' ELSE 'N' END, f.penalty_indicator
ORDER BY 1, 2;

-- Reduction % should equal (1 - factor) x 100.
-- @name payment_factor_pct_violations
SELECT facility_id, payment_adjustment_factor, payment_reduction_pct
FROM fact_hospital_payment
WHERE ABS((1 - payment_adjustment_factor) * 100 - payment_reduction_pct) > 0.006;

-- @name hospitals_without_payment_by_state
SELECT h.state, COUNT(*) AS hrrp_hospitals_without_payment_record
FROM dim_hospital h
WHERE NOT EXISTS (SELECT 1 FROM fact_hospital_payment p WHERE p.facility_id = h.facility_id)
GROUP BY h.state
ORDER BY 2 DESC;

-- @name hospital_attribute_coverage
SELECT COUNT(*) AS hospitals,
       SUM(CASE WHEN in_general_info = 'Y' THEN 1 ELSE 0 END) AS in_general_info,
       SUM(CASE WHEN in_impact_file  = 'Y' THEN 1 ELSE 0 END) AS in_impact_file,
       SUM(CASE WHEN in_supplemental = 'Y' THEN 1 ELSE 0 END) AS in_supplemental,
       SUM(CASE WHEN teaching_status IS NULL THEN 1 ELSE 0 END) AS missing_teaching_status
FROM dim_hospital;
