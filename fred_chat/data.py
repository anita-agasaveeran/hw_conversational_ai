"""Loading the two FRED series from local CSVs, with an optional refresh from FRED."""

from __future__ import annotations

import io
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Public CSV download endpoint used by FRED's own "Download" button; no API key needed.
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

SERIES_INFO: dict[str, dict[str, str]] = {
    "GDP": {
        "title": "Gross Domestic Product",
        "units": "Billions of Dollars",
        "adjustment": "Seasonally Adjusted Annual Rate",
        "frequency": "Quarterly",
        "source": "U.S. Bureau of Economic Analysis via FRED",
        "url": "https://fred.stlouisfed.org/series/GDP",
        "note": "Nominal GDP (current dollars), so its growth rate includes price changes.",
    },
    "CPILFESL": {
        "title": "Consumer Price Index for All Urban Consumers: All Items Less Food and Energy",
        "units": "Index 1982-1984=100",
        "adjustment": "Seasonally Adjusted",
        "frequency": "Monthly",
        "source": "U.S. Bureau of Labor Statistics via FRED",
        "url": "https://fred.stlouisfed.org/series/CPILFESL",
        "note": "Core CPI index level. Core inflation is the % change in this index, usually year over year.",
    },
}


def _parse_csv(text_or_path, series_id: str) -> pd.Series:
    df = pd.read_csv(text_or_path)
    # FRED has used both "observation_date" and "DATE" as the date column name.
    date_col = next(c for c in df.columns if c.lower() in ("observation_date", "date"))
    if series_id not in df.columns:
        raise ValueError(f"CSV has no {series_id} column (columns: {list(df.columns)})")
    values = pd.to_numeric(df[series_id], errors="coerce")  # FRED marks missing values as "."
    s = pd.Series(values.values, index=pd.to_datetime(df[date_col]), name=series_id)
    return s.dropna().sort_index()


def load_series(series_id: str, data_dir: Path = DATA_DIR) -> pd.Series:
    if series_id not in SERIES_INFO:
        raise KeyError(f"Unknown series {series_id!r}; expected one of {list(SERIES_INFO)}")
    return _parse_csv(Path(data_dir) / f"{series_id}.csv", series_id)


def refresh_from_fred(series_id: str, data_dir: Path = DATA_DIR, timeout: float = 20.0) -> int:
    """Download the latest CSV for a series from FRED and overwrite the local copy.

    Returns the number of observations written. Raises on network or format errors,
    leaving the existing file untouched.
    """
    url = FRED_CSV_URL.format(series_id=series_id)
    req = urllib.request.Request(url, headers={"User-Agent": "fred-chat/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
    series = _parse_csv(io.StringIO(text), series_id)  # validate before overwriting
    out = series.rename_axis("observation_date").reset_index()
    out.to_csv(Path(data_dir) / f"{series_id}.csv", index=False)
    return len(series)


def fill_gaps(series: pd.Series, freq: str) -> tuple[pd.Series, list[pd.Timestamp]]:
    """Put the series on a complete date grid and fill interior gaps by log-linear interpolation.

    FRED leaves a blank when an observation was never published (e.g. BLS did not
    publish October 2025 CPI because of the federal government shutdown). A blank
    would otherwise shift every positional lag, so we fill it and report which
    dates were filled.
    """
    full = series.asfreq(freq)
    missing = list(full.index[full.isna()])
    if missing:
        full = np.exp(np.log(full).interpolate(method="linear"))  # equal steps per period
        full.name = series.name
    return full, missing


@dataclass
class FredData:
    gdp: pd.Series
    cpi: pd.Series
    imputed: dict[str, list[pd.Timestamp]] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path = DATA_DIR) -> "FredData":
        gdp, gdp_missing = fill_gaps(load_series("GDP", data_dir), "QS")
        cpi, cpi_missing = fill_gaps(load_series("CPILFESL", data_dir), "MS")
        return cls(gdp=gdp, cpi=cpi, imputed={"GDP": gdp_missing, "CPILFESL": cpi_missing})

    def imputed_note(self, series_id: str | None = None) -> str | None:
        ids = [series_id] if series_id else list(self.imputed)
        parts = [
            f"{sid} {', '.join(t.strftime('%Y-%m') for t in self.imputed.get(sid, []))}"
            for sid in ids
            if self.imputed.get(sid)
        ]
        if not parts:
            return None
        note = ("Blank in the FRED data because it was never published; this app estimates it by "
                "log-linear interpolation between the neighboring periods: " + "; ".join(parts) + ".")
        if pd.Timestamp("2025-10-01") in self.imputed.get("CPILFESL", []):
            note += " (BLS did not release October 2025 CPI because of the federal government shutdown.)"
        return note

    def get(self, series_id: str) -> pd.Series:
        if series_id == "GDP":
            return self.gdp
        if series_id == "CPILFESL":
            return self.cpi
        raise KeyError(f"Unknown series {series_id!r}; expected 'GDP' or 'CPILFESL'")
