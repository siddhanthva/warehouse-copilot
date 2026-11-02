-- SYNTHETIC data for local development and the integration tests only.
-- It mirrors the placeholder schema in tests/fixtures/dbt_target. Your real warehouse is
-- loaded with dbt (Level 2); never commit real data you don't have rights to publish.
CREATE SCHEMA IF NOT EXISTS marts;
DROP TABLE IF EXISTS marts.fct_inpatient_drg_payments, marts.dim_hospitals, marts.dim_drg, marts.fct_hospital_financials;

CREATE TABLE marts.dim_hospitals (provider_ccn text PRIMARY KEY, hospital_name text, city text, state text, zip_code text, ownership_type text);
CREATE TABLE marts.dim_drg (drg_code text PRIMARY KEY, drg_description text, mdc text);
CREATE TABLE marts.fct_inpatient_drg_payments (provider_ccn text, drg_code text, year int, total_discharges int,
  avg_submitted_charges numeric, avg_total_payment numeric, avg_medicare_payment numeric, total_medicare_payment numeric);
CREATE TABLE marts.fct_hospital_financials (provider_ccn text, fiscal_year int, net_patient_revenue numeric,
  operating_expenses numeric, operating_margin numeric, total_beds int);

INSERT INTO marts.dim_hospitals VALUES
 ('450001','Sample General Hospital','Houston','TX','77001','nonprofit'),
 ('450002','Example Medical Center','Dallas','TX','75201','proprietary'),
 ('050003','Demo Community Hospital','Los Angeles','CA','90001','government');
INSERT INTO marts.dim_drg VALUES ('291','HEART FAILURE AND SHOCK WITH MCC','05'), ('470','MAJOR HIP AND KNEE JOINT REPLACEMENT','08');
INSERT INTO marts.fct_inpatient_drg_payments
SELECT h.provider_ccn, d.drg_code, y, 20 + (y % 7) * 3,
       30000 + (y - 2019) * 900 + length(h.hospital_name) * 100,
       12000 + (y - 2019) * 300 + length(h.hospital_name) * 40,
       10000 + (y - 2019) * 250 + length(h.hospital_name) * 35,
       (10000 + (y - 2019) * 250 + length(h.hospital_name) * 35) * (20 + (y % 7) * 3)
FROM marts.dim_hospitals h CROSS JOIN marts.dim_drg d CROSS JOIN generate_series(2019, 2023) y;
INSERT INTO marts.fct_hospital_financials
SELECT provider_ccn, y, 5e8 + y * 1000, 4.6e8 + y * 900, NULL, 300 FROM marts.dim_hospitals CROSS JOIN generate_series(2019, 2023) y;
UPDATE marts.fct_hospital_financials SET operating_margin = (net_patient_revenue - operating_expenses) / net_patient_revenue;
