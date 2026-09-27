"""Static charts for the risk report (matplotlib -> base64 PNG, fully self-contained HTML)."""

from __future__ import annotations

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

INK = "#122033"
MUTED = "#566476"
LINE = "#D6DDE6"
ACCENT = "#1D5BA8"
CURVE = "#B8700F"
BAD = "#B03434"
GOOD = "#2D7A4C"
SOFT = "#9DB6D9"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": LINE, "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": LINE, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 10,
    "axes.titlecolor": INK, "axes.titlelocation": "left", "legend.frameon": False,
})


def _png(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _usd_m(x: float, _pos: int | None = None) -> str:
    return f"${x / 1e6:,.1f}M"


def curve_chart(par: dict[str, float], zero: dict[str, float], prev_par: dict[str, float] | None) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    labels = list(par)
    x = np.arange(len(labels))
    if prev_par:
        ax.plot(x, [prev_par[k] * 100 for k in labels], color=SOFT, lw=1.5, ls="--", label="Par, 1 week ago")
    ax.plot(x, [par[k] * 100 for k in labels], color=CURVE, lw=2.4, marker="o", ms=4, label="Par yield (today)")
    ax.plot(x, [zero[k] * 100 for k in labels], color=ACCENT, lw=1.6, label="Zero rate (continuous)")
    ax.set_xticks(x, [f"{float(k):g}Y" if float(k) >= 1 else f"{int(float(k) * 12)}M" for k in labels])
    ax.yaxis.set_major_formatter(lambda v, _p: f"{v:.2f}%")
    ax.set_title("UST curve")
    ax.legend(loc="lower right", fontsize=8)
    return _png(fig)


def krd_chart(krd: pd.DataFrame) -> str:
    piv = krd.groupby(["tenor_years", "book_id"]).dv01.sum().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    x = np.arange(len(piv.index))
    bottom = np.zeros(len(x))
    for col, color in zip(piv.columns, [ACCENT, CURVE, GOOD, SOFT], strict=False):
        ax.bar(x, piv[col].values / 1e3, bottom=bottom, color=color, width=0.55, label=col)
        bottom += piv[col].values / 1e3
    for xi, v in zip(x, bottom, strict=True):
        ax.text(xi, v, f"${v:,.0f}k", ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xticks(x, [f"{t:g}Y" for t in piv.index])
    ax.set_ylabel("DV01, $k per bp")
    ax.set_title("Key-rate DV01 by tenor")
    ax.legend(fontsize=8)
    ax.set_ylim(0, bottom.max() * 1.18)
    return _png(fig)


def pnl_hist(pnl: np.ndarray, var: float, es: float) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    bins = np.linspace(pnl.min(), pnl.max(), 45)
    n, edges, patches = ax.hist(pnl, bins=bins, color=SOFT, edgecolor="white", linewidth=0.4)
    for p, left in zip(patches, edges[:-1], strict=True):
        if left < -var:
            p.set_facecolor(BAD)
    ax.axvline(-var, color=INK, lw=1.4)
    ax.axvline(-es, color=BAD, lw=1.4, ls="--")
    ymax = n.max()
    ax.text(-var, ymax * 0.95, f"  VaR 99% {_usd_m(var)}", color=INK, fontsize=8, va="top")
    ax.text(-es, ymax * 0.80, f"ES 99% {_usd_m(es)}  ", color=BAD, fontsize=8, va="top", ha="right")
    ax.xaxis.set_major_formatter(_usd_m)
    ax.set_ylabel("days")
    ax.set_title("Historical 1-day P&L distribution (500 days, full revaluation)")
    return _png(fig)


def backtest_chart(bt: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    x = pd.to_datetime(bt.test_date)
    ax.bar(x, bt.pnl, color=np.where(bt.pnl >= 0, SOFT, "#C9D3E0"), width=1.0)
    ax.plot(x, -bt.var_99, color=INK, lw=1.3, label="-VaR 99%")
    exc = bt[bt.exception == 1]
    ax.scatter(pd.to_datetime(exc.test_date), exc.pnl, color=BAD, zorder=5, s=22, label="Exception")
    ax.yaxis.set_major_formatter(_usd_m)
    ax.set_title("Backtest: hypothetical daily P&L vs VaR, last 250 days")
    ax.legend(fontsize=8, loc="upper left")
    fig.autofmt_xdate()
    return _png(fig)


def stress_chart(by_scen: pd.DataFrame) -> str:
    d = by_scen.sort_values("total")
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    y = np.arange(len(d))
    ax.barh(y, d.total / 1e6, color=[BAD if v < 0 else GOOD for v in d.total], height=0.6)
    ax.set_yticks(y, d.name)
    for yi, v in zip(y, d.total, strict=True):
        ax.text(v / 1e6, yi, f" {v / 1e6:,.1f}", va="center", ha="left" if v >= 0 else "right", fontsize=8, color=INK)
    ax.axvline(0, color=MUTED, lw=0.8)
    ax.set_xlabel("P&L, $M")
    lo, hi = min(d.total.min(), 0) / 1e6, max(d.total.max(), 0) / 1e6
    pad = (hi - lo) * 0.18
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_title("Stress P&L by scenario (full revaluation)")
    return _png(fig)


def credit_chart(gs: pd.DataFrame, grades: list[str]) -> str:
    piv = gs.pivot(index="grade", columns="scenario_id", values="expected_loss").reindex(grades).dropna(how="all").fillna(0)
    order = [c for c in ("BASELINE", "ADVERSE", "SEVERE") if c in piv.columns]
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    x = np.arange(len(piv.index))
    w = 0.26
    for i, (c, color) in enumerate(zip(order, [ACCENT, CURVE, BAD], strict=False)):
        ax.bar(x + (i - 1) * w, piv[c] / 1e6, width=w, color=color, label=c.title())
    ax.set_xticks(x, piv.index)
    ax.set_ylabel("Expected loss, $M")
    ax.set_title("Expected loss by grade and macro scenario")
    ax.legend(fontsize=8)
    return _png(fig)


def default_rate_chart(dr: dict[str, float], unemp: dict[int, float]) -> str:
    years = sorted(int(y) for y in dr)
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.bar(years, [dr[str(y)] * 100 for y in years], color=SOFT, label="Observed default rate")
    ax.set_ylabel("default rate, %")
    ax2 = ax.twinx()
    ax2.plot(years, [unemp.get(y, np.nan) for y in years], color=CURVE, lw=2, marker="o", ms=3, label="Unemployment")
    ax2.set_ylabel("unemployment, %", color=CURVE)
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    ax.set_xticks(years[::2], [str(y) for y in years[::2]])
    ax.set_title("Default rates track the macro cycle (basis for the satellite model)")
    return _png(fig)
