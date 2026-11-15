"""Tool definitions exposed to Claude, and the code that runs them."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from . import analytics as A
from .charts import CHART_TYPES, make_chart
from .data import FredData

SERIES_ENUM = ["GDP", "CPILFESL"]

_WINDOW_PROPS: dict[str, Any] = {
    "start_date": {
        "type": "string",
        "description": "Window start. Accepts YYYY, YYYY-MM, YYYY-MM-DD or YYYYQn. Omit for the first available observation.",
    },
    "end_date": {
        "type": "string",
        "description": "Window end, same formats. Omit for the latest observation.",
    },
    "years_back": {
        "type": "number",
        "description": "Alternative to start_date: number of years before end_date (or before the latest observation). E.g. 10 for 'the past 10 years'.",
    },
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_latest_observation",
        "description": (
            "Latest available observation of a series, with the previous period, the value a year earlier "
            "and the % changes. For CPILFESL it also returns core inflation (12-month, 3-month annualized, "
            "1-month annualized). For GDP it also returns quarter-over-quarter annualized growth. "
            "Use for 'latest', 'current' or 'most recent' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"series": {"type": "string", "enum": SERIES_ENUM, "description": "GDP or CPILFESL (core CPI)."}},
            "required": ["series"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_observations",
        "description": (
            "Raw historical observations of a series over a window. Long windows are averaged to annual "
            "values automatically. Use when the user wants the actual numbers or a table."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "series": {"type": "string", "enum": SERIES_ENUM},
                **_WINDOW_PROPS,
                "frequency": {
                    "type": "string",
                    "enum": ["native", "quarterly", "annual"],
                    "description": "native = GDP quarterly / CPI monthly. quarterly averages CPI to quarters. annual averages to years.",
                },
            },
            "required": ["series"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compute_indicator",
        "description": (
            "Compute an economic indicator over a window and return summary statistics (first, last, mean, "
            "median, min, max with dates) plus the values. Indicators: "
            + "; ".join(f"{k} = {v['description']}" for k, v in A.INDICATORS.items())
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indicator": {"type": "string", "enum": list(A.INDICATORS)},
                **_WINDOW_PROPS,
            },
            "required": ["indicator"],
            "additionalProperties": False,
        },
    },
    {
        "name": "change_over_period",
        "description": (
            "How a series changed over a window: start and end values, absolute change, total % change and "
            "compound annual rate, plus growth (GDP) or inflation (CPI) statistics and per-year figures. "
            "Use for 'how has X changed over the past N years'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"series": {"type": "string", "enum": SERIES_ENUM}, **_WINDOW_PROPS},
            "required": ["series"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_growth_and_inflation",
        "description": (
            "Quantify the relationship between GDP growth and core CPI inflation over a window: averages, "
            "Pearson and Spearman correlation, correlation without the 2020-21 pandemic swings, a regression "
            "slope, lead/lag correlations and a decade-by-decade breakdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_WINDOW_PROPS},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "summarize_trends",
        "description": (
            "Digest of long-run and recent trends in both series: decade averages, best and worst GDP quarters, "
            "inflation peaks, latest readings and how they compare with a year ago. Use for summary questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_WINDOW_PROPS},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_chart",
        "description": (
            "Draw a chart that is shown to the user under your reply. Returns a description of what was drawn. "
            "Chart types: " + "; ".join(f"{k} = {v}" for k, v in CHART_TYPES.items())
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": list(CHART_TYPES)},
                **_WINDOW_PROPS,
                "title": {"type": "string", "description": "Optional chart title."},
            },
            "required": ["chart_type"],
            "additionalProperties": False,
        },
    },
]

_SCHEMAS = {t["name"]: t["input_schema"] for t in TOOLS}


class ToolInputError(ValueError):
    pass


def validate_input(name: str, args: Any) -> dict:
    """Check a tool call's input against its JSON schema (the subset these schemas use)."""
    if name not in _SCHEMAS:
        raise ToolInputError(f"Unknown tool {name!r}")
    if not isinstance(args, dict):
        raise ToolInputError("Tool input must be a JSON object")
    schema = _SCHEMAS[name]
    props = schema["properties"]
    extra = set(args) - set(props)
    if extra:
        raise ToolInputError(f"Unexpected argument(s): {sorted(extra)}")
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        raise ToolInputError(f"Missing required argument(s): {missing}")
    for key, value in args.items():
        spec = props[key]
        if spec["type"] == "string" and not isinstance(value, str):
            raise ToolInputError(f"{key} must be a string")
        if spec["type"] == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ToolInputError(f"{key} must be a number")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolInputError(f"{key} must be one of {spec['enum']}")
    return args


def _window(args: dict) -> dict:
    return {
        "start": args.get("start_date"),
        "end": args.get("end_date"),
        "years_back": args.get("years_back"),
    }


@dataclass
class ChartResult:
    chart_type: str
    figure: Any
    description: str


@dataclass
class ToolExecutor:
    data: FredData
    charts: list[ChartResult] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def _handlers(self) -> dict[str, Callable[[dict], dict]]:
        d = self.data
        return {
            "get_latest_observation": lambda a: A.latest_observation(d, a["series"]),
            "get_observations": lambda a: A.get_observations(d, a["series"], frequency=a.get("frequency", "native"), **_window(a)),
            "compute_indicator": lambda a: A.compute_indicator(d, a["indicator"], **_window(a)),
            "change_over_period": lambda a: A.change_over_period(d, a["series"], **_window(a)),
            "compare_growth_and_inflation": lambda a: A.growth_vs_inflation(d, **_window(a)),
            "summarize_trends": lambda a: A.key_trends(d, **_window(a)),
            "create_chart": self._chart,
        }

    def _chart(self, a: dict) -> dict:
        fig, desc = make_chart(self.data, a["chart_type"], title=a.get("title"), **_window(a))
        self.charts.append(ChartResult(a["chart_type"], fig, desc))
        return {"status": "chart displayed to the user", "chart_number": len(self.charts), "description": desc}

    def _touches_cpi(self, name: str, args: dict) -> bool:
        if args.get("series") == "GDP":
            return False
        if name == "compute_indicator":
            return A.INDICATORS[args["indicator"]]["series"] != "GDP"
        if name == "create_chart":
            return not args["chart_type"].startswith("gdp")
        return True

    def run(self, name: str, args: Any) -> tuple[str, bool]:
        """Execute one tool call. Returns (JSON text for the tool_result, is_error)."""
        try:
            args = validate_input(name, args)
            result = self._handlers()[name](args)
            note = self.data.imputed_note("CPILFESL") if self._touches_cpi(name, args) else None
            if note:
                result["data_note"] = note
            self.calls.append({"tool": name, "input": args, "ok": True})
            return json.dumps(result), False
        except (ToolInputError, ValueError, KeyError) as exc:
            self.calls.append({"tool": name, "input": args, "ok": False, "error": str(exc)})
            return json.dumps({"error": str(exc)}), True
