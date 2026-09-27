-- Curated reporting views for Power BI / Streamlit / ad hoc SQL.
-- Reporting tools read these views only; they never recalculate risk.

CREATE OR REPLACE VIEW v_latest_run AS
SELECT DISTINCT ON (as_of_date) run_id, as_of_date, status, started_at, finished_at, git_sha, config_hash
FROM risk_run
WHERE status = 'SUCCEEDED'
ORDER BY as_of_date, started_at DESC;

CREATE OR REPLACE VIEW v_daily_risk_summary AS
SELECT
    r.as_of_date,
    r.run_id,
    (SELECT SUM(market_value) FROM position_valuation p WHERE p.run_id = r.run_id)                  AS market_value,
    (SELECT SUM(dv01) FROM position_valuation p WHERE p.run_id = r.run_id)                          AS dv01,
    (SELECT value FROM risk_result x WHERE x.run_id = r.run_id AND x.scope = 'TOTAL' AND x.metric = 'VaR'
        AND x.method = 'historical' AND x.confidence = 0.99 AND x.horizon_days = 1)                AS var_99_1d,
    (SELECT value FROM risk_result x WHERE x.run_id = r.run_id AND x.scope = 'TOTAL' AND x.metric = 'ES'
        AND x.method = 'historical' AND x.confidence = 0.975 AND x.horizon_days = 1)               AS es_975_1d,
    (SELECT MIN(t) FROM (SELECT SUM(total_pnl) AS t FROM stress_result s WHERE s.run_id = r.run_id
        GROUP BY scenario_id) q)                                                                     AS worst_stress_pnl,
    (SELECT SUM(expected_loss) FROM credit_grade_summary c WHERE c.run_id = r.run_id
        AND c.scenario_id = 'SEVERE')                                                                AS el_severe,
    (SELECT COUNT(*) FROM limit_check l WHERE l.run_id = r.run_id AND l.status <> 'GREEN')           AS limits_flagged,
    (SELECT COUNT(*) FROM recon_log k WHERE k.run_id = r.run_id AND k.status = 'BREAK')              AS recon_breaks
FROM v_latest_run r;

CREATE OR REPLACE VIEW v_krd_by_tenor AS
SELECT r.as_of_date, k.run_id, k.book_id, k.tenor_years, SUM(k.dv01) AS dv01
FROM krd_result k JOIN v_latest_run r USING (run_id)
GROUP BY r.as_of_date, k.run_id, k.book_id, k.tenor_years;

CREATE OR REPLACE VIEW v_stress_by_scenario AS
SELECT r.as_of_date, s.run_id, s.scenario_id, sc.name,
       SUM(s.rates_pnl) AS rates_pnl, SUM(s.spread_pnl) AS spread_pnl,
       SUM(s.vol_pnl) AS vol_pnl, SUM(s.total_pnl) AS total_pnl
FROM stress_result s
JOIN v_latest_run r USING (run_id)
JOIN stress_scenario sc ON sc.run_id = s.run_id AND sc.scenario_id = s.scenario_id
GROUP BY r.as_of_date, s.run_id, s.scenario_id, sc.name;

CREATE OR REPLACE VIEW v_limit_status AS
SELECT r.as_of_date, l.* FROM limit_check l JOIN v_latest_run r USING (run_id);
