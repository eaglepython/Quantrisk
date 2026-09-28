"""QuantRisk Streamlit dashboard.

Reads only persisted results from the database (same tables the report and
Power BI use). Run with:  streamlit run dashboards/app.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from quantrisk.config import load_settings
from quantrisk.data import db, schema

st.set_page_config(page_title="QuantRisk Live Ops", layout="wide")

ACCENT, CURVE, BAD, GOOD, SOFT = "#5DA9E9", "#F4B942", "#F05D5E", "#3ED0A3", "#A9C7F2"
STATUS_COLOR = {"GREEN": GOOD, "AMBER": CURVE, "RED": BAD, "PASS": GOOD, "WATCH": CURVE, "FAIL": BAD,
                "MATCH": GOOD, "BREAK": BAD}

st.markdown(
    """
    <style>
    :root {
        --bg: #07111f;
        --panel: rgba(18, 31, 48, 0.85);
        --panel-strong: rgba(10, 18, 31, 0.95);
        --line: rgba(141, 168, 204, 0.2);
        --text: #edf4ff;
        --muted: #9bb2d1;
        --accent: #5DA9E9;
        --accent-2: #8DE3FF;
        --warning: #F4B942;
        --success: #3ED0A3;
        --danger: #F05D5E;
    }
    html, body, [data-testid="stAppViewContainer"] {
        background: linear-gradient(135deg, #06101a 0%, #0b1d2e 45%, #091520 100%);
        color: var(--text);
    }
    .stApp {
        background: radial-gradient(circle at top left, rgba(93,169,233,0.18), transparent 22%),
                    radial-gradient(circle at bottom right, rgba(164, 125, 255, 0.12), transparent 25%);
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(8,16,26,0.98), rgba(14,24,39,0.96));
        border-right: 1px solid var(--line);
    }
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    .glass {
        background: linear-gradient(180deg, rgba(15,27,42,0.82), rgba(10,18,29,0.88));
        border: 1px solid var(--line);
        border-radius: 18px;
        box-shadow: 0 18px 40px rgba(0,0,0,0.22);
        padding: 1.2rem 1.25rem;
    }
    .hero {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 1rem;
        padding: 1.2rem 1.4rem;
        background: linear-gradient(135deg, rgba(93,169,233,0.12), rgba(19,33,53,0.85));
        border: 1px solid rgba(93,169,233,0.35);
        border-radius: 20px;
        box-shadow: inset 0 0 0 1px rgba(255,255,255,0.03);
    }
    .hero-badge {
        display: inline-flex;
        align-items: center;
        padding: 0.35rem 0.7rem;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        background: rgba(61,208,163,0.12);
        color: var(--success);
        border: 1px solid rgba(61,208,163,0.35);
    }
    .kpi-card {
        background: linear-gradient(180deg, rgba(12,22,35,0.9), rgba(14,26,41,0.9));
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 0.9rem 1rem;
        box-shadow: 0 12px 24px rgba(0,0,0,0.15);
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.7rem !important;
        font-weight: 700 !important;
        color: var(--text) !important;
    }
    div[data-testid="stMetricDelta"] {
        font-size: 0.74rem !important;
    }
    .sidebar-title {
        font-size: 1.3rem;
        font-weight: 800;
        letter-spacing: 0.03em;
        color: var(--text);
    }
    .muted {
        color: var(--muted);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def engine():
    return db.make_engine(load_settings().database_url)


@st.cache_data(ttl=300)
def runs() -> pd.DataFrame:
    return db.read_sql(engine(), "select run_id, as_of_date, status, started_at, git_sha, config_hash "
                                 "from risk_run order by started_at desc")


@st.cache_data(ttl=300)
def table(name: str, run_id: str) -> pd.DataFrame:
    return db.read_df(engine(), schema.metadata.tables[name], run_id=run_id)


@st.cache_data(ttl=300)
def summary(run_id: str) -> dict:
    df = table("run_summary", run_id)
    return {r.key: (r.value_text if isinstance(r.value_text, str) else r.value_num) for r in df.itertuples()}


def usd_m(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x) / 1e6:,.2f}M"


