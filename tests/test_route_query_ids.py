"""route_query: bond / future id fallback routes to bond_analytics."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cqfi.agent import cli
from cqfi.config import _BOND_ANALYTICS_KEYWORDS, _INPUT_KEYWORDS


@pytest.fixture
def app(tmp_path: Path) -> SimpleNamespace:
    """Stub settings: real keywords, bond DB missing (futures codes only)."""
    cli._bond_ids.cache_clear()
    return SimpleNamespace(
        mcp_datasets={
            "input": SimpleNamespace(keywords=_INPUT_KEYWORDS),
            "bond_analytics": SimpleNamespace(keywords=_BOND_ANALYTICS_KEYWORDS),
        },
        bond_analytics_db_path=tmp_path / "missing.duckdb",
        ycs_semantics_dir=Path(__file__).parent.parent / "semantics",
    )


def _target(app, text: str) -> str | None:
    routed = cli.route_query(app, text)
    return routed.target if routed else None


@pytest.mark.parametrize(
    "text",
    [
        "show me FOAT for the last available week",
        "fgbmh6 last week",  # dated contract
        "what does fm look like",  # Bloomberg root
    ],
)
def test_future_ids_route_to_bond_analytics(app, text):
    assert _target(app, text) == "bond_analytics"


def test_analytics_keyword_routes_to_bond_analytics(app):
    assert _target(app, "show me the FOAT analytics") == "bond_analytics"


@pytest.mark.parametrize(
    "text",
    [
        "what are the rolling betas of the swiss 10y par rate versus the US 10y par rate?",
        "what is the correlation matrix of all the 10y par rates for the last month?",
        "what is the probability of the swiss franc strengthening if the japanese yen weakens over the last year?",
        "run a logistic regression of chf on yen moves",
        "for the last year, what was the daily level of the 5y20y DEU-FRA box?",
        "1y4y8y butterfly in USA over the last month",
        "5y10y steepener in ITA",
        "2y10y flattener in ESP",
    ],
)
def test_stats_questions_route_to_input(app, text):
    assert _target(app, text) == "input"


def test_input_keyword_wins_over_id(app):
    assert _target(app, "show the 3y zero rate") == "input"


def test_unrelated_text_unrouted(app):
    assert _target(app, "hello there") is None


def test_explicit_prefix_wins(app):
    routed = cli.route_query(app, "input: FOAT")
    assert routed.target == "input" and routed.text == "FOAT"


def test_bond_ids_loaded_from_db(app, monkeypatch):
    monkeypatch.setattr(cli, "_bond_ids", lambda *_: frozenset({"ust_5y_x"}))
    app.bond_analytics_db_path = Path(__file__)  # exists; mtime read only
    assert _target(app, "price ust_5y_x please") == "bond_analytics"


@pytest.mark.parametrize(
    "text",
    ["help", "/help", "?", "I want to clear the context, how do I do that?", "what commands are available?"],
)
def test_help_intent(text):
    assert "/ctx clear" in cli.help_reply(text)


@pytest.mark.parametrize("text", ["5y20y DEU-FRA box last year", "/curve swap USD 2024-01-02", "10y zero rate DEU"])
def test_help_not_triggered(text):
    assert cli.help_reply(text) is None


@pytest.mark.parametrize(
    "text",
    ["I want to clear the context, how do I do that?", "how can I reset the chat history", "forget the conversation"],
)
def test_clear_context_reply(text):
    reply = cli.help_reply(text)
    assert "/ctx clear" in reply and "Clear" in reply and len(reply) < 600


@pytest.mark.parametrize(
    "text, expected, other",
    [
        ("how do I use /calc?", "/calc <bond_id>", "/mctx <YYYY"),
        ("how do I use the bond command", "/bond <id>", "/calc <bond_id>"),
        ("what is the /mctx command?", "/mctx <YYYY-MM-DD>", "/bond <id>"),
        ("how can I run /fut", "/fut <basket|contract>", "/batch <issuer>"),
        ("how do I use /ctx", "/ctx clear", "/calc <bond_id>"),
    ],
)
def test_per_command_help(text, expected, other):
    reply = cli.help_reply(text)
    assert expected in reply and other not in reply


def test_interface_help_in_agent_prompt():
    prompt = cli.with_interface_help("PROFILE")
    assert prompt.startswith("PROFILE") and "FIRST with that exact command" in prompt and "/calc <bond_id>" in prompt
    assert cli.with_interface_help(None).startswith("Interface commands")


@pytest.mark.parametrize(
    "text",
    ["what is the chf for the last year?", "plot me CHF for the last year?", "show yen last month", "GBP over 2024", "cable this week"],
)
def test_bare_currency_routes_to_input(app, text):
    assert _target(app, text) == "input"


def test_fx_fallback_does_not_override_bond_keyword(app):
    assert _target(app, "price the EUR bond") == "bond_analytics"
