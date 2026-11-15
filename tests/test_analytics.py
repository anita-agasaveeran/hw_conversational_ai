import json

import pandas as pd
import pytest

from fred_chat import analytics as A
from fred_chat.charts import CHART_TYPES, make_chart
from fred_chat.data import FredData, fill_gaps
from fred_chat.tools import TOOLS, ToolExecutor


@pytest.fixture(scope="module")
def data():
    return FredData.load()


def test_series_are_on_complete_grids(data):
    assert data.gdp.index.freqstr == "QS-JAN"
    assert data.cpi.index.freqstr == "MS"
    assert not data.gdp.isna().any() and not data.cpi.isna().any()


def test_october_2025_cpi_gap_is_filled_between_neighbours(data):
    assert pd.Timestamp("2025-10-01") in data.imputed["CPILFESL"]
    sep, octo, nov = data.cpi.loc["2025-09-01"], data.cpi.loc["2025-10-01"], data.cpi.loc["2025-11-01"]
    assert sep < octo < nov
    assert "shutdown" in data.imputed_note()


def test_fill_gaps_is_geometric_midpoint():
    s = pd.Series([100.0, float("nan"), 121.0], index=pd.date_range("2020-01-01", periods=3, freq="MS"))
    filled, missing = fill_gaps(s.dropna(), "MS")
    assert missing == [pd.Timestamp("2020-02-01")]
    assert filled.iloc[1] == pytest.approx(110.0, rel=1e-3)


def test_yoy_uses_same_month_a_year_earlier(data):
    latest = A.latest_observation(data, "CPILFESL")
    assert latest["latest_period"] == "Oct 2026"
    assert latest["year_ago_period"] == "Oct 2025"
    expected = (data.cpi.iloc[-1] / data.cpi.loc["2025-10-01"] - 1) * 100
    assert latest["core_inflation_yoy_pct"] == pytest.approx(expected, abs=0.01)


def test_latest_gdp(data):
    out = A.latest_observation(data, "GDP")
    assert out["latest_period"] == "2026 Q3"
    assert out["latest_value"] == pytest.approx(33010.036)
    q = (33010.036 / 32563.030) ** 4 * 100 - 100
    assert out["qoq_annualized_growth_pct"] == pytest.approx(q, abs=0.01)


def test_change_over_ten_years(data):
    out = A.change_over_period(data, "GDP", years_back=10)
    assert out["start"]["period"] == "2016 Q3"
    assert out["end"]["period"] == "2026 Q3"
    assert out["compound_annual_rate_pct"] == pytest.approx(((33010.036 / out["start"]["value"]) ** 0.1 - 1) * 100, abs=0.02)


def test_parse_date_formats():
    assert A.parse_date("2016") == pd.Timestamp("2016-01-01")
    assert A.parse_date("2016Q3") == pd.Timestamp("2016-07-01")
    assert A.parse_date("2016-Q4") == pd.Timestamp("2016-10-01")
    assert A.parse_date("2016-05") == pd.Timestamp("2016-05-01")
    assert A.parse_date(None) is None
    with pytest.raises(ValueError):
        A.parse_date("not a date")


def test_quarterly_inflation_drops_partial_quarter(data):
    q = A.quarterly_core_inflation_yoy(data.cpi).dropna()
    # data ends Oct 2026, so Q4 2026 is partial and must not appear
    assert q.index[-1] == pd.Timestamp("2026-07-01")


def test_growth_vs_inflation_shapes(data):
    out = A.growth_vs_inflation(data, years_back=10)
    assert -1 <= out["pearson_correlation"] <= 1
    assert "0" in out["lead_lag_correlations"]
    assert out["latest"]["period"] == "2026 Q3"


def test_every_indicator_runs(data):
    for name in A.INDICATORS:
        out = A.compute_indicator(data, name, years_back=5)
        assert out["count"] > 0, name


@pytest.mark.parametrize("chart_type", list(CHART_TYPES))
def test_every_chart_renders(data, chart_type):
    fig, desc = make_chart(data, chart_type, years_back=20)
    assert fig is not None and desc


def test_tool_executor_validates_and_runs(data):
    ex = ToolExecutor(data)
    text, err = ex.run("get_latest_observation", {"series": "GDP"})
    assert not err and json.loads(text)["latest_period"] == "2026 Q3"

    text, err = ex.run("get_latest_observation", {"series": "UNRATE"})
    assert err and "must be one of" in json.loads(text)["error"]

    text, err = ex.run("compute_indicator", {"indicator": "core_inflation_yoy", "years_back": "five"})
    assert err

    text, err = ex.run("create_chart", {"chart_type": "growth_vs_inflation", "start_date": "2000"})
    assert not err and len(ex.charts) == 1
    assert "data_note" in json.loads(text)

    text, err = ex.run("get_latest_observation", {"series": "GDP", "extra": 1})
    assert err


def test_tool_schemas_are_well_formed():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    for t in TOOLS:
        s = t["input_schema"]
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert set(s["required"]) <= set(s["properties"])
