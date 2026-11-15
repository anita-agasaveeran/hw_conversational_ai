"""Matplotlib charts for GDP and Core CPI. Used by the Streamlit app and the figure export script."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless; Streamlit renders the Figure object directly

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import analytics as A
from .data import FredData

GDP_COLOR = "#1f5fa8"
CPI_COLOR = "#d4651b"
NEUTRAL = "#5f6670"

# NBER business-cycle peak and trough months (https://www.nber.org/research/business-cycle-dating)
NBER_RECESSIONS = [
    ("1948-11", "1949-10"), ("1953-07", "1954-05"), ("1957-08", "1958-04"),
    ("1960-04", "1961-02"), ("1969-12", "1970-11"), ("1973-11", "1975-03"),
    ("1980-01", "1980-07"), ("1981-07", "1982-11"), ("1990-07", "1991-03"),
    ("2001-03", "2001-11"), ("2007-12", "2009-06"), ("2020-02", "2020-04"),
]

CHART_TYPES = {
    "gdp_level": "Nominal GDP level (billions of dollars, SAAR)",
    "gdp_growth": "GDP growth: year-over-year % and quarter-over-quarter annualized %",
    "core_cpi_level": "Core CPI index level (1982-84=100)",
    "core_inflation": "Core CPI inflation: 12-month % and 3-month annualized %",
    "growth_vs_inflation": "GDP growth vs. core CPI inflation over time (both YoY %, quarterly)",
    "growth_vs_inflation_scatter": "Scatter of GDP growth vs. core CPI inflation by decade, with fitted line",
}


def _style(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color="#1b1f24")
    ax.set_ylabel(ylabel, color=NEUTRAL)
    ax.grid(True, axis="y", color="#e3e6ea", linewidth=0.8)
    ax.grid(False, axis="x")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c4c9cf")
    ax.tick_params(colors=NEUTRAL)


def _shade_recessions(ax, start: pd.Timestamp, end: pd.Timestamp) -> None:
    labeled = False
    for a, b in NBER_RECESSIONS:
        a_ts, b_ts = pd.Timestamp(a), pd.Timestamp(b) + pd.offsets.MonthEnd(0)
        if b_ts < start or a_ts > end:
            continue
        ax.axvspan(max(a_ts, start), min(b_ts, end), color="#d9dde2", alpha=0.6, lw=0,
                   label=None if labeled else "NBER recession")
        labeled = True


def _date_axis(ax, start: pd.Timestamp, end: pd.Timestamp) -> None:
    span_years = (end - start).days / 365.25
    if span_years <= 6:
        ax.xaxis.set_major_locator(mdates.YearLocator(1))
    elif span_years <= 15:
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
    elif span_years <= 35:
        ax.xaxis.set_major_locator(mdates.YearLocator(5))
    else:
        ax.xaxis.set_major_locator(mdates.YearLocator(10))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlim(start, end)


def _footer(fig, data: FredData, uses_cpi: bool = True) -> None:
    if uses_cpi:
        text = "Source: U.S. BEA (GDP) and U.S. BLS (CPILFESL) via FRED, Federal Reserve Bank of St. Louis."
    else:
        text = "Source: U.S. Bureau of Economic Analysis via FRED, Federal Reserve Bank of St. Louis."
    if uses_cpi and data.imputed.get("CPILFESL"):
        text += " Oct 2025 core CPI not published; interpolated."
    fig.text(0.01, 0.01, text, fontsize=7.5, color=NEUTRAL, ha="left", va="bottom")


def make_chart(
    data: FredData,
    chart_type: str,
    start: str | None = None,
    end: str | None = None,
    years_back: float | None = None,
    title: str | None = None,
):
    """Build one chart. Returns (matplotlib Figure, short text description of what it shows)."""
    if chart_type not in CHART_TYPES:
        raise ValueError(f"Unknown chart_type {chart_type!r}; choose from {list(CHART_TYPES)}")

    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=110)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.88, bottom=0.14)

    if chart_type == "gdp_level":
        a, b = A.resolve_window(data.gdp.index, start, end, years_back)
        s = A._clip(data.gdp, a, b) / 1000
        _shade_recessions(ax, a, b)
        ax.plot(s.index, s.values, color=GDP_COLOR, lw=2, label="GDP")
        _style(ax, title or "U.S. Gross Domestic Product", "Trillions of dollars (SAAR)")
        _date_axis(ax, a, b)
        desc = f"GDP level from {A.period_label(s.index[0], 'Q')} (${s.iloc[0]:.2f}T) to {A.period_label(s.index[-1], 'Q')} (${s.iloc[-1]:.2f}T)."

    elif chart_type == "gdp_growth":
        yoy = A.gdp_growth(data.gdp, "yoy").dropna()
        qoq = A.gdp_growth(data.gdp, "qoq_annualized").dropna()
        a, b = A.resolve_window(yoy.index, start, end, years_back)
        yoy, qoq = A._clip(yoy, a, b), A._clip(qoq, a, b)
        _shade_recessions(ax, a, b)
        ax.bar(qoq.index, qoq.values, width=60, color=GDP_COLOR, alpha=0.25, label="Quarter over quarter, annualized")
        ax.plot(yoy.index, yoy.values, color=GDP_COLOR, lw=2, label="Year over year")
        ax.axhline(0, color="#8a9099", lw=0.8)
        _style(ax, title or "U.S. GDP growth (nominal)", "Percent")
        _date_axis(ax, a - pd.DateOffset(months=2), b + pd.DateOffset(months=2))
        desc = (f"GDP growth {A.period_label(yoy.index[0], 'Q')}–{A.period_label(yoy.index[-1], 'Q')}; "
                f"latest YoY {yoy.iloc[-1]:.2f}%, latest QoQ annualized {qoq.iloc[-1]:.2f}%.")

    elif chart_type == "core_cpi_level":
        a, b = A.resolve_window(data.cpi.index, start, end, years_back)
        s = A._clip(data.cpi, a, b)
        _shade_recessions(ax, a, b)
        ax.plot(s.index, s.values, color=CPI_COLOR, lw=2, label="Core CPI")
        _style(ax, title or "Core CPI (all items less food and energy)", "Index, 1982–84 = 100")
        _date_axis(ax, a, b)
        desc = f"Core CPI index from {A.period_label(s.index[0], 'M')} ({s.iloc[0]:.1f}) to {A.period_label(s.index[-1], 'M')} ({s.iloc[-1]:.1f})."

    elif chart_type == "core_inflation":
        yoy = A.core_inflation(data.cpi, "yoy").dropna()
        m3 = A.core_inflation(data.cpi, "3m_annualized").dropna()
        a, b = A.resolve_window(yoy.index, start, end, years_back)
        yoy, m3 = A._clip(yoy, a, b), A._clip(m3, a, b)
        _shade_recessions(ax, a, b)
        ax.plot(m3.index, m3.values, color=CPI_COLOR, lw=1, alpha=0.45, label="3-month annualized")
        ax.plot(yoy.index, yoy.values, color=CPI_COLOR, lw=2.2, label="12-month")
        ax.axhline(2, color="#8a9099", lw=0.9, ls="--", label="2% reference")
        _style(ax, title or "U.S. core CPI inflation", "Percent")
        _date_axis(ax, a, b)
        desc = (f"Core CPI inflation {A.period_label(yoy.index[0], 'M')}–{A.period_label(yoy.index[-1], 'M')}; "
                f"latest 12-month {yoy.iloc[-1]:.2f}%, peak {yoy.max():.2f}% in {A.period_label(yoy.idxmax(), 'M')}.")

    elif chart_type == "growth_vs_inflation":
        df = A.aligned_growth_inflation(data)
        a, b = A.resolve_window(df.index, start, end, years_back)
        df = df.loc[(df.index >= a) & (df.index <= b)]
        _shade_recessions(ax, a, b)
        ax.plot(df.index, df["gdp_growth_yoy"], color=GDP_COLOR, lw=2, label="Nominal GDP growth (YoY)")
        ax.plot(df.index, df["core_inflation_yoy"], color=CPI_COLOR, lw=2, label="Core CPI inflation (YoY)")
        ax.fill_between(df.index, df["gdp_growth_yoy"], df["core_inflation_yoy"],
                        where=df["gdp_growth_yoy"] >= df["core_inflation_yoy"],
                        color=GDP_COLOR, alpha=0.08, interpolate=True, label="Growth above inflation")
        ax.fill_between(df.index, df["gdp_growth_yoy"], df["core_inflation_yoy"],
                        where=df["gdp_growth_yoy"] < df["core_inflation_yoy"],
                        color=CPI_COLOR, alpha=0.12, interpolate=True, label="Inflation above growth")
        ax.axhline(0, color="#8a9099", lw=0.8)
        r = df["gdp_growth_yoy"].corr(df["core_inflation_yoy"])
        _style(ax, title or "GDP growth vs. core CPI inflation", "Percent, year over year")
        _date_axis(ax, a, b)
        ax.text(0.99, 0.97, f"Correlation r = {r:.2f}", transform=ax.transAxes, ha="right", va="top",
                fontsize=9, color=NEUTRAL)
        desc = (f"Quarterly GDP growth and core inflation {A.period_label(df.index[0], 'Q')}–"
                f"{A.period_label(df.index[-1], 'Q')}; correlation {r:.2f}.")

    else:  # growth_vs_inflation_scatter
        df = A.aligned_growth_inflation(data)
        a, b = A.resolve_window(df.index, start, end, years_back)
        df = df.loc[(df.index >= a) & (df.index <= b)]
        decades = (df.index.year // 10) * 10
        cmap = plt.get_cmap("tab10")
        for i, dec in enumerate(sorted(set(decades))):
            sub = df[decades == dec]
            ax.scatter(sub["gdp_growth_yoy"], sub["core_inflation_yoy"], s=18, alpha=0.8,
                       color=cmap(i), label=f"{dec}s")
        slope, intercept = np.polyfit(df["gdp_growth_yoy"], df["core_inflation_yoy"], 1)
        xs = np.linspace(df["gdp_growth_yoy"].min(), df["gdp_growth_yoy"].max(), 50)
        r = df["gdp_growth_yoy"].corr(df["core_inflation_yoy"])
        ax.plot(xs, slope * xs + intercept, color="#1b1f24", lw=1.2, ls="--",
                label=f"Fit: y = {slope:.2f}x + {intercept:.2f} (r = {r:.2f})")
        _style(ax, title or "GDP growth vs. core CPI inflation, each dot one quarter", "Core CPI inflation, YoY %")
        ax.set_xlabel("Nominal GDP growth, YoY %", color=NEUTRAL)
        ax.grid(True, axis="x", color="#e3e6ea", linewidth=0.8)
        desc = (f"Scatter of {len(df)} quarters ({A.period_label(df.index[0], 'Q')}–"
                f"{A.period_label(df.index[-1], 'Q')}); slope {slope:.2f}, r = {r:.2f}.")

    ax.legend(frameon=False, fontsize=8.5, loc="upper left", ncol=2 if chart_type == "growth_vs_inflation_scatter" else 1)
    _footer(fig, data, uses_cpi=not chart_type.startswith("gdp"))
    return fig, desc
