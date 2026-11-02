"""GUI chat memory: bounded turn recap and the /ctx command."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

pytest.importorskip("PySide6")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cqfi" / "gui"))

from cqfi.gui.chat_dialog import (  # noqa: E402
    MEMORY_TURNS,
    LlmWorker,
    memory_preamble,
    summarize_turn,
)

_SQL = "SELECT trade_date, price FROM bond_future_outputs WHERE convention_id='FOAT'"
_MSGS = [SimpleNamespace(tool_calls=[{"name": "run_sql", "args": {"sql": _SQL}}])]
_DF = pl.DataFrame({"trade_date": ["2026-10-26", "2026-11-01"], "price": [1.0, 2.0]})


def test_summary_has_query_sql_answer_and_data_shape():
    s = summarize_turn("show FOAT", "bond_analytics", "ok", _MSGS, _DF)
    assert "Q: show FOAT [dataset: bond_analytics]" in s
    assert _SQL in s
    assert "A: ok" in s
    assert "2 rows" in s and "trade_date 2026-10-26 to 2026-11-01" in s


def test_summary_is_bounded_and_holds_no_rows():
    big = pl.DataFrame({"d": range(10_000), "v": [1.5] * 10_000})
    s = summarize_turn("q" * 1000, "input", "a" * 5000, _MSGS, big)
    assert len(s) < 1200
    assert "1.5" not in s


def test_summary_without_sql_or_data():
    s = summarize_turn("hi", "input", "hello", [], None)
    assert "SQL:" not in s and "Plotted" not in s


def test_summary_ignores_non_sql_tool_calls():
    msgs = [SimpleNamespace(tool_calls=[{"name": "list_tables", "args": {}}])]
    assert "SQL:" not in summarize_turn("q", "input", "a", msgs, None)


def test_preamble_empty_when_no_memory():
    assert memory_preamble([]) == ""


def test_preamble_contains_turns_and_ends_with_marker():
    p = memory_preamble(["turn one", "turn two"])
    assert "turn one" in p and "turn two" in p
    assert p.endswith("New request: ")


def test_ctx_show_clear_and_passthrough():
    w = LlmWorker(None)
    assert "No chat context" in w._handle_ctx_command("/ctx")
    w._memory.append("Q: x")
    assert "Q: x" in w._handle_ctx_command("/CTX")
    assert "cleared" in w._handle_ctx_command("/ctx clear")
    assert w._memory == []
    assert "Usage" in w._handle_ctx_command("/ctx bogus")
    assert w._handle_ctx_command("/bond") is None
    assert w._handle_ctx_command("show ctx") is None


def test_memory_turn_limit_constant():
    assert MEMORY_TURNS == 5
