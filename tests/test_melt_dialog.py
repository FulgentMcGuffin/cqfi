"""Melt dialog helpers + dialog behaviour (offscreen)."""

import os
import sys
from pathlib import Path

import polars as pl
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "cqfi" / "gui"))

from melt_dialog import (  # noqa: E402
    MeltDialog,
    default_id_column,
    eligible_id_columns,
    melt_defaults,
    melt_frame,
)

DF = pl.DataFrame(
    {"source": ["DEU", "DEU"], "date": [__import__("datetime").date(2024, 1, 1)] * 2,
     "Y005p0": [1.0, 2.0], "Y010p0": [3.0, 4.0]}
)


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT * FROM zero_rates", ("term", "zero_rate")),
        ("select date, Y005p0 from par_rates", ("term", "par_rate")),
        ("SELECT * FROM swap_par_rates WHERE ccy='USD'", ("term", "swap_par_rate")),
        ("SELECT * FROM repo_rfr_rates", ("term", "repo_rfr_rate")),
        ("SELECT date, EURUSD FROM spotfx", ("currency", "fx_rate")),
        ("SELECT 1", ("id", "value")),
        (None, ("id", "value")),
    ],
)
def test_defaults_from_sql(sql, expected):
    assert melt_defaults(DF, sql) == expected


def test_defaults_fx_from_columns():
    fx = pl.DataFrame({"date": [1], "EURUSD": [1.1], "USDJPY": [150.0]})
    assert melt_defaults(fx, None) == ("currency", "fx_rate")


def test_id_columns_exclude_floats_and_default_is_date():
    assert eligible_id_columns(DF) == ["source", "date"]
    assert default_id_column(DF) == "date"
    assert default_id_column(pl.DataFrame({"a": ["x"], "v": [1.0]})) == "a"


def test_melt_frame():
    out = melt_frame(DF, ["date"], "term", "par_rate")
    assert out.columns == ["date", "term", "par_rate"] and out.height == 6
    assert melt_frame(DF, ["source", "date"], "term", "par_rate").height == 4
    with pytest.raises(ValueError):
        melt_frame(DF, [], "a", "b")
    with pytest.raises(ValueError):
        melt_frame(DF, DF.columns, "a", "b")


def test_dialog_defaults_and_error_keeps_dialog_open(monkeypatch):
    import melt_dialog
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    shown = []
    monkeypatch.setattr(melt_dialog, "show_message", lambda parent, icon, title, *a: shown.append(title))
    dlg = MeltDialog(DF, "SELECT * FROM par_rates")
    assert dlg.id_vars() == ["date"]
    assert [dlg._value_list.item(i).text() for i in range(dlg._value_list.count())] == ["source", "Y005p0", "Y010p0"]
    assert (dlg._variable_edit.text(), dlg._value_edit.text()) == ("term", "par_rate")
    dlg._variable_edit.setText("")  # invalid -> error, no result
    dlg.accept()
    assert shown == ["Melt Failed"] and dlg.melted is None and dlg.result() == 0
    dlg._variable_edit.setText("term")
    dlg.accept()
    assert dlg.melted is not None and dlg.melted.height == 6
