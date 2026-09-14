"""Excel cell sanitation for the export stage (STEP 50).

Single unified sanitizer used by every string cell written to openpyxl.
Removes XML-illegal control characters, guards cell length, escapes
formula injection, and survives lone surrogates. Non-string values are
returned unchanged; only strings are sanitized.
"""
from __future__ import annotations

import re
from typing import Any

EXCEL_CELL_LIMIT = 32767
TRUNCATION_MARKER = "...[TRUNCATED]"

# XML 1.0 illegal control chars. Keep \t (0x09), \n (0x0A), \r (0x0D).
_ILLEGAL_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Lone surrogates (U+D800-U+DFFF) are invalid in well-formed XML.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_CRLF = re.compile(r"\r\n?")

FORMULA_PREFIXES = ("=", "+", "-", "@")
FORMULA_PLACEHOLDER = "'"

# Excel worksheet constraints: max 31 chars, no : \ / ? * [ ]
_SHEET_INVALID = re.compile(r"[:\\/?*\[\]]")
MAX_SHEET_NAME_LENGTH = 31


def safe_sheet_name(name: str | None, fallback: str = "Sheet") -> str:
    """Sanitize a worksheet name (length ≤31, no : \\ / ? * [ ] chars)."""
    text = str(name or "").strip()
    text = _SHEET_INVALID.sub("_", text)
    if not text:
        return fallback
    return text[:MAX_SHEET_NAME_LENGTH]


class ExcelSanitizeStats:
    """Per-export counters for export diagnostics."""

    __slots__ = (
        "cells_sanitized",
        "illegal_chars_removed",
        "cells_truncated",
        "formula_escaped",
        "cell_write_failures",
    )

    def __init__(self) -> None:
        self.cells_sanitized = 0
        self.illegal_chars_removed = 0
        self.cells_truncated = 0
        self.formula_escaped = 0
        self.cell_write_failures = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "excel_cells_sanitized": self.cells_sanitized,
            "excel_illegal_chars_removed": self.illegal_chars_removed,
            "excel_cells_truncated": self.cells_truncated,
            "excel_formula_escaped": self.formula_escaped,
            "excel_cell_write_failures": self.cell_write_failures,
        }


def sanitize_excel_value(value: Any, stats: ExcelSanitizeStats | None = None) -> Any:
    """Sanitize one value for safe openpyxl cell assignment.

    Strings: strip XML-illegal controls (keep \\t \\n \\r), normalize CRLF,
    drop lone surrogates, cap at EXCEL_CELL_LIMIT with marker, escape
    formula prefixes. Non-strings (None/int/float/bool/datetime/list/dict)
    pass through unchanged — original JSON data is never modified.
    """
    if not isinstance(value, str):
        return value
    text = value
    changed = False

    # Lone surrogates break openpyxl's XML writer; drop them so
    # workbook.save cannot fail on one bad character.
    if _LONE_SURROGATE.search(text):
        text = _LONE_SURROGATE.sub("", text)
        changed = True

    removed = len(_ILLEGAL_CTRL.findall(text))
    if removed:
        text = _ILLEGAL_CTRL.sub("", text)
        changed = True
        if stats is not None:
            stats.illegal_chars_removed += removed

    if "\r" in text:
        text = _CRLF.sub("\n", text)
        changed = True

    if len(text) > EXCEL_CELL_LIMIT:
        text = text[: EXCEL_CELL_LIMIT - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
        changed = True
        if stats is not None:
            stats.cells_truncated += 1

    if text.startswith(FORMULA_PREFIXES):
        text = FORMULA_PLACEHOLDER + text
        changed = True
        if stats is not None:
            stats.formula_escaped += 1

    if stats is not None and changed:
        stats.cells_sanitized += 1
    return text
