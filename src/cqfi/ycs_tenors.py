"""Tenor column names and natural-language labels from ycs_data.yaml conventions."""

from __future__ import annotations

# Maps YAML vocabulary tenors -> tenor column name in the ycs_data rate tables.
TENOR_LABEL_TO_COLUMN: dict[str, str] = {
    "3M": "Y000p25",
    "6M": "Y000p5",
    "1Y": "Y001p0",
    "2Y": "Y002p0",
    "3Y": "Y003p0",
    "4Y": "Y004p0",
    "5Y": "Y005p0",
    "6Y": "Y006p0",
    "7Y": "Y007p0",
    "8Y": "Y008p0",
    "9Y": "Y009p0",
    "10Y": "Y010p0",
    "12Y": "Y012p0",
    "15Y": "Y015p0",
    "20Y": "Y020p0",
    "25Y": "Y025p0",
    "30Y": "Y030p0",
}

# Column -> years (float).
TENOR_COLUMN_TO_YEARS: dict[str, float] = {
    "Y000p25": 0.25,
    "Y000p5": 0.5,
    "Y001p0": 1.0,
    "Y002p0": 2.0,
    "Y003p0": 3.0,
    "Y004p0": 4.0,
    "Y005p0": 5.0,
    "Y006p0": 6.0,
    "Y007p0": 7.0,
    "Y008p0": 8.0,
    "Y009p0": 9.0,
    "Y010p0": 10.0,
    "Y012p0": 12.0,
    "Y015p0": 15.0,
    "Y020p0": 20.0,
    "Y025p0": 25.0,
    "Y030p0": 30.0,
}

TENOR_COLUMNS: tuple[str, ...] = tuple(TENOR_COLUMN_TO_YEARS.keys())


def column_to_label(column: str) -> str:
    for label, col in TENOR_LABEL_TO_COLUMN.items():
        if col == column:
            return label
    return column


def label_to_column(label: str) -> str | None:
    return TENOR_LABEL_TO_COLUMN.get(label.upper())

# Pillars used for sovereign bond curves. zero_rates/par_rates also carry
# 6Y/8Y/9Y (and others); they are excluded to keep bond curves unchanged.
BOND_TENOR_COLUMNS: tuple[str, ...] = tuple(
    c for c in TENOR_COLUMNS if c not in ("Y000p25", "Y006p0", "Y008p0", "Y009p0")
)
