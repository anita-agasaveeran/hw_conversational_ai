"""Run the assignment's sample questions through the agent and save transcripts.

Needs ANTHROPIC_API_KEY (environment or .env). Writes:
  docs/sample_conversations/conversation.md   - the full transcript, one continuous chat
  docs/sample_conversations/charts/*.png      - every chart Claude created
  docs/sample_conversations/transcript.json   - machine-readable record (tool calls, usage)

Usage:  python scripts/run_samples.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from fred_chat.agent import FredAgent  # noqa: E402
from fred_chat.data import FredData  # noqa: E402

QUESTIONS = [
    "What is the latest reported U.S. GDP?",
    "How has U.S. GDP changed over the past 10 years?",
    "What is the current Core CPI inflation rate?",
    "How has Core CPI inflation changed over the past five years?",
    "What is the relationship between GDP growth and Core CPI inflation?",
    "Generate a visualization comparing GDP growth and Core CPI inflation over time.",
    "Summarize the key economic trends observed in both datasets.",
    # Follow-up and out-of-scope checks
    "And how does that compare with the 1970s?",
    "What is the current unemployment rate?",
]

OUT = ROOT / "docs" / "sample_conversations"


def main() -> None:
    load_dotenv(ROOT / ".env")
    (OUT / "charts").mkdir(parents=True, exist_ok=True)
    data = FredData.load()
    agent = FredAgent(data)

    md = [
        "# Sample conversation",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} with `{agent.model}` (effort `{agent.effort}`) "
        "as a single continuous chat, so later questions can refer to earlier answers.",
        "",
    ]
    record = []
    chart_n = 0
    for i, q in enumerate(QUESTIONS, 1):
        print(f"[{i}/{len(QUESTIONS)}] {q}", flush=True)
        result = agent.ask(q)
        md += [f"## {i}. {q}", "", f"**User:** {q}", "", "**Assistant:**", "", result.text, ""]
        charts = []
        for c in result.charts:
            chart_n += 1
            name = f"q{i:02d}_{chart_n:02d}_{c.chart_type}.png"
            c.figure.savefig(OUT / "charts" / name, dpi=150)
            charts.append(name)
            md += [f"![{c.description}](charts/{name})", ""]
        if result.tool_calls:
            md += ["<details><summary>Tool calls</summary>", ""]
            md += [f"- `{c['tool']}` `{json.dumps(c['input'])}`" + ("" if c["ok"] else f" — error: {c['error']}")
                   for c in result.tool_calls]
            md += ["", "</details>", ""]
        record.append({
            "question": q,
            "answer": result.text,
            "tool_calls": result.tool_calls,
            "charts": charts,
            "stop_reason": result.stop_reason,
            "model": result.model,
            "usage": vars(result.usage),
        })

    u = agent.total_usage
    md += ["---", "", f"Totals: {u.api_calls} API calls; input tokens: {u.input_tokens:,} uncached, "
           f"{u.cache_creation_input_tokens:,} written to cache, {u.cache_read_input_tokens:,} read from cache; "
           f"{u.output_tokens:,} output tokens."]
    (OUT / "conversation.md").write_text("\n".join(md) + "\n")
    (OUT / "transcript.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Wrote {OUT / 'conversation.md'}")


if __name__ == "__main__":
    main()
