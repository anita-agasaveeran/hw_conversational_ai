"""Economic calculations on GDP and Core CPI.

Every function returns plain JSON-friendly dicts so the results can be handed to
Claude as tool output. All numbers come from here; the model only explains them.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .data import SERIES_INFO, FredData

# --------------------------------------------------------------------------- helpers


def _r(x: Any, nd: int = 2) -> float | None:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, nd)


def period_label(ts: pd.Timestamp, freq: str) -> str:
    if freq == "Q":
        return f"{ts.year} Q{(ts.month - 1) // 3 + 1}"
    if freq == "A":
        return str(ts.year)
    return ts.strftime("%b %Y")


def _freq_of(series_id: str) -> str:
    return "Q" if series_id == "GDP" else "M"


_Q_RE = re.compile(r"^(\d{4})\s*-?\s*Q([1-4])$", re.I)


def parse_date(value: str | None) -> pd.Timestamp | None:
    """Accept '2016', '2016-04', '2016-04-01', '2016Q2' / '2016-Q2'."""
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip()
    m = _Q_RE.match(v)
    if m:
        return pd.Timestamp(year=int(m.group(1)), month=3 * (int(m.group(2)) - 1) + 1, day=1)
    if re.fullmatch(r"\d{4}", v):
        return pd.Timestamp(year=int(v), month=1, day=1)
    try:
        return pd.Timestamp(v)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Could not parse date {value!r}; use YYYY, YYYY-MM, YYYY-MM-DD or YYYYQn") from exc


def resolve_window(
    index: pd.DatetimeIndex,
    start: str | None = None,
    end: str | None = None,
    years_back: float | None = None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Turn user-facing window arguments into concrete timestamps within the data."""
    end_ts = parse_date(end) or index.max()
    if end_ts > index.max():
        end_ts = index.max()
    if years_back is not None:
        if years_back <= 0:
            raise ValueError("years_back must be positive")
        months = int(round(years_back * 12))
        start_ts = end_ts - pd.DateOffset(months=months)
    else:
        start_ts = parse_date(start) or index.min()
    if start_ts < index.min():
        start_ts = index.min()
    if start_ts > end_ts:
        raise ValueError(f"Start {start_ts.date()} is after end {end_ts.date()}")
    return start_ts, end_ts


def _clip(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    return s.loc[(s.index >= start) & (s.index <= end)].dropna()


def _points(s: pd.Series, freq: str, nd: int = 2) -> list[list]:
    return [[period_label(ts, freq), _r(v, nd)] for ts, v in s.items()]


def _downsample(s: pd.Series, freq: str, max_points: int) -> tuple[pd.Series, str, str | None]:
    """Reduce to annual averages when there are too many points to hand to the model."""
    if len(s) <= max_points:
        return s, freq, None
    annual = s.groupby(s.index.year).mean()
    annual.index = pd.to_datetime(annual.index.astype(str) + "-01-01")
    note = f"{len(s)} observations were averaged to {len(annual)} annual values to keep the output compact."
    return annual, "A", note


# ---------------------------------------------------------------------- core series


def gdp_growth(gdp: pd.Series, kind: str = "yoy") -> pd.Series:
    """GDP growth in percent.

    yoy: % change from the same quarter a year earlier.
    qoq_annualized: quarter-over-quarter change compounded to an annual rate (BEA headline style).
    """
    if kind == "yoy":
        return gdp.pct_change(4) * 100
    if kind == "qoq_annualized":
        return ((gdp / gdp.shift(1)) ** 4 - 1) * 100
    if kind == "qoq":
        return gdp.pct_change(1) * 100
    raise ValueError(f"Unknown GDP growth kind {kind!r}")


def core_inflation(cpi: pd.Series, kind: str = "yoy") -> pd.Series:
    """Core CPI inflation in percent.

    yoy: 12-month % change (the standard headline inflation rate).
    mom_annualized: 1-month change compounded to an annual rate.
    3m_annualized: 3-month change compounded to an annual rate.
    """
    if kind == "yoy":
        return cpi.pct_change(12) * 100
    if kind == "mom_annualized":
        return ((cpi / cpi.shift(1)) ** 12 - 1) * 100
    if kind == "3m_annualized":
        return ((cpi / cpi.shift(3)) ** 4 - 1) * 100
    if kind == "mom":
        return cpi.pct_change(1) * 100
    raise ValueError(f"Unknown inflation kind {kind!r}")


def quarterly_core_inflation_yoy(cpi: pd.Series) -> pd.Series:
    """Core CPI averaged to quarters (start-of-quarter dates, matching GDP), then YoY %."""
    q = cpi.resample("QS").mean()
    # Drop a trailing partial quarter so it doesn't distort the last value.
    last_month = cpi.index.max()
    if (last_month.month - 1) % 3 != 2:
        q = q.iloc[:-1]
    return q.pct_change(4) * 100


def aligned_growth_inflation(data: FredData) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "gdp_growth_yoy": gdp_growth(data.gdp, "yoy"),
            "core_inflation_yoy": quarterly_core_inflation_yoy(data.cpi),
        }
    ).dropna()
    df["real_growth_proxy"] = df["gdp_growth_yoy"] - df["core_inflation_yoy"]
    return df


