"""Export the standard economic charts to docs/figures/ as PNG files (no API key needed).

Usage:  python scripts/make_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fred_chat.charts import make_chart  # noqa: E402
from fred_chat.data import FredData  # noqa: E402

OUT = ROOT / "docs" / "figures"

FIGURES = [
    ("01_gdp_level.png", "gdp_level", {}),
    ("02_gdp_growth_last_10y.png", "gdp_growth", {"years_back": 10}),
    ("03_core_cpi_level.png", "core_cpi_level", {}),
    ("04_core_inflation.png", "core_inflation", {}),
    ("05_core_inflation_last_5y.png", "core_inflation", {"years_back": 5}),
    ("06_growth_vs_inflation.png", "growth_vs_inflation", {}),
    ("07_growth_vs_inflation_since_2000.png", "growth_vs_inflation", {"start": "2000"}),
    ("08_growth_vs_inflation_scatter.png", "growth_vs_inflation_scatter", {}),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = FredData.load()
    for filename, chart_type, kwargs in FIGURES:
        fig, desc = make_chart(data, chart_type, **kwargs)
        fig.savefig(OUT / filename, dpi=150)
        print(f"{filename}: {desc}")


if __name__ == "__main__":
    main()
