"""Draw the system architecture diagram to docs/figures/architecture.png.

Usage:  python scripts/make_architecture_diagram.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent.parent / "docs" / "figures" / "architecture.png"

INK = "#1b1f24"
MUTED = "#5f6670"
FILL = {"ui": "#e8f0fb", "llm": "#fdeee3", "logic": "#eef6ee", "data": "#f3f1f8", "ext": "#f5f5f5"}
EDGE = {"ui": "#1f5fa8", "llm": "#d4651b", "logic": "#3b8a3e", "data": "#6b5ca5", "ext": "#8a9099"}

# name: (x, y, w, h, kind, title, subtitle)
BOXES = {
    "user": (0.3, 7.6, 2.0, 1.1, "ext", "User", "browser"),
    "ui": (3.3, 7.6, 3.0, 1.1, "ui", "Streamlit chat UI", "app.py · chat, charts, metrics"),
    "agent": (7.6, 7.6, 3.3, 1.1, "llm", "FredAgent", "agent.py · tool-use loop, history"),
    "claude": (12.7, 7.6, 3.0, 1.1, "llm", "Claude API", "claude-opus-5-5 · Messages API"),
    "tools": (7.6, 5.5, 3.3, 1.1, "logic", "ToolExecutor", "tools.py · 7 tools, input validation"),
    "analytics": (4.9, 3.4, 3.4, 1.1, "logic", "Analytics", "analytics.py · growth, inflation, stats"),
    "charts": (10.2, 3.4, 3.4, 1.1, "logic", "Charts", "charts.py · matplotlib figures"),
    "data": (7.6, 1.4, 3.3, 1.1, "data", "FredData", "data.py · load, gap-fill, align"),
    "csv": (3.3, 1.4, 3.0, 1.1, "data", "Local CSVs", "data/GDP.csv · data/CPILFESL.csv"),
    "fred": (0.3, 1.4, 2.0, 1.1, "ext", "FRED", "CSV download"),
}

ARROWS = [
    ("user", "ui", "question", "right"),
    ("ui", "agent", "ask()", "right"),
    ("agent", "claude", "messages +\ntool schemas", "right"),
    ("agent", "tools", "tool_use calls", "down"),
    ("tools", "analytics", "", "down"),
    ("tools", "charts", "", "down"),
    ("analytics", "data", "", "down"),
    ("charts", "data", "", "down"),
    ("data", "csv", "read", "left"),
    ("fred", "csv", "refresh", "right"),
]


def anchor(name: str, side: str) -> tuple[float, float]:
    x, y, w, h, *_ = BOXES[name]
    return {
        "left": (x, y + h / 2), "right": (x + w, y + h / 2),
        "top": (x + w / 2, y + h), "bottom": (x + w / 2, y),
    }[side]


def main() -> None:
    fig, ax = plt.subplots(figsize=(14, 8.2), dpi=150)
    ax.set_xlim(0, 16.0)
    ax.set_ylim(0.4, 10.0)
    ax.axis("off")

    ax.text(0.3, 9.55, "FRED Economic Chat: system architecture", fontsize=15, fontweight="bold", color=INK)
    ax.text(0.3, 9.15, "Claude chooses tools and writes the answer; every number is computed by Python code from FRED data.",
            fontsize=9.5, color=MUTED)

    for name, (x, y, w, h, kind, title, sub) in BOXES.items():
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc=FILL[kind], ec=EDGE[kind], lw=1.6))
        ax.text(x + w / 2, y + h * 0.62, title, ha="center", va="center", fontsize=11, fontweight="bold", color=INK)
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=8, color=MUTED)

    def arrow(p, q, label="", color=MUTED, rad=0.0, label_xy=None):
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=13, lw=1.3, color=color,
                                     connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2))
        if label:
            lx, ly = label_xy or ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2 + 0.18)
            ax.text(lx, ly, label, ha="left" if label_xy else "center", va="bottom" if not label_xy else "center",
                    fontsize=8, color=color)

    side = {"right": ("right", "left"), "left": ("left", "right"), "down": ("bottom", "top")}
    for a, b, label, d in ARROWS:
        sa, sb = side[d]
        p, q = anchor(a, sa), anchor(b, sb)
        if d == "down" and a == "tools":
            p = (p[0] + (-0.6 if b == "analytics" else 0.6), p[1])
        if d == "down" and b == "data":
            q = (q[0] + (-0.6 if a == "analytics" else 0.6), q[1])
        arrow(p, q, label, label_xy=(p[0] + 0.15, (p[1] + q[1]) / 2) if d == "down" and label else None)

    # return paths
    arrow((12.7, 7.85), (10.9, 7.85), "", color=EDGE["llm"])
    ax.text(11.8, 7.75, "tool_use or\nfinal text", ha="center", va="top", fontsize=8, color=EDGE["llm"])
    arrow((8.3, 6.6), (8.3, 7.6), "", color=EDGE["logic"])
    ax.text(8.15, 7.1, "tool_result\nJSON", ha="right", va="center", fontsize=8, color=EDGE["logic"])
    arrow((7.6, 7.85), (6.3, 7.85), "", color=EDGE["ui"])
    ax.text(6.95, 7.75, "answer +\nfigures", ha="center", va="top", fontsize=8, color=EDGE["ui"])

    # loop annotation
    ax.text(14.2, 6.95, "Loop until stop_reason = end_turn\n(max 10 tool rounds per question)",
            ha="center", va="center", fontsize=8.5, color=EDGE["llm"],
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec=EDGE["llm"], lw=0.8, ls="--"))

    tools_txt = ("Tools: get_latest_observation · get_observations\n"
                 "compute_indicator · change_over_period\n"
                 "compare_growth_and_inflation · summarize_trends\ncreate_chart")
    ax.text(11.2, 6.05, tools_txt, ha="left", va="center", fontsize=7.6, color=EDGE["logic"], linespacing=1.5)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
