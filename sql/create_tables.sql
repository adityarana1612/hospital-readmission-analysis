-- =============================================================================
-- create_tables.sql
-- Oracle Database 19c or later (developed and tested on Oracle AI Database 26ai
-- Free 23.26.3). Run as the project schema user (HRRA) in FREEPDB1.
-- Re-runnable: drops existing project objects first.
--
-- Model
--   STG_HRRP_HOSPITAL         raw CMS provider file, all text, exactly as published
--   DIM_MEASURE               6 HRRP clinical measures
--   DIM_HOSPITAL              one row per HRRP hospital (CCN) + attributes
--   FACT_HOSPITAL_PAYMENT     official FY2026 payment adjustment (Supplemental file)
--   FACT_READMISSION_MEASURE  one row per hospital x measure x performance period
--   V_MEASURE_ANALYSIS        analysis view joining the above
-- =============================================================================

BEGIN
  FOR t IN (SELECT object_name, object_type FROM user_objects
            WHERE object_name IN ('V_MEASURE_ANALYSIS', 'FACT_READMISSION_MEASURE', 'FACT_HOSPITAL_PAYMENT',
                                  'DIM_HOSPITAL', 'DIM_MEASURE', 'STG_HRRP_HOSPITAL')
              AND object_type IN ('TABLE', 'VIEW')
            ORDER BY CASE object_type WHEN 'VIEW' THEN 0 ELSE 1 END,
                     CASE object_name WHEN 'FACT_READMISSION_MEASURE' THEN 1 WHEN 'FACT_HOSPITAL_PAYMENT' THEN 2
                                      ELSE 3 END) LOOP
    EXECUTE IMMEDIATE 'DROP ' || t.object_type || ' ' || t.object_name ||
                      CASE t.object_type WHEN 'TABLE' THEN ' CASCADE CONSTRAINTS PURGE' END;
  END LOOP;
END;
/

