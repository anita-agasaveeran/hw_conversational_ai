"""Agent loop tests with a scripted fake client (no network, no API key)."""

from types import SimpleNamespace as NS

import pytest

from fred_chat.agent import FredAgent
from fred_chat.data import FredData


def _usage():
    return NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _text(t):
    return NS(type="text", text=t)


def _tool(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self
        self.beta = NS(messages=self)

    def create(self, **kwargs):
        # snapshot the message list so later mutation doesn't change what we recorded
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture(scope="module")
def data():
    return FredData.load()


def test_tool_round_then_answer(data):
    client = FakeClient([
        NS(content=[_tool("t1", "get_latest_observation", {"series": "GDP"}),
                    _tool("t2", "create_chart", {"chart_type": "gdp_level", "years_back": 10})],
           stop_reason="tool_use", usage=_usage(), model="claude-opus-5-5"),
        NS(content=[_text("GDP was $33.01 trillion in 2026 Q3.")], stop_reason="end_turn", usage=_usage(),
           model="claude-opus-5-5"),
    ])
    agent = FredAgent(data, client=client)
    out = agent.ask("What is the latest GDP?")

    assert out.text == "GDP was $33.01 trillion in 2026 Q3."
    assert len(out.charts) == 1
    assert [c["tool"] for c in out.tool_calls] == ["get_latest_observation", "create_chart"]
    # both tool results go back in ONE user message
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "user" and len(second[-1]["content"]) == 2
    assert {r["tool_use_id"] for r in second[-1]["content"]} == {"t1", "t2"}
    # history: user, assistant(tool_use), user(tool_result), assistant(text)
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "user", "assistant"]
    assert out.usage.api_calls == 2 and agent.total_usage.api_calls == 2


def test_request_parameters(data):
    client = FakeClient([NS(content=[_text("hi")], stop_reason="end_turn", usage=_usage(), model="m")])
    FredAgent(data, client=client).ask("hello")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": "medium"}
    assert req["fallbacks"] == "default"
    assert "tool_choice" not in req
    assert "GDP" in req["system"] and "CPILFESL" in req["system"]


def test_bad_tool_input_returns_error_result(data):
    client = FakeClient([
        NS(content=[_tool("t1", "get_observations", {"series": "UNRATE"})], stop_reason="tool_use", usage=_usage(), model="m"),
        NS(content=[_text("That series isn't available.")], stop_reason="end_turn", usage=_usage(), model="m"),
    ])
    out = FredAgent(data, client=client).ask("unemployment?")
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert out.tool_calls[0]["ok"] is False


def test_refusal_does_not_pollute_history(data):
    client = FakeClient([NS(content=[], stop_reason="refusal", usage=_usage(), model="m")])
    agent = FredAgent(data, client=client)
    out = agent.ask("something")
    assert out.stop_reason == "refusal"
    assert agent.messages == []


def test_history_carries_into_next_turn(data):
    client = FakeClient([
        NS(content=[_text("first")], stop_reason="end_turn", usage=_usage(), model="m"),
        NS(content=[_text("second")], stop_reason="end_turn", usage=_usage(), model="m"),
    ])
    agent = FredAgent(data, client=client)
    agent.ask("q1")
    agent.ask("q2")
    assert [m["role"] for m in client.requests[1]["messages"]] == ["user", "assistant", "user"]
