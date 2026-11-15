"""Streamlit chat app: ask questions about U.S. GDP and Core CPI (FRED) in plain English.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os

import anthropic
import streamlit as st
from dotenv import load_dotenv

from fred_chat import analytics as A
from fred_chat.agent import DEFAULT_MODEL, FredAgent
from fred_chat.data import SERIES_INFO, FredData, refresh_from_fred

load_dotenv()

SAMPLE_QUESTIONS = [
    "What is the latest reported U.S. GDP?",
    "How has U.S. GDP changed over the past 10 years?",
    "What is the current Core CPI inflation rate?",
    "How has Core CPI inflation changed over the past five years?",
    "What is the relationship between GDP growth and Core CPI inflation?",
    "Generate a visualization comparing GDP growth and Core CPI inflation over time.",
    "Summarize the key economic trends observed in both datasets.",
]

st.set_page_config(page_title="FRED Economic Chat", page_icon="📈", layout="wide")


@st.cache_resource
def load_data(version: int) -> FredData:  # version busts the cache after a refresh
    return FredData.load()


def get_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY")
        except Exception:  # no secrets.toml
            key = None
    return key or st.session_state.get("api_key_input") or None


def get_agent(data: FredData) -> FredAgent | None:
    key = get_api_key()
    if not key:
        return None
    agent: FredAgent | None = st.session_state.get("agent")
    if agent is None or agent.data is not data or st.session_state.get("agent_key") != key:
        agent = FredAgent(data, client=anthropic.Anthropic(api_key=key))
        st.session_state.agent = agent
        st.session_state.agent_key = key
    return agent


def render_assistant(msg: dict) -> None:
    st.markdown(msg["text"])
    for chart in msg.get("charts", []):
        st.pyplot(chart.figure, clear_figure=False)
    calls = msg.get("tool_calls") or []
    if calls:
        with st.expander(f"How this was calculated ({len(calls)} tool call{'s' if len(calls) != 1 else ''})"):
            for c in calls:
                status = "✅" if c["ok"] else "⚠️"
                st.markdown(f"{status} `{c['tool']}` — `{c['input']}`" + (f"  \n_error: {c['error']}_" if not c["ok"] else ""))


# ----------------------------------------------------------------------------- state
st.session_state.setdefault("data_version", 0)
st.session_state.setdefault("history", [])  # list of {"role", "text", "charts", "tool_calls"}
data = load_data(st.session_state.data_version)

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("📈 FRED Economic Chat")
    st.caption(f"Model: `{DEFAULT_MODEL}`")

    if not (os.environ.get("ANTHROPIC_API_KEY")):
        st.text_input("Anthropic API key", type="password", key="api_key_input",
                      help="Or set ANTHROPIC_API_KEY in your environment or a .env file.")

    st.subheader("Data")
    for sid, series in (("GDP", data.gdp), ("CPILFESL", data.cpi)):
        freq = "Q" if sid == "GDP" else "M"
        st.markdown(
            f"**{sid}** · {SERIES_INFO[sid]['frequency'].lower()}  \n"
            f"{A.period_label(series.index[0], freq)} → {A.period_label(series.index[-1], freq)} "
            f"([FRED]({SERIES_INFO[sid]['url']}))"
        )
    note = data.imputed_note()
    if note:
        st.caption(note)

    if st.button("Refresh data from FRED", use_container_width=True):
        try:
            with st.spinner("Downloading latest observations…"):
                counts = {sid: refresh_from_fred(sid) for sid in SERIES_INFO}
            st.session_state.data_version += 1
            st.success("Updated: " + ", ".join(f"{k} ({v} obs)" for k, v in counts.items()))
            st.rerun()
        except Exception as exc:  # network errors, format changes
            st.error(f"Refresh failed; still using local CSVs. ({exc})")

    st.subheader("Try asking")
    for q in SAMPLE_QUESTIONS:
        if st.button(q, use_container_width=True, key=f"sample-{q}"):
            st.session_state.pending_question = q

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        st.session_state.history = []
        if st.session_state.get("agent"):
            st.session_state.agent.reset()
        st.rerun()

    agent_for_usage = st.session_state.get("agent")
    if agent_for_usage and agent_for_usage.total_usage.api_calls:
        u = agent_for_usage.total_usage
        st.caption(
            f"Session usage: {u.api_calls} API calls · {u.input_tokens:,} input "
            f"(+{u.cache_read_input_tokens:,} cached) · {u.output_tokens:,} output tokens"
        )

# ----------------------------------------------------------------------------- header metrics
st.title("Chat with U.S. GDP and Core CPI data")
st.caption("Ask in plain English. Every number comes from FRED data through calculation tools; Claude explains the results.")

latest_gdp = A.latest_observation(data, "GDP")
latest_cpi = A.latest_observation(data, "CPILFESL")
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"GDP ({latest_gdp['latest_period']})", f"${latest_gdp['latest_value_trillions']:.2f}T")
c2.metric("GDP growth (QoQ ann.)", f"{latest_gdp['qoq_annualized_growth_pct']:.1f}%")
c3.metric(f"Core inflation ({latest_cpi['latest_period']})", f"{latest_cpi['core_inflation_yoy_pct']:.2f}%",
          help="12-month % change in core CPI")
c4.metric("Core inflation (3-mo ann.)", f"{latest_cpi['core_inflation_3m_annualized_pct']:.2f}%")

# ----------------------------------------------------------------------------- chat
for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["text"])
        else:
            render_assistant(msg)

typed = st.chat_input("Ask about GDP, core inflation, trends, or ask for a chart…")
question = typed or st.session_state.pop("pending_question", None)

if question:
    st.session_state.history.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        agent = get_agent(data)
        if agent is None:
            msg = {"role": "assistant", "text": "Add your Anthropic API key in the sidebar (or set `ANTHROPIC_API_KEY`) to start chatting."}
        else:
            try:
                with st.spinner("Analyzing the data…"):
                    result = agent.ask(question)
                msg = {"role": "assistant", "text": result.text, "charts": result.charts, "tool_calls": result.tool_calls}
            except anthropic.AuthenticationError:
                msg = {"role": "assistant", "text": "The API key was rejected. Check it and try again."}
            except anthropic.RateLimitError:
                msg = {"role": "assistant", "text": "Rate limited by the API. Wait a moment and ask again."}
            except anthropic.APIConnectionError:
                msg = {"role": "assistant", "text": "Couldn't reach the Anthropic API. Check your internet connection."}
            except anthropic.APIStatusError as exc:
                msg = {"role": "assistant", "text": f"The API returned an error ({exc.status_code}): {exc.message}"}
        render_assistant(msg)
    st.session_state.history.append(msg)
