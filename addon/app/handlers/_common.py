"""Item 06 split: shared helpers used by multiple handler modules.

Kept minimal — anything larger belongs in its own handler.
"""
from datetime import datetime


def _valid_date(date_str: str | None) -> str | None:
    """Validates an ISO date string, returning it unchanged if valid or None otherwise."""
    if not date_str:
        return None
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        return None


def _fmt_ils(x: float) -> str:
    return f"₪{x:,.0f}" if float(x).is_integer() else f"₪{x:,.2f}"
