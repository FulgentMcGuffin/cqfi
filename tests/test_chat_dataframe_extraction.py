"""GUI result extraction: tool messages and markdown tables -> DataFrame."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "cqfi" / "gui"))

from langchain_core.messages import ToolMessage  # noqa: E402

from cqfi.gui.chat_dialog import (  # noqa: E402
    extract_dataframe_from_messages,
    extract_markdown_table_with_date_index,
)

_PAYLOAD = {
    "columns": ["date", "Y005p0"],
    "row_count": 2,
    "rows": [{"date": "2026-09-18", "Y005p0": 3.3}, {"date": "2026-09-17", "Y005p0": 3.2}],
}


def test_mcp_text_block_content():
    msg = ToolMessage(
        content=[{"type": "text", "text": json.dumps(_PAYLOAD), "id": "x"}],
        tool_call_id="1",
    )
    df = extract_dataframe_from_messages([msg])
    assert df is not None and df.columns == ["date", "Y005p0"] and df.height == 2


def test_mcp_structured_artifact():
    msg = ToolMessage(
        content=[{"type": "text", "text": "not json"}],
        artifact={"structured_content": _PAYLOAD},
        tool_call_id="1",
    )
    assert extract_dataframe_from_messages([msg]).height == 2


def test_plain_json_string_content():
    msg = ToolMessage(content=json.dumps(_PAYLOAD), tool_call_id="1")
    assert extract_dataframe_from_messages([msg]).height == 2


def test_markdown_table_with_year_month_index():
    text = "Result:\n\n| Month | Avg |\n|---|---|\n| 2025-10 | -0.778 |\n| 2025-11 | -0.75 |\n"
    df = extract_markdown_table_with_date_index(text)
    assert df is not None
    assert df["Month"].to_list() == ["2025-10", "2025-11"]
    assert df["Avg"].to_list() == [-0.778, -0.75]


def _tool_msg(rows: list[dict], call_id: str) -> ToolMessage:
    payload = {"columns": list(rows[0]), "rows": rows, "row_count": len(rows)}
    return ToolMessage(content=json.dumps(payload), tool_call_id=call_id)


_MONTHLY = [{"month": f"2025-{m:02d}", "avg_slope": 0.7 + m / 100} for m in (10, 11, 12)]
_ANSWER = (
    "Average 0.70\n\n| Month | Avg Slope |\n|---|---|\n"
    "| 2025-10 | 0.80 |\n| 2025-11 | 0.81 |\n| 2025-12 | 0.82 |\n"
)


def _fetch(answer: str, messages):
    import asyncio

    from cqfi.gui.chat_dialog import fetch_dataframe_for_answer

    return asyncio.run(fetch_dataframe_for_answer(None, answer, messages))


def test_answer_table_prefers_matching_earlier_tool_result():
    messages = [_tool_msg(_MONTHLY, "1"), _tool_msg([{"avg_monthly_slope": 0.7026}], "2")]
    df = _fetch(_ANSWER, messages)
    assert df.columns == ["month", "avg_slope"] and df.height == 3


def test_answer_table_used_when_no_tool_result_matches():
    df = _fetch(_ANSWER, [_tool_msg([{"avg_monthly_slope": 0.7026}], "2")])
    assert df.columns == ["Month", "Avg Slope"] and df.height == 3


def test_without_answer_table_most_recent_tool_result_wins():
    messages = [_tool_msg(_MONTHLY, "1"), _tool_msg([{"latest": 1.1486}], "2")]
    assert _fetch("EUR/USD is 1.1486", messages).columns == ["latest"]
