"""The conversational agent: Claude plus the FRED analysis tools, in a tool-use loop."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .data import SERIES_INFO, FredData
from .tools import TOOLS, ChartResult, ToolExecutor

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
MAX_TOOL_ROUNDS = 10

SYSTEM_PROMPT_TEMPLATE = """You are an economic data assistant inside a chat app. You help people explore two U.S. economic series from FRED (Federal Reserve Economic Data):

1. GDP: {gdp_title}. {gdp_units}, {gdp_adj}, quarterly, {gdp_first} to {gdp_last}. This is nominal GDP (current dollars), so its growth rate includes price changes.
2. CPILFESL: {cpi_title} ("core CPI"). {cpi_units}, {cpi_adj}, monthly, {cpi_first} to {cpi_last}.

How to answer:
- Get every number from the tools. Do not state a value, date, growth rate, correlation or trend from memory. If the tools cannot answer a question (for example about real GDP, unemployment or interest rates, which are not in this data), say so plainly and offer what the data can show.
- "Core CPI inflation" or "the current inflation rate" means the 12-month % change in core CPI unless the user asks otherwise. Mention the 3-month annualized rate when it shows a different short-term direction.
- GDP growth defaults to the year-over-year % change. The quarter-over-quarter annualized rate is the BEA headline figure; give it too when talking about the latest quarter.
- "The past N years" means N years back from the latest observation; use years_back.
- Always say which period a number refers to (e.g. "2026 Q3", "October 2026") and its units. Write GDP in trillions of dollars with one or two decimals.
- When a tool result includes a data_note about an interpolated observation, tell the user briefly if it affects the numbers you report.
- When comparing GDP growth with inflation, remember that nominal GDP growth already includes inflation, so part of any positive correlation is mechanical. Correlation is not causation.
- When the user asks for a chart, graph, plot or visualization, call create_chart (more than once if needed) and then describe what the chart shows. You may also add a chart unasked when it clearly helps a trend question.
- Keep answers short and readable: lead with the direct answer, then a few supporting points or a small markdown table. No long preambles."""


def build_system_prompt(data: FredData) -> str:
    g, c = SERIES_INFO["GDP"], SERIES_INFO["CPILFESL"]
    q = lambda t: f"{t.year} Q{(t.month - 1) // 3 + 1}"  # noqa: E731
    return SYSTEM_PROMPT_TEMPLATE.format(
        gdp_title=g["title"], gdp_units=g["units"], gdp_adj=g["adjustment"],
        gdp_first=q(data.gdp.index[0]), gdp_last=q(data.gdp.index[-1]),
        cpi_title=c["title"], cpi_units=c["units"], cpi_adj=c["adjustment"],
        cpi_first=data.cpi.index[0].strftime("%b %Y"), cpi_last=data.cpi.index[-1].strftime("%b %Y"),
    )


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    api_calls: int = 0

    def add(self, u: Any) -> None:
        self.api_calls += 1
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0


@dataclass
class TurnResult:
    text: str
    charts: list[ChartResult]
    tool_calls: list[dict]
    stop_reason: str | None
    usage: Usage
    model: str | None = None


@dataclass
class FredAgent:
    data: FredData
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    use_fallbacks: bool = os.environ.get("CLAUDE_FALLBACKS", "1") != "0"
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)
    messages: list[dict] = field(default_factory=list)
    total_usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        self.system_prompt = build_system_prompt(self.data)

    def reset(self) -> None:
        self.messages = []

    def _create(self, messages: list[dict]):
        params: dict[str, Any] = dict(
            model=self.model,
            max_tokens=16000,
            system=self.system_prompt,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},  # caches the growing conversation prefix
        )
        if self.use_fallbacks:
            # If the primary model declines a request, the API re-runs it on a fallback model.
            return self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
            )
        return self.client.messages.create(**params)

    def ask(self, user_text: str) -> TurnResult:
        """Run one user turn to completion. History is only committed if the turn succeeds."""
        messages = copy.copy(self.messages)
        messages.append({"role": "user", "content": user_text})
        executor = ToolExecutor(self.data)
        usage = Usage()
        texts: list[str] = []
        response = None

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create(messages)
            usage.add(response.usage)
            self.total_usage.add(response.usage)
            messages.append({"role": "assistant", "content": response.content})
            texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    content, is_error = executor.run(block.name, block.input)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                    )
                messages.append({"role": "user", "content": tool_results})
                continue
            if response.stop_reason == "pause_turn":
                continue
            break
        else:
            texts.append("_I stopped after too many tool calls. Try asking a narrower question._")

        stop = response.stop_reason if response is not None else None
        has_open_tool_call = any(b.type == "tool_use" for b in response.content) if response is not None else False
        if stop == "refusal":
            texts = ["I can't help with that request. I can answer questions about U.S. GDP and core CPI data."]
        elif stop == "max_tokens":
            texts.append("_The answer was cut off because it hit the length limit._")

        # Only keep the turn in history if it ended cleanly; a refusal, an unfinished tool round
        # or a truncated tool call would leave the conversation in a state the API rejects.
        clean = stop in ("end_turn", "stop_sequence") or (stop == "max_tokens" and not has_open_tool_call)
        if clean:
            self.messages = messages

        return TurnResult(
            text="\n\n".join(texts).strip() or "_(No text response.)_",
            charts=executor.charts,
            tool_calls=executor.calls,
            stop_reason=stop,
            usage=usage,
            model=getattr(response, "model", None),
        )