def pill(status: str) -> str:
    c = STATUS_COLOR.get(status, "#566476")
    return f"<span style='background:{c}22;color:{c};padding:2px 8px;border-radius:10px;font-weight:700;font-size:12px'>{status}</span>"


r = runs()
if r.empty:
    st.warning("No runs yet. Run `quantrisk run --as-of YYYY-MM-DD --with-sample` first.")
    st.stop()

with st.sidebar:
    st.markdown("<div class='sidebar-title'>QuantRisk</div>", unsafe_allow_html=True)
    st.caption("Live risk operating system")
    ok = r[r.status == "SUCCEEDED"]
    choice = st.selectbox("Run", ok.run_id if len(ok) else r.run_id,
                          format_func=lambda x: f"{r.set_index('run_id').loc[x, 'as_of_date']}  ({x})")
    meta = r.set_index("run_id").loc[choice]
    st.caption(f"Status: {meta.status}  \nGit: {(meta.git_sha or 'n/a')[:10]}  \nConfig: {meta.config_hash}")
    st.caption("Production mode with live market data and controls.")

s = summary(choice)
val = table("position_valuation", choice)
risk = table("risk_result", choice)
limits = table("limit_check", choice)
stress = table("stress_result", choice)
scen = table("stress_scenario", choice)

