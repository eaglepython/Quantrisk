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

st.set_page_config(page_title="QuantRisk", layout="wide")

ACCENT, CURVE, BAD, GOOD, SOFT = "#1D5BA8", "#B8700F", "#B03434", "#2D7A4C", "#9DB6D9"
STATUS_COLOR = {"GREEN": GOOD, "AMBER": CURVE, "RED": BAD, "PASS": GOOD, "WATCH": CURVE, "FAIL": BAD,
                "MATCH": GOOD, "BREAK": BAD}


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
    st.markdown("### QuantRisk")
    ok = r[r.status == "SUCCEEDED"]
    choice = st.selectbox("Run", ok.run_id if len(ok) else r.run_id,
                          format_func=lambda x: f"{r.set_index('run_id').loc[x, 'as_of_date']}  ({x})")
    meta = r.set_index("run_id").loc[choice]
    st.caption(f"Status: {meta.status}  \nGit: {(meta.git_sha or 'n/a')[:10]}  \nConfig: {meta.config_hash}")
    st.caption("Sample data for demonstration.")

s = summary(choice)
val = table("position_valuation", choice)
risk = table("risk_result", choice)
limits = table("limit_check", choice)
stress = table("stress_result", choice)
scen = table("stress_scenario", choice)

st.title(f"Risk dashboard, {meta.as_of_date}")

tot1 = risk[(risk.scope == "TOTAL") & (risk.horizon_days == 1) & (risk.method == "historical")]
var99 = float(tot1[(tot1.metric == "VaR") & np.isclose(tot1.confidence, 0.99)].value.iloc[0])
es975 = float(tot1[(tot1.metric == "ES") & np.isclose(tot1.confidence, 0.975)].value.iloc[0])
by_scen = stress.groupby("scenario_id").total_pnl.sum()
el = table("credit_grade_summary", choice).groupby("scenario_id").expected_loss.sum()
c = st.columns(6)
c[0].metric("Market value", usd_m(s["total_market_value"]))
c[1].metric("DV01 / bp", f"${s['total_dv01']:,.0f}")
c[2].metric("VaR 99% 1d", usd_m(var99))
c[3].metric("ES 97.5% 1d", usd_m(es975))
c[4].metric("Worst stress", usd_m(by_scen.min()))
c[5].metric("EL severe", usd_m(float(el.get("SEVERE", 0))))

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