INDICATORS: dict[str, dict[str, Any]] = {
    "gdp_level": {
        "series": "GDP", "freq": "Q", "units": "Billions of dollars (SAAR)",
        "description": "Nominal GDP level",
        "fn": lambda d: d.gdp,
    },
    "gdp_growth_yoy": {
        "series": "GDP", "freq": "Q", "units": "%",
        "description": "Nominal GDP growth, % change from a year earlier",
        "fn": lambda d: gdp_growth(d.gdp, "yoy"),
    },
    "gdp_growth_qoq_annualized": {
        "series": "GDP", "freq": "Q", "units": "% (annualized)",
        "description": "Nominal GDP growth, quarter over quarter at an annualized rate",
        "fn": lambda d: gdp_growth(d.gdp, "qoq_annualized"),
    },
    "core_cpi_level": {
        "series": "CPILFESL", "freq": "M", "units": "Index 1982-84=100",
        "description": "Core CPI index level",
        "fn": lambda d: d.cpi,
    },
    "core_inflation_yoy": {
        "series": "CPILFESL", "freq": "M", "units": "%",
        "description": "Core CPI inflation, 12-month % change",
        "fn": lambda d: core_inflation(d.cpi, "yoy"),
    },
    "core_inflation_3m_annualized": {
        "series": "CPILFESL", "freq": "M", "units": "% (annualized)",
        "description": "Core CPI inflation, 3-month % change at an annualized rate",
        "fn": lambda d: core_inflation(d.cpi, "3m_annualized"),
    },
    "core_inflation_mom_annualized": {
        "series": "CPILFESL", "freq": "M", "units": "% (annualized)",
        "description": "Core CPI inflation, 1-month % change at an annualized rate",
        "fn": lambda d: core_inflation(d.cpi, "mom_annualized"),
    },
    "real_growth_proxy_yoy": {
        "series": "both", "freq": "Q", "units": "percentage points",
        "description": (
            "Nominal GDP growth minus core CPI inflation (both YoY, quarterly). A rough proxy for "
            "real growth; official real GDP uses the GDP price deflator, not core CPI."
        ),
        "fn": lambda d: aligned_growth_inflation(d)["real_growth_proxy"],
    },
}


# ------------------------------------------------------------------- tool functions


def latest_observation(data: FredData, series_id: str) -> dict:
    s = data.get(series_id)
    freq = _freq_of(series_id)
    lag = 4 if freq == "Q" else 12
    out: dict[str, Any] = {
        "series": series_id,
        "title": SERIES_INFO[series_id]["title"],
        "units": SERIES_INFO[series_id]["units"],
        "adjustment": SERIES_INFO[series_id]["adjustment"],
        "latest_period": period_label(s.index[-1], freq),
        "latest_date": s.index[-1].date().isoformat(),
        "latest_value": _r(s.iloc[-1], 3),
        "previous_period": period_label(s.index[-2], freq),
        "previous_value": _r(s.iloc[-2], 3),
        "pct_change_from_previous": _r((s.iloc[-1] / s.iloc[-2] - 1) * 100, 3),
        "year_ago_period": period_label(s.index[-1 - lag], freq),
        "year_ago_value": _r(s.iloc[-1 - lag], 3),
        "pct_change_from_year_ago": _r((s.iloc[-1] / s.iloc[-1 - lag] - 1) * 100, 2),
        "first_period_in_data": period_label(s.index[0], freq),
        "note": SERIES_INFO[series_id]["note"],
    }
    if series_id == "GDP":
        out["latest_value_trillions"] = _r(s.iloc[-1] / 1000, 3)
        out["qoq_annualized_growth_pct"] = _r(gdp_growth(s, "qoq_annualized").iloc[-1], 2)
    else:
        out["core_inflation_yoy_pct"] = out["pct_change_from_year_ago"]
        out["core_inflation_3m_annualized_pct"] = _r(core_inflation(s, "3m_annualized").iloc[-1], 2)
        out["core_inflation_mom_annualized_pct"] = _r(core_inflation(s, "mom_annualized").iloc[-1], 2)
    return out


