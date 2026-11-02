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
