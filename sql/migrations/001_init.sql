-- Generated from quantrisk.data.schema. Do not edit by hand.

CREATE TABLE credit_obligor (
	obligor_id VARCHAR(16) NOT NULL, 
	year INTEGER NOT NULL, 
	sector VARCHAR(24) NOT NULL, 
	leverage FLOAT NOT NULL, 
	interest_coverage FLOAT NOT NULL, 
	roa FLOAT NOT NULL, 
	current_ratio FLOAT NOT NULL, 
	log_assets FLOAT NOT NULL, 
	default_flag INTEGER, 
	ead FLOAT, 
	seniority VARCHAR(16), 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (obligor_id, year), 
	CONSTRAINT ck_obl_lev CHECK (leverage >= 0 AND leverage <= 1.5)
);

CREATE TABLE dq_check_result (
	run_id VARCHAR(40) NOT NULL, 
	check_name VARCHAR(48) NOT NULL, 
	table_name VARCHAR(32) NOT NULL, 
	severity VARCHAR(8) NOT NULL, 
	rows_checked INTEGER NOT NULL, 
	rows_failed INTEGER NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	detail TEXT, 
	PRIMARY KEY (run_id, check_name)
);

CREATE TABLE ledger_balance (
	book_id VARCHAR(16) NOT NULL, 
	instrument_id VARCHAR(32) NOT NULL, 
	as_of_date DATE NOT NULL, 
	face_amount FLOAT NOT NULL, 
	market_value FLOAT NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (book_id, instrument_id, as_of_date)
);

CREATE TABLE macro_series (
	series_id VARCHAR(16) NOT NULL, 
	period INTEGER NOT NULL, 
	value FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (series_id, period)
);

CREATE TABLE mkt_curve_point (
	curve_id VARCHAR(16) NOT NULL, 
	as_of_date DATE NOT NULL, 
	tenor_years FLOAT NOT NULL, 
	par_rate FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (curve_id, as_of_date, tenor_years), 
	CONSTRAINT ck_curve_rate CHECK (par_rate > -0.05 AND par_rate < 0.30), 
	CONSTRAINT ck_curve_tenor CHECK (tenor_years > 0)
);

CREATE INDEX ix_curve_date ON mkt_curve_point (as_of_date);

CREATE TABLE mkt_spread (
	spread_index VARCHAR(8) NOT NULL, 
	as_of_date DATE NOT NULL, 
	spread_bp FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (spread_index, as_of_date)
);

CREATE TABLE mkt_vol (
	vol_index VARCHAR(16) NOT NULL, 
	as_of_date DATE NOT NULL, 
	normal_vol_bp FLOAT NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (vol_index, as_of_date)
);

CREATE TABLE recon_log (
	recon_id VARCHAR(64) NOT NULL, 
	run_id VARCHAR(40) NOT NULL, 
	as_of_date DATE NOT NULL, 
	book_id VARCHAR(16) NOT NULL, 
	instrument_id VARCHAR(32), 
	measure VARCHAR(24) NOT NULL, 
	value_risk FLOAT, 
	value_ledger FLOAT, 
	difference FLOAT, 
	tolerance FLOAT NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	owner VARCHAR(32), 
	comment TEXT, 
	resolved_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (recon_id)
);

CREATE TABLE ref_instrument (
	instrument_id VARCHAR(32) NOT NULL, 
	description VARCHAR(120) NOT NULL, 
	asset_class VARCHAR(8) NOT NULL, 
	issuer VARCHAR(64) NOT NULL, 
	sector VARCHAR(32) NOT NULL, 
	rating VARCHAR(8) NOT NULL, 
	spread_index VARCHAR(8), 
	currency VARCHAR(3) NOT NULL, 
	coupon FLOAT NOT NULL, 
	issue_date DATE NOT NULL, 
	maturity_date DATE NOT NULL, 
	frequency INTEGER NOT NULL, 
	day_count VARCHAR(12) NOT NULL, 
	spread_basis_bp FLOAT NOT NULL, 
	vega_per_100 FLOAT NOT NULL, 
	PRIMARY KEY (instrument_id), 
	CONSTRAINT ck_instr_coupon CHECK (coupon >= 0 AND coupon < 0.25), 
	CONSTRAINT ck_instr_freq CHECK (frequency IN (1, 2, 4, 12)), 
	CONSTRAINT ck_instr_dates CHECK (maturity_date > issue_date)
);

CREATE TABLE risk_run (
	run_id VARCHAR(40) NOT NULL, 
	as_of_date DATE NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITHOUT TIME ZONE, 
	git_sha VARCHAR(40), 
	config_hash VARCHAR(16) NOT NULL, 
	code_version VARCHAR(16) NOT NULL, 
	message TEXT, 
	PRIMARY KEY (run_id)
);