def get_observations(
    data: FredData,
    series_id: str,
    start: str | None = None,
    end: str | None = None,
    years_back: float | None = None,
    frequency: str = "native",
    max_points: int = 160,
) -> dict:
    s = data.get(series_id)
    freq = _freq_of(series_id)
    a, b = resolve_window(s.index, start, end, years_back)
    w = _clip(s, a, b)
    if frequency == "annual":
        w = w.groupby(w.index.year).mean()
        w.index = pd.to_datetime(w.index.astype(str) + "-01-01")
        freq = "A"
    elif frequency == "quarterly" and series_id == "CPILFESL":
        w = w.resample("QS").mean()
        freq = "Q"
    w, freq, note = _downsample(w, freq, max_points)
    return {
        "series": series_id,
        "units": SERIES_INFO[series_id]["units"],
        "window": [a.date().isoformat(), b.date().isoformat()],
        "frequency": {"Q": "quarterly", "M": "monthly", "A": "annual average"}[freq],
        "count": len(w),
        "observations": _points(w, freq, 3),
        "note": note,
    }


def _stats(s: pd.Series, freq: str) -> dict:
    return {
        "first": {"period": period_label(s.index[0], freq), "value": _r(s.iloc[0])},
        "last": {"period": period_label(s.index[-1], freq), "value": _r(s.iloc[-1])},
        "mean": _r(s.mean()),
        "median": _r(s.median()),
        "std": _r(s.std()),
        "min": {"period": period_label(s.idxmin(), freq), "value": _r(s.min())},
        "max": {"period": period_label(s.idxmax(), freq), "value": _r(s.max())},
    }


def compute_indicator(
    data: FredData,
    indicator: str,
    start: str | None = None,
    end: str | None = None,
    years_back: float | None = None,
    include_values: bool = True,
    max_points: int = 80,
) -> dict:
    if indicator not in INDICATORS:
        raise ValueError(f"Unknown indicator {indicator!r}; choose from {list(INDICATORS)}")
    spec = INDICATORS[indicator]
    full = spec["fn"](data).dropna()
    a, b = resolve_window(full.index, start, end, years_back)
    s = _clip(full, a, b)
    if s.empty:
        raise ValueError("No data in that window")
    freq = spec["freq"]
    out = {
        "indicator": indicator,
        "description": spec["description"],
        "units": spec["units"],
        "window": [period_label(s.index[0], freq), period_label(s.index[-1], freq)],
        "count": len(s),
        "stats": _stats(s, freq),
        "change_first_to_last": _r(s.iloc[-1] - s.iloc[0]),
    }
    if include_values:
        vals, vfreq, note = _downsample(s, freq, max_points)
        out["values"] = _points(vals, vfreq)
        out["values_note"] = note
    return out


def change_over_period(
    data: FredData,
    series_id: str,
    start: str | None = None,
    end: str | None = None,
    years_back: float | None = None,
) -> dict:
    """Level change, total % change and compound annual growth rate over a window."""
    s = data.get(series_id)
    freq = _freq_of(series_id)
    a, b = resolve_window(s.index, start, end, years_back)
    w = _clip(s, a, b)
    first, last = w.iloc[0], w.iloc[-1]
    years = (w.index[-1] - w.index[0]).days / 365.25
    out: dict[str, Any] = {
        "series": series_id,
        "units": SERIES_INFO[series_id]["units"],
        "start": {"period": period_label(w.index[0], freq), "value": _r(first, 3)},
        "end": {"period": period_label(w.index[-1], freq), "value": _r(last, 3)},
        "years": _r(years, 2),
        "absolute_change": _r(last - first, 3),
        "total_pct_change": _r((last / first - 1) * 100, 2),
        "compound_annual_rate_pct": _r(((last / first) ** (1 / years) - 1) * 100, 2) if years > 0 else None,
    }
    if series_id == "GDP":
        g = _clip(gdp_growth(s, "yoy"), a, b)
        q = _clip(gdp_growth(s, "qoq_annualized"), a, b)
        out["yoy_growth_stats"] = _stats(g, "Q")
        out["qoq_annualized_growth_stats"] = _stats(q, "Q")
        out["negative_qoq_quarters"] = [period_label(t, "Q") for t, v in q.items() if v < 0]
        annual_growth = g.groupby(g.index.year).mean()
        out["avg_yoy_growth_by_year"] = {str(k): _r(v) for k, v in annual_growth.items()}
    else:
        inf = _clip(core_inflation(s, "yoy"), a, b)
        out["core_inflation_yoy_stats"] = _stats(inf, "M")
        dec = inf[inf.index.month == 12]
        out["december_yoy_inflation_by_year"] = {str(t.year): _r(v) for t, v in dec.items()}
        if inf.index[-1].month != 12:
            out["december_yoy_inflation_by_year"][f"{inf.index[-1].year} (latest, {period_label(inf.index[-1], 'M')})"] = _r(inf.iloc[-1])
    return out