st.markdown(
    f"""
    <div class="hero">
      <div>
        <div class="hero-badge">Operational dashboard</div>
        <h1 style="margin: 0.6rem 0 0.2rem 0; font-size: 2.2rem;">Risk overview, {meta.as_of_date}</h1>
        <div class="muted">Live fixed-income, market, and credit risk across the production portfolio.</div>
      </div>
      <div class="glass" style="min-width: 240px; text-align: left;">
        <div class="muted" style="font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;">Run status</div>
        <div style="margin-top: 0.3rem; font-size: 1.3rem; font-weight: 800; color: {GOOD};">{meta.status}</div>
        <div class="muted" style="margin-top: 0.25rem;">{choice}</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tot1 = risk[(risk.scope == "TOTAL") & (risk.horizon_days == 1) & (risk.method == "historical")]
var99 = float(tot1[(tot1.metric == "VaR") & np.isclose(tot1.confidence, 0.99)].value.iloc[0])
es975 = float(tot1[(tot1.metric == "ES") & np.isclose(tot1.confidence, 0.975)].value.iloc[0])
by_scen = stress.groupby("scenario_id").total_pnl.sum()
el = table("credit_grade_summary", choice).groupby("scenario_id").expected_loss.sum()

st.markdown('<div class="glass">', unsafe_allow_html=True)
col = st.columns(6)
col[0].metric("Market value", usd_m(s["total_market_value"]))
col[1].metric("DV01 / bp", f"${s['total_dv01']:,.0f}")
col[2].metric("VaR 99% 1d", usd_m(var99))
col[3].metric("ES 97.5% 1d", usd_m(es975))
col[4].metric("Worst stress", usd_m(by_scen.min()))
col[5].metric("EL severe", usd_m(float(el.get("SEVERE", 0))))
st.markdown('</div>', unsafe_allow_html=True)

book_mv = val.groupby("book_id").market_value.sum().reset_index().sort_values("market_value", ascending=False)
book_fig = px.pie(book_mv, names="book_id", values="market_value", hole=0.45,
                  color_discrete_sequence=[ACCENT, CURVE, GOOD, SOFT, BAD])
book_fig.update_traces(textinfo="label+percent")
book_fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False, height=280)

stress_rank = stress.groupby("scenario_id").total_pnl.sum().sort_values().reset_index()
stress_fig = px.bar(stress_rank, x="scenario_id", y="total_pnl",
                   color="total_pnl", color_continuous_scale="RdBu_r")
stress_fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280, xaxis_title="Scenario", yaxis_title="P&L")

risk_by_metric = risk[(risk.scope == "TOTAL") & (risk.horizon_days == 1) & (risk.method == "historical")]
risk_metric_fig = px.bar(risk_by_metric[risk_by_metric.metric.isin(["VaR", "ES"])],
                        x="metric", y="value", color="confidence",
                        barmode="group", color_discrete_sequence=[ACCENT, CURVE])
risk_metric_fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280,
                              xaxis_title="Metric", yaxis_title="$ value")

st.markdown("<div class='glass' style='margin-top: 1rem; margin-bottom: 1rem;'>", unsafe_allow_html=True)
st.markdown("<div class='muted' style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.08em;'>Executive quick sense</div>", unsafe_allow_html=True)

dq = db.read_df(engine(), schema.dq_check_result, run_id=choice)
failed_dq = dq[dq.status == "FAIL"]
if not failed_dq.empty:
    st.warning(f"Data quality alerts: {', '.join(failed_dq.check_name.head(4).tolist())}")
else:
    st.success("Data quality is clean; no failing checks on the last production run.")

limit_alerts = limits[limits.status.isin(["AMBER", "RED"])].copy()
if not limit_alerts.empty:
    st.markdown(f"<div style='margin-top:0.75rem; padding:0.8rem 1rem; border-radius:14px; background:rgba(240,93,94,0.08); border:1px solid rgba(240,93,94,0.25); color:#ffd7d7;'>⚠️ {len(limit_alerts)} limit watch items: {', '.join(limit_alerts.limit_id.tolist())}</div>", unsafe_allow_html=True)

col_a, col_b, col_c = st.columns(3)
col_a.plotly_chart(book_fig, use_container_width=True)
col_b.plotly_chart(risk_metric_fig, use_container_width=True)
col_c.plotly_chart(stress_fig, use_container_width=True)

ref_instr = db.read_df(engine(), schema.ref_instrument)
sector_breakdown = None
if "sector" in ref_instr.columns:
    sector_breakdown = val.merge(ref_instr[["instrument_id", "sector"]], on="instrument_id", how="left").groupby("sector").market_value.sum().reset_index()
elif "asset_class" in ref_instr.columns:
    sector_breakdown = val.merge(ref_instr[["instrument_id", "asset_class"]], on="instrument_id", how="left").groupby("asset_class").market_value.sum().reset_index()
if sector_breakdown is not None and not sector_breakdown.empty:
    sector_fig = px.treemap(sector_breakdown, path=[sector_breakdown.columns[0]], values="market_value",
                           color="market_value", color_continuous_scale="Blues")
    sector_fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)
else:
    sector_fig = px.bar(book_mv, x="book_id", y="market_value", color="market_value",
                        color_continuous_scale="Tealgrn")
    sector_fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300)

par_map = {float(k): float(v) for k, v in json.loads(s["curve_par"]).items()} if s.get("curve_par") else {}
zero_map = {float(k): float(v) for k, v in json.loads(s["curve_zero"]).items()} if s.get("curve_zero") else {}
short_y = par_map.get(2.0, 0.0)
long_y = par_map.get(10.0, 0.0)
curve_slope_bp = (long_y - short_y) * 10_000 if short_y and long_y else 0.0
curve_slope_fig = go.Figure()
curve_slope_fig.add_bar(x=["2Y", "10Y"], y=[short_y * 100, long_y * 100], marker_color=[ACCENT, CURVE])
curve_slope_fig.update_layout(title="Curve level monitor", margin=dict(t=35, b=10, l=10, r=10), height=220,
                             xaxis_title="Point", yaxis_title="Yield %")

risk_method = risk[(risk.scope == "TOTAL") & (risk.horizon_days == 1) & (risk.metric.isin(["VaR", "ES"]))]
method_fig = px.bar(risk_method, x="method", y="value", color="metric", barmode="group",
                    color_discrete_map={"VaR": ACCENT, "ES": BAD})
method_fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=260,
                         xaxis_title="Method", yaxis_title="$ value")

col_d, col_e, col_f = st.columns(3)
col_d.plotly_chart(sector_fig, use_container_width=True)
col_e.plotly_chart(curve_slope_fig, use_container_width=True)
col_f.plotly_chart(method_fig, use_container_width=True)

worst_scenario = stress_rank.loc[stress_rank["total_pnl"].idxmin(), "scenario_id"]
worst_value = stress_rank["total_pnl"].min()
largest_book = book_mv.loc[book_mv["market_value"].idxmax(), "book_id"]
var_row = risk[(risk.scope == "TOTAL") & (risk.horizon_days == 1) & (risk.metric == "VaR") & np.isclose(risk.confidence, 0.99)]
var_value = float(var_row.value.iloc[0]) if not var_row.empty else 0.0

narrative = (
    f"The portfolio is currently led by {largest_book}. "
    f"The sharpest risk drag is {worst_scenario}, which is producing a {usd_m(worst_value)} loss under the current stress path. "
    f"One-day VaR at 99% is {usd_m(var_value)}, and the 2Y-10Y curve slope is {curve_slope_bp:.1f} bp, which indicates a {('steepening' if curve_slope_bp > 25 else 'stable or flattening')} rate structure. "
    f"The dominant exposures remain in the {largest_book} book, with the most acute sensitivity in the rate and spread buckets."
)
st.markdown(
    f"""
    <div class='glass' style='margin-bottom: 1rem; padding: 1rem 1.1rem;'>
      <div class='muted' style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.08em;'>Risk narrative</div>
      <p style='margin:0.7rem 0 0; line-height:1.5;'>{narrative}</p>
    </div>
    """,
    unsafe_allow_html=True,
)

status_strip = []
status_strip.append(("Data quality", "OK" if failed_dq.empty else "ALERT", GOOD if failed_dq.empty else BAD))
status_strip.append(("Limits", "GREEN" if limit_alerts.empty else ("AMBER" if limit_alerts.status.eq("AMBER").any() else "RED"), GOOD if limit_alerts.empty else (CURVE if limit_alerts.status.eq("AMBER").any() else BAD)))
status_strip.append(("Curve", "STABLE" if abs(curve_slope_bp) < 30 else ("STEEP" if curve_slope_bp > 0 else "FLAT"), GOOD if abs(curve_slope_bp) < 30 else CURVE))
status_strip.append(("VaR", "NORMAL" if var_value < s.get("total_market_value", 0) * 0.02 else "ELEVATED", GOOD if var_value < s.get("total_market_value", 0) * 0.02 else CURVE))

strip_cols = st.columns(len(status_strip))
for idx, (label, value, color) in enumerate(status_strip):
    with strip_cols[idx]:
        st.markdown(
            f"""
            <div style='background: rgba(8,16,26,0.9); border:1px solid {color}55; border-radius:12px; padding:0.65rem 0.8rem; margin-bottom:0.5rem; min-height:88px;'>
              <div style='font-size:0.7rem; text-transform:uppercase; letter-spacing:0.08em; color:{color};'>{label}</div>
              <div style='font-size:1.35rem; font-weight:800; margin-top:0.45rem; color:#edf4ff;'>{value}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

heat_df = val.merge(ref_instr[["instrument_id", "sector"]], on="instrument_id", how="left") if "sector" in ref_instr.columns else val.merge(ref_instr[["instrument_id", "asset_class"]], on="instrument_id", how="left")
heat_key = "sector" if "sector" in heat_df.columns else "asset_class"
heat_df["duration_bucket"] = pd.cut(
    heat_df["mod_duration"].fillna(0),
    bins=[-1, 2, 5, 10, 100],
    labels=["<2Y", "2-5Y", "5-10Y", ">10Y"],
    right=False,
)
heat = heat_df.groupby([heat_key, "duration_bucket"], as_index=False)["market_value"].sum().fillna(0)
heat_piv = heat.pivot(index=heat_key, columns="duration_bucket", values="market_value").fillna(0)
heat_fig = px.imshow(heat_piv, text_auto=True, color_continuous_scale="Blues", aspect="auto")
heat_fig.update_layout(title="Exposure heatmap by sector / duration", height=280, margin=dict(t=30, l=10, r=10, b=10))

st.plotly_chart(heat_fig, use_container_width=True)

st.markdown(
    f"""
    <div class='glass' style='margin-bottom: 1rem; padding: 0.8rem 1rem;'>
      <div style='display:flex; gap:1rem; flex-wrap:wrap;'>
        <div><strong>Quick read:</strong> largest exposure is <span style='color:{ACCENT};'>{largest_book}</span>.</div>
        <div><strong>Risk drag:</strong> worst stress is <span style='color:{BAD};'>{worst_scenario}</span> at {usd_m(worst_value)}.</div>
        <div><strong>Current VaR:</strong> {usd_m(var_value)} at 99% / 1D.</div>
        <div><strong>2Y-10Y slope:</strong> {curve_slope_bp:.1f} bp.</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('</div>', unsafe_allow_html=True)

tabs = st.tabs(["Limits", "Curve & positions", "VaR / ES", "Stress", "Credit", "Data quality", "Governance"])

with tabs[0]:
    lim = limits.copy()
    lim["utilization %"] = (lim.utilization * 100).round(0)
    lim["value"] = lim.value.round(3)
    fig = px.bar(lim.sort_values("utilization"), x="utilization %", y="limit_id", orientation="h", color="status",
                 color_discrete_map=STATUS_COLOR, range_x=[0, max(110, lim["utilization %"].max() + 10)])
    fig.add_vline(x=100, line_dash="dash", line_color=BAD)
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=None)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(lim[["limit_id", "description", "value", "amber", "red", "utilization %", "status"]],
                 hide_index=True, use_container_width=True)

with tabs[1]:
    par = json.loads(s["curve_par"])
    zero = json.loads(s["curve_zero"])
    fig = go.Figure()
    fig.add_scatter(x=list(par), y=[v * 100 for v in par.values()], name="Par", line=dict(color=CURVE, width=3))
    fig.add_scatter(x=list(zero), y=[v * 100 for v in zero.values()], name="Zero", line=dict(color=ACCENT))
    fig.update_layout(height=320, xaxis_title="tenor (years)", yaxis_title="%", margin=dict(t=10))
    left, right = st.columns([1, 1])
    left.plotly_chart(fig, use_container_width=True)
    krd = table("krd_result", choice)
    kf = krd.groupby(["tenor_years", "book_id"]).dv01.sum().reset_index()
    kf["tenor"] = kf.tenor_years.map(lambda t: f"{t:g}Y")
    right.plotly_chart(px.bar(kf, x="tenor", y="dv01", color="book_id", title="Key-rate DV01",
                              color_discrete_sequence=[ACCENT, CURVE]).update_layout(height=320), use_container_width=True)
    st.dataframe(val[["book_id", "instrument_id", "face_amount", "clean_price", "vendor_price", "market_value", "ytm",
                      "mod_duration", "eff_duration", "convexity", "spread_duration", "dv01"]].round(4),
                 hide_index=True, use_container_width=True)

with tabs[2]:
    h = st.radio("Horizon (days)", sorted(risk.horizon_days.unique()), horizontal=True)
    t = risk[(risk.scope == "TOTAL") & (risk.horizon_days == h)]
    piv = t.pivot_table(index=["confidence", "metric"], columns="method", values="value").round(0)
    st.dataframe(piv, use_container_width=True)
    pnl = table("pnl_vector", choice).sort_values("scenario_date")
    look = int(s.get("lookback_days", 500))
    fig = px.histogram(pnl.tail(look), x="pnl", nbins=50, color_discrete_sequence=[SOFT])
    fig.add_vline(x=-var99, line_color="black", annotation_text="VaR 99%")
    fig.update_layout(height=320, margin=dict(t=30))
    a, b = st.columns(2)
    a.plotly_chart(fig, use_container_width=True)
    btr = table("backtest_result", choice).sort_values("test_date")
    fig2 = go.Figure()
    fig2.add_bar(x=btr.test_date, y=btr.pnl, name="P&L", marker_color=SOFT)
    fig2.add_scatter(x=btr.test_date, y=-btr.var_99, name="-VaR 99%", line=dict(color="black"))
    ex = btr[btr.exception == 1]
    fig2.add_scatter(x=ex.test_date, y=ex.pnl, mode="markers", name="Exception", marker=dict(color=BAD, size=9))
    fig2.update_layout(height=320, margin=dict(t=30), title=f"Backtest: {int(s['bt_exceptions'])} exceptions, {s['bt_zone']}")
    b.plotly_chart(fig2, use_container_width=True)

with tabs[3]:
    agg = stress.groupby("scenario_id")[["rates_pnl", "spread_pnl", "vol_pnl", "total_pnl"]].sum()
    agg = agg.join(scen.set_index("scenario_id")[["name", "description"]]).sort_values("total_pnl")
    fig = go.Figure()
    for col, color, nm in (("rates_pnl", ACCENT, "Rates"), ("spread_pnl", CURVE, "Spread"), ("vol_pnl", SOFT, "Vol")):
        fig.add_bar(y=agg.name, x=agg[col], name=nm, orientation="h", marker_color=color)
    fig.update_layout(barmode="relative", height=440, margin=dict(t=10))
    st.plotly_chart(fig, use_container_width=True)
    pick = st.selectbox("Drill into scenario", agg.index, format_func=lambda x: agg.loc[x, "name"])
    st.caption(agg.loc[pick, "description"])
    st.dataframe(stress[stress.scenario_id == pick].drop(columns=["run_id", "scenario_hash"]).sort_values("total_pnl").round(0),
                 hide_index=True, use_container_width=True)

with tabs[4]:
    gs = table("credit_grade_summary", choice)
    order = list(load_settings().credit["master_scale"])
    gs["order"] = gs.grade.map({g: i for i, g in enumerate(order)})
    gs = gs.sort_values(["order", "scenario_id"])
    st.plotly_chart(px.bar(gs, x="grade", y="expected_loss", color="scenario_id", barmode="group",
                           color_discrete_map={"BASELINE": ACCENT, "ADVERSE": CURVE, "SEVERE": BAD}).update_layout(height=340),
                    use_container_width=True)
    m = st.columns(4)
    m[0].metric("OOT AUC", f"{s['pd_auc_oot']:.3f}")
    m[1].metric("OOT Gini", f"{s['pd_gini_oot']:.3f}")
    m[2].metric("PSI", f"{s['pd_psi']:.3f}")
    m[3].metric("Satellite R²", f"{s['sat_r2']:.2f}")
    st.dataframe(table("credit_calibration", choice).drop(columns="run_id"), hide_index=True, use_container_width=True)

with tabs[5]:
    dq = db.read_df(engine(), schema.dq_check_result, run_id=choice)
    recon = db.read_df(engine(), schema.recon_log, run_id=choice)
    a, b = st.columns(2)
    a.markdown("#### Data-quality checks")
    a.dataframe(dq.drop(columns="run_id").sort_values(["status", "severity"]), hide_index=True, use_container_width=True)
    b.markdown("#### Reconciliation log")
    b.dataframe(recon.drop(columns=["run_id", "recon_id"]).sort_values("status"), hide_index=True, use_container_width=True)

with tabs[6]:
    mv = table("model_validation", choice)
    st.markdown(" ".join(f"{pill(k)} {v}" for k, v in mv.status.value_counts().items()), unsafe_allow_html=True)
    st.dataframe(mv.drop(columns="run_id"), hide_index=True, use_container_width=True)
    secs = {k.removeprefix("seconds_"): v for k, v in s.items() if k.startswith("seconds_")}
    if secs:
        st.markdown("#### Batch step timings (seconds)")
        st.bar_chart(pd.Series(secs))