CREATE TABLE backtest_result (
	run_id VARCHAR(40) NOT NULL, 
	test_date DATE NOT NULL, 
	pnl FLOAT NOT NULL, 
	var_99 FLOAT NOT NULL, 
	exception INTEGER NOT NULL, 
	PRIMARY KEY (run_id, test_date), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE credit_calibration (
	run_id VARCHAR(40) NOT NULL, 
	grade VARCHAR(4) NOT NULL, 
	n INTEGER NOT NULL, 
	defaults INTEGER NOT NULL, 
	observed_dr FLOAT NOT NULL, 
	predicted_pd FLOAT NOT NULL, 
	ttc_pd FLOAT NOT NULL, 
	p_value FLOAT NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	PRIMARY KEY (run_id, grade), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE credit_grade_summary (
	run_id VARCHAR(40) NOT NULL, 
	scenario_id VARCHAR(16) NOT NULL, 
	grade VARCHAR(4) NOT NULL, 
	obligors INTEGER NOT NULL, 
	ead FLOAT NOT NULL, 
	pd_ttc FLOAT NOT NULL, 
	pd_scenario FLOAT NOT NULL, 
	expected_loss FLOAT NOT NULL, 
	PRIMARY KEY (run_id, scenario_id, grade), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE credit_pd (
	run_id VARCHAR(40) NOT NULL, 
	obligor_id VARCHAR(16) NOT NULL, 
	scenario_id VARCHAR(16) NOT NULL, 
	score FLOAT NOT NULL, 
	grade VARCHAR(4) NOT NULL, 
	pd_ttc FLOAT NOT NULL, 
	pd_scenario FLOAT NOT NULL, 
	lgd FLOAT NOT NULL, 
	ead FLOAT NOT NULL, 
	expected_loss FLOAT NOT NULL, 
	PRIMARY KEY (run_id, obligor_id, scenario_id), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE krd_result (
	run_id VARCHAR(40) NOT NULL, 
	book_id VARCHAR(16) NOT NULL, 
	instrument_id VARCHAR(32) NOT NULL, 
	tenor_years FLOAT NOT NULL, 
	krd FLOAT NOT NULL, 
	dv01 FLOAT NOT NULL, 
	PRIMARY KEY (run_id, book_id, instrument_id, tenor_years), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE limit_check (
	run_id VARCHAR(40) NOT NULL, 
	limit_id VARCHAR(24) NOT NULL, 
	description VARCHAR(120) NOT NULL, 
	value FLOAT NOT NULL, 
	amber FLOAT NOT NULL, 
	red FLOAT NOT NULL, 
	utilization FLOAT NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	PRIMARY KEY (run_id, limit_id), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE mkt_price (
	instrument_id VARCHAR(32) NOT NULL, 
	as_of_date DATE NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	clean_price FLOAT NOT NULL, 
	price_date DATE NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (instrument_id, as_of_date, source), 
	CONSTRAINT ck_price_pos CHECK (clean_price > 0), 
	FOREIGN KEY(instrument_id) REFERENCES ref_instrument (instrument_id)
);

CREATE TABLE model_validation (
	run_id VARCHAR(40) NOT NULL, 
	model_id VARCHAR(24) NOT NULL, 
	test_name VARCHAR(48) NOT NULL, 
	value FLOAT, 
	threshold VARCHAR(48), 
	status VARCHAR(8) NOT NULL, 
	detail TEXT, 
	PRIMARY KEY (run_id, model_id, test_name), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE pnl_vector (
	run_id VARCHAR(40) NOT NULL, 
	scenario_date DATE NOT NULL, 
	pnl FLOAT NOT NULL, 
	PRIMARY KEY (run_id, scenario_date), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE pos_position (
	book_id VARCHAR(16) NOT NULL, 
	instrument_id VARCHAR(32) NOT NULL, 
	as_of_date DATE NOT NULL, 
	face_amount FLOAT NOT NULL, 
	source_system VARCHAR(32) NOT NULL, 
	load_id VARCHAR(40) NOT NULL, 
	PRIMARY KEY (book_id, instrument_id, as_of_date), 
	FOREIGN KEY(instrument_id) REFERENCES ref_instrument (instrument_id)
);

CREATE TABLE position_valuation (
	run_id VARCHAR(40) NOT NULL, 
	book_id VARCHAR(16) NOT NULL, 
	instrument_id VARCHAR(32) NOT NULL, 
	face_amount FLOAT NOT NULL, 
	clean_price FLOAT NOT NULL, 
	dirty_price FLOAT NOT NULL, 
	accrued FLOAT NOT NULL, 
	market_value FLOAT NOT NULL, 
	ytm FLOAT NOT NULL, 
	mod_duration FLOAT NOT NULL, 
	eff_duration FLOAT NOT NULL, 
	convexity FLOAT NOT NULL, 
	spread_duration FLOAT NOT NULL, 
	dv01 FLOAT NOT NULL, 
	vendor_price FLOAT, 
	PRIMARY KEY (run_id, book_id, instrument_id), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE risk_result (
	run_id VARCHAR(40) NOT NULL, 
	scope VARCHAR(16) NOT NULL, 
	metric VARCHAR(16) NOT NULL, 
	method VARCHAR(16) NOT NULL, 
	confidence FLOAT NOT NULL, 
	horizon_days INTEGER NOT NULL, 
	value FLOAT NOT NULL, 
	PRIMARY KEY (run_id, scope, metric, method, confidence, horizon_days), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE run_summary (
	run_id VARCHAR(40) NOT NULL, 
	key VARCHAR(64) NOT NULL, 
	value_num FLOAT, 
	value_text TEXT, 
	PRIMARY KEY (run_id, key), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE stress_result (
	run_id VARCHAR(40) NOT NULL, 
	scenario_id VARCHAR(24) NOT NULL, 
	book_id VARCHAR(16) NOT NULL, 
	instrument_id VARCHAR(32) NOT NULL, 
	rates_pnl FLOAT NOT NULL, 
	spread_pnl FLOAT NOT NULL, 
	vol_pnl FLOAT NOT NULL, 
	total_pnl FLOAT NOT NULL, 
	scenario_hash VARCHAR(16) NOT NULL, 
	PRIMARY KEY (run_id, scenario_id, book_id, instrument_id), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

CREATE TABLE stress_scenario (
	run_id VARCHAR(40) NOT NULL, 
	scenario_id VARCHAR(24) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description TEXT, 
	shock_vector TEXT NOT NULL, 
	PRIMARY KEY (run_id, scenario_id), 
	FOREIGN KEY(run_id) REFERENCES risk_run (run_id)
);