-- -----------------------------------------------------------------------------
-- Staging: no constraints, so data-quality checks can find problems in raw data.
-- -----------------------------------------------------------------------------
CREATE TABLE stg_hrrp_hospital (
  facility_name               VARCHAR2(200),
  facility_id                 VARCHAR2(20),
  state                       VARCHAR2(10),
  measure_name                VARCHAR2(50),
  number_of_discharges        VARCHAR2(30),
  footnote                    VARCHAR2(50),
  excess_readmission_ratio    VARCHAR2(30),
  predicted_readmission_rate  VARCHAR2(30),
  expected_readmission_rate   VARCHAR2(30),
  number_of_readmissions      VARCHAR2(30),
  start_date                  VARCHAR2(10),
  end_date                    VARCHAR2(10),
  loaded_at                   TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- -----------------------------------------------------------------------------
-- Dimensions
-- -----------------------------------------------------------------------------
CREATE TABLE dim_measure (
  measure_code   VARCHAR2(10)  CONSTRAINT pk_dim_measure PRIMARY KEY,
  measure_label  VARCHAR2(100) NOT NULL
);

CREATE TABLE dim_hospital (
  facility_id            VARCHAR2(6)   CONSTRAINT pk_dim_hospital PRIMARY KEY,
  facility_name          VARCHAR2(200) NOT NULL,
  city                   VARCHAR2(100),
  state                  VARCHAR2(2)   NOT NULL,
  county                 VARCHAR2(100),
  hospital_type          VARCHAR2(100),
  hospital_ownership     VARCHAR2(100),
  emergency_services     VARCHAR2(3),
  overall_rating         NUMBER(1),
  census_region          VARCHAR2(30),
  urban_rural            VARCHAR2(20),
  resident_to_bed_ratio  NUMBER(8,4),
  teaching_status        VARCHAR2(20),   -- derived proxy: resident_to_bed_ratio > 0
  beds                   NUMBER(6),
  bed_size_group         VARCHAR2(20),
  dsh_patient_pct        NUMBER(8,4),
  ownership_control      VARCHAR2(20),
  in_general_info        CHAR(1) NOT NULL,
  in_impact_file         CHAR(1) NOT NULL,
  in_supplemental        CHAR(1) NOT NULL,
  CONSTRAINT ck_hosp_ccn      CHECK (REGEXP_LIKE(facility_id, '^[0-9]{2}[0-9A-Z][0-9]{3}$')),
  CONSTRAINT ck_hosp_rating   CHECK (overall_rating BETWEEN 1 AND 5),
  CONSTRAINT ck_hosp_teach    CHECK (teaching_status IN ('Teaching', 'Non-teaching')),
  CONSTRAINT ck_hosp_flags    CHECK (in_general_info IN ('Y','N') AND in_impact_file IN ('Y','N')
                                     AND in_supplemental IN ('Y','N'))
);

-- -----------------------------------------------------------------------------
-- Facts
-- -----------------------------------------------------------------------------
CREATE TABLE fact_hospital_payment (
  facility_id                VARCHAR2(6)  CONSTRAINT pk_fact_payment PRIMARY KEY
                                          CONSTRAINT fk_payment_hosp REFERENCES dim_hospital (facility_id),
  program_year               VARCHAR2(6)  NOT NULL,
  payment_adjustment_factor  NUMBER(5,4)  NOT NULL,
  payment_reduction_pct      NUMBER(5,2)  NOT NULL,
  dual_proportion            NUMBER(6,4),
  peer_group                 NUMBER(1),
  neutrality_modifier        NUMBER(6,4),
  CONSTRAINT ck_pay_factor CHECK (payment_adjustment_factor BETWEEN 0.97 AND 1),
  CONSTRAINT ck_pay_pct    CHECK (payment_reduction_pct BETWEEN 0 AND 3),
  CONSTRAINT ck_pay_dual   CHECK (dual_proportion BETWEEN 0 AND 1),
  CONSTRAINT ck_pay_peer   CHECK (peer_group BETWEEN 1 AND 5)
);

CREATE TABLE fact_readmission_measure (
  facility_id                 VARCHAR2(6)  NOT NULL CONSTRAINT fk_meas_hosp REFERENCES dim_hospital (facility_id),
  measure_code                VARCHAR2(10) NOT NULL CONSTRAINT fk_meas_measure REFERENCES dim_measure (measure_code),
  start_date                  DATE         NOT NULL,
  end_date                    DATE         NOT NULL,
  number_of_discharges        NUMBER(7),
  eligible_discharges         NUMBER(7),      -- Supplemental file; >= 25 needed to count toward penalty
  footnote                    VARCHAR2(50),
  excess_readmission_ratio    NUMBER(6,4),
  predicted_readmission_rate  NUMBER(7,4),
  expected_readmission_rate   NUMBER(7,4),
  number_of_readmissions      NUMBER(7),
  readmissions_suppressed     CHAR(1)      NOT NULL,
  peer_group_median_err       NUMBER(6,4),
  penalty_indicator           CHAR(1),        -- official CMS field
  drg_payment_ratio           NUMBER(8,4),
  est_excess_readmissions     NUMBER(10,4),   -- derived estimate, not a CMS count
  record_status               VARCHAR2(20) NOT NULL,
  CONSTRAINT pk_fact_measure  PRIMARY KEY (facility_id, measure_code, start_date, end_date),
  CONSTRAINT ck_meas_period   CHECK (end_date > start_date),
  CONSTRAINT ck_meas_err      CHECK (excess_readmission_ratio BETWEEN 0 AND 3),
  CONSTRAINT ck_meas_rates    CHECK (predicted_readmission_rate BETWEEN 0 AND 100
                                     AND expected_readmission_rate BETWEEN 0 AND 100),
  CONSTRAINT ck_meas_status   CHECK (record_status IN ('ANALYZABLE', 'EXCLUDED_NO_ERR', 'REVIEW')),
  CONSTRAINT ck_meas_penalty  CHECK (penalty_indicator IN ('Y', 'N')),
  CONSTRAINT ck_meas_suppr    CHECK (readmissions_suppressed IN ('Y', 'N')),
  CONSTRAINT ck_meas_err_stat CHECK (record_status <> 'ANALYZABLE' OR excess_readmission_ratio IS NOT NULL)
);

-- Indexes for the filters and joins used in the analysis scripts.
CREATE INDEX ix_meas_measure ON fact_readmission_measure (measure_code, record_status);
CREATE INDEX ix_meas_period  ON fact_readmission_measure (start_date, end_date);
CREATE INDEX ix_hosp_state   ON dim_hospital (state);

-- -----------------------------------------------------------------------------
-- Analysis view
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_measure_analysis AS
SELECT f.facility_id, h.facility_name, h.state, h.census_region, h.urban_rural, h.teaching_status,
       h.ownership_control, h.bed_size_group, f.measure_code, m.measure_label, f.start_date, f.end_date,
       f.excess_readmission_ratio, f.predicted_readmission_rate, f.expected_readmission_rate,
       f.eligible_discharges, f.peer_group_median_err, f.penalty_indicator, f.drg_payment_ratio,
       f.est_excess_readmissions, f.record_status,
       p.payment_adjustment_factor, p.payment_reduction_pct, p.dual_proportion, p.peer_group, p.neutrality_modifier
FROM fact_readmission_measure f
JOIN dim_hospital h ON h.facility_id = f.facility_id
JOIN dim_measure  m ON m.measure_code = f.measure_code
LEFT JOIN fact_hospital_payment p ON p.facility_id = f.facility_id;