def growth_vs_inflation(
    data: FredData,
    start: str | None = None,
    end: str | None = None,
    years_back: float | None = None,
) -> dict:
    """Relationship between nominal GDP growth and core inflation (quarterly, YoY)."""
    df_all = aligned_growth_inflation(data)
    a, b = resolve_window(df_all.index, start, end, years_back)
    df = df_all.loc[(df_all.index >= a) & (df_all.index <= b)]
    if len(df) < 8:
        raise ValueError("Need at least 8 overlapping quarters to compare")
    g, p = df["gdp_growth_yoy"], df["core_inflation_yoy"]

    covid = (df.index >= "2020-01-01") & (df.index <= "2021-12-31")
    slope, intercept = np.polyfit(g, p, 1)

    # Lead/lag: corr(GDP growth at t, core inflation at t+k). k>0 => inflation lags growth.
    lags = {}
    for k in (-8, -4, -2, 0, 2, 4, 6, 8):
        shifted = df_all["core_inflation_yoy"].shift(-k)
        pair = pd.concat([df_all["gdp_growth_yoy"], shifted], axis=1).dropna()
        pair = pair.loc[(pair.index >= a) & (pair.index <= b)]
        if len(pair) >= 8:
            lags[str(k)] = _r(pair.iloc[:, 0].corr(pair.iloc[:, 1]), 3)
    best_lag = max(lags, key=lambda k: abs(lags[k] or 0)) if lags else None

    decades = {}
    for dec, grp in df.groupby((df.index.year // 10) * 10):
        if len(grp) >= 8:
            decades[f"{dec}s"] = {
                "avg_gdp_growth_yoy": _r(grp["gdp_growth_yoy"].mean()),
                "avg_core_inflation_yoy": _r(grp["core_inflation_yoy"].mean()),
                "correlation": _r(grp["gdp_growth_yoy"].corr(grp["core_inflation_yoy"]), 3),
                "quarters": len(grp),
            }

    return {
        "window": [period_label(df.index[0], "Q"), period_label(df.index[-1], "Q")],
        "quarters": len(df),
        "method": (
            "Quarterly data. GDP growth = nominal GDP % change vs. same quarter a year earlier. "
            "Core inflation = core CPI averaged over each quarter, % change vs. a year earlier."
        ),
        "avg_gdp_growth_yoy": _r(g.mean()),
        "avg_core_inflation_yoy": _r(p.mean()),
        "avg_real_growth_proxy": _r((g - p).mean()),
        "pearson_correlation": _r(g.corr(p), 3),
        "spearman_correlation": _r(g.rank().corr(p.rank()), 3),
        "pearson_correlation_excluding_2020_2021": _r(g[~covid].corr(p[~covid]), 3) if covid.any() else None,
        "regression_core_inflation_on_gdp_growth": {"slope": _r(slope, 3), "intercept": _r(intercept, 3)},
        "lead_lag_correlations": lags,
        "lead_lag_note": "Key k = quarters that core inflation is shifted after GDP growth; positive k means inflation lags growth.",
        "strongest_lag_quarters": best_lag,
        "by_decade": decades,
        "latest": {
            "period": period_label(df.index[-1], "Q"),
            "gdp_growth_yoy": _r(g.iloc[-1]),
            "core_inflation_yoy": _r(p.iloc[-1]),
            "real_growth_proxy": _r(g.iloc[-1] - p.iloc[-1]),
        },
        "caveat": (
            "GDP here is nominal, so its growth already contains inflation; part of the positive "
            "correlation is mechanical. Correlation does not imply causation."
        ),
    }


def key_trends(data: FredData, start: str | None = None, end: str | None = None, years_back: float | None = None) -> dict:
    """A compact digest of long-run and recent trends in both series."""
    gdp, cpi = data.gdp, data.cpi
    g_yoy = gdp_growth(gdp, "yoy").dropna()
    g_qoq = gdp_growth(gdp, "qoq_annualized").dropna()
    inf = core_inflation(cpi, "yoy").dropna()

    ga, gb = resolve_window(gdp.index, start, end, years_back)
    ca, cb = resolve_window(cpi.index, start, end, years_back)
    g_yoy_w, g_qoq_w, inf_w = _clip(g_yoy, ga, gb), _clip(g_qoq, ga, gb), _clip(inf, ca, cb)

    def by_decade(s: pd.Series) -> dict:
        return {f"{d}s": _r(v) for d, v in s.groupby((s.index.year // 10) * 10).mean().items()}

    worst = g_qoq_w.nsmallest(3)
    best = g_qoq_w.nlargest(3)

    # Local peaks in core inflation above 4% (separate episodes at least 3 years apart)
    peaks = []
    for t, v in inf_w.sort_values(ascending=False).items():
        if v < 4:
            break
        if all(abs((t - pt).days) > 3 * 365 for pt, _ in peaks):
            peaks.append((t, v))
        if len(peaks) == 4:
            break

    gw = _clip(gdp, ga, gb)
    years = (gw.index[-1] - gw.index[0]).days / 365.25
    cw = _clip(cpi, ca, cb)
    cyears = (cw.index[-1] - cw.index[0]).days / 365.25

    return {
        "gdp": {
            "window": [period_label(gw.index[0], "Q"), period_label(gw.index[-1], "Q")],
            "level_start_billions": _r(gw.iloc[0], 1),
            "level_end_billions": _r(gw.iloc[-1], 1),
            "compound_annual_growth_pct": _r(((gw.iloc[-1] / gw.iloc[0]) ** (1 / years) - 1) * 100) if years > 0 else None,
            "avg_yoy_growth_by_decade": by_decade(g_yoy_w),
            "worst_quarters_qoq_annualized": [[period_label(t, "Q"), _r(v)] for t, v in worst.items()],
            "best_quarters_qoq_annualized": [[period_label(t, "Q"), _r(v)] for t, v in best.items()],
            "last_4_quarters_qoq_annualized": _points(g_qoq.iloc[-4:], "Q"),
            "latest_yoy_growth": [period_label(g_yoy.index[-1], "Q"), _r(g_yoy.iloc[-1])],
            "avg_yoy_growth_last_5y": _r(g_yoy.loc[g_yoy.index > g_yoy.index[-1] - pd.DateOffset(years=5)].mean()),
        },
        "core_cpi": {
            "window": [period_label(cw.index[0], "M"), period_label(cw.index[-1], "M")],
            "index_start": _r(cw.iloc[0], 2),
            "index_end": _r(cw.iloc[-1], 2),
            "compound_annual_inflation_pct": _r(((cw.iloc[-1] / cw.iloc[0]) ** (1 / cyears) - 1) * 100) if cyears > 0 else None,
            "avg_yoy_inflation_by_decade": by_decade(inf_w),
            "inflation_peaks_above_4pct": [[period_label(t, "M"), _r(v)] for t, v in peaks],
            "lowest_yoy_inflation": [period_label(inf_w.idxmin(), "M"), _r(inf_w.min())],
            "latest_yoy_inflation": [period_label(inf.index[-1], "M"), _r(inf.iloc[-1])],
            "latest_3m_annualized": _r(core_inflation(cpi, "3m_annualized").iloc[-1]),
            "yoy_inflation_12_months_ago": _r(inf.iloc[-13]),
            "pct_of_months_since_2010_below_2pct": _r((inf.loc["2010":] < 2).mean() * 100, 1),
        },
        "combined": {
            k: v for k, v in growth_vs_inflation(data, start, end, years_back).items()
            if k in ("pearson_correlation", "pearson_correlation_excluding_2020_2021", "avg_real_growth_proxy", "latest")
        },
    }
