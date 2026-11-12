"""Download-time melt dialog: pick ``unpivot`` (a.k.a. ``melt``) parameters for a dataframe."""

import re

import polars as pl
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from gui_constants import Theme, dialog_style, get_theme
from gui_utils import TitleBar, show_message

# table -> (variable_name, value_name) defaults; anything else -> ("id", "value")
_TABLE_DEFAULTS = {
    "spotfx": ("currency", "fx_rate"),
    "zero_rates": ("term", "zero_rate"),
    "par_rates": ("term", "par_rate"),
    "swap_par_rates": ("term", "swap_par_rate"),
    "repo_rfr_rates": ("term", "repo_rfr_rate"),
}
_TABLE_RE = re.compile(r"\b(%s)\b" % "|".join(_TABLE_DEFAULTS), re.IGNORECASE)
_FX_PAIR_RE = re.compile(r"[A-Z]{6}")


def melt_defaults(df: pl.DataFrame, sql: str | None) -> tuple[str, str]:
    """Default ``(variable_name, value_name)``: from the first ycs_data table in *sql*,
    else FX when every float column is named like a currency pair, else generic."""
    match = _TABLE_RE.search(sql or "")
    if match:
        return _TABLE_DEFAULTS[match.group(1).lower()]
    floats = [c for c, t in df.schema.items() if t.is_float()]
    if floats and all(_FX_PAIR_RE.fullmatch(c) for c in floats):
        return _TABLE_DEFAULTS["spotfx"]
    return "id", "value"


def eligible_id_columns(df: pl.DataFrame) -> list[str]:
    """Columns allowed as ``id_vars``: everything except float columns."""
    return [c for c, t in df.schema.items() if not t.is_float()]


def default_id_column(df: pl.DataFrame) -> str | None:
    """First date/datetime column, else the first eligible column."""
    eligible = eligible_id_columns(df)
    dates = [c for c in eligible if df.schema[c] in (pl.Date, pl.Datetime)]
    return (dates or eligible or [None])[0]


def melt_frame(
    df: pl.DataFrame, id_vars: list[str], variable_name: str, value_name: str
) -> pl.DataFrame:
    """Melt *df*; ``value_vars`` are all columns not in *id_vars*. Raises ``ValueError``/polars errors."""
    if not id_vars:
        raise ValueError("Select at least one id column.")
    value_vars = [c for c in df.columns if c not in id_vars]
    if not value_vars:
        raise ValueError("At least one column must remain as a value column.")
    if not variable_name or not value_name:
        raise ValueError("variable_name and value_name must not be empty.")
    return df.unpivot(
        on=value_vars, index=id_vars, variable_name=variable_name, value_name=value_name
    )


class MeltDialog(QDialog):
    """Modal dialog (same look as the settings dialog); ``melted`` holds the result on accept."""

    def __init__(
        self,
        df: pl.DataFrame,
        sql: str | None = None,
        parent=None,
        theme: str | Theme | None = None,
    ):
        super().__init__(parent)
        self._df = df
        self.melted: pl.DataFrame | None = None
        self._theme = theme = get_theme(theme)

        self.setStyleSheet(dialog_style(theme))
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Melt Data")
        self.setMinimumWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QWidget()
        shell.setObjectName("dialogShell")
        shell.setAttribute(Qt.WA_StyledBackground, True)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(TitleBar(self, "Melt Data", shell))

        content = QWidget()
        content.setObjectName("dialogContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("id_vars (select at least one; float columns not allowed):"))
        self._id_list = QListWidget()
        default_id = default_id_column(df)
        for col in eligible_id_columns(df):
            item = QListWidgetItem(col)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if col == default_id else Qt.CheckState.Unchecked
            )
            self._id_list.addItem(item)
        self._id_list.itemChanged.connect(self._refresh_value_vars)
        layout.addWidget(self._id_list)

        layout.addWidget(QLabel("value_vars (all columns not selected as id_vars):"))
        self._value_list = QListWidget()
        self._value_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._value_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self._value_list)

        variable_name, value_name = melt_defaults(df, sql)
        form = QFormLayout()
        self._variable_edit = QLineEdit(variable_name)
        self._value_edit = QLineEdit(value_name)
        form.addRow("variable_name:", self._variable_edit)
        form.addRow("value_name:", self._value_edit)
        layout.addLayout(form)
        shell_layout.addWidget(content, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        button_row = QWidget()
        button_row.setObjectName("dialogContent")
        button_row.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(button_row)
        row.setContentsMargins(16, 8, 16, 16)
        row.addStretch()
        row.addWidget(buttons)
        shell_layout.addWidget(button_row)

        outer.addWidget(shell)
        self._refresh_value_vars()

    def id_vars(self) -> list[str]:
        """Checked id columns, in dataframe order."""
        return [
            self._id_list.item(i).text()
            for i in range(self._id_list.count())
            if self._id_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _refresh_value_vars(self, *_) -> None:
        ids = set(self.id_vars())
        self._value_list.clear()
        self._value_list.addItems([c for c in self._df.columns if c not in ids])

    def accept(self) -> None:
        """Melt; on failure show the error and stay in the dialog."""
        try:
            self.melted = melt_frame(
                self._df,
                self.id_vars(),
                self._variable_edit.text().strip(),
                self._value_edit.text().strip(),
            )
        except Exception as exc:
            show_message(
                self,
                QMessageBox.Icon.Critical,
                "Melt Failed",
                f"Could not melt the data:\n{exc}",
                self._theme,
            )
            return
        super().accept()
