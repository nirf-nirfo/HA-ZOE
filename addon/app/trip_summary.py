"""Hebrew post-shop summary composer.

Takes a trip payload (as returned by GET /api/zoe/summary → `summary`, or a
single trip from GET /api/zoe/history) and produces one WhatsApp-ready
Hebrew message. Called by `_grocery_trip_loop`; isolated so it's easy to
test against representative shapes without the loop around it.

PRICE SEMANTICS — THE WORDING IS LOAD-BEARING. The grocery API's per-line
unit/line prices are a mix of shopper-confirmed and catalog-estimate (the
API never discloses which per row). `paidAgorot` is a CALCULATED BASKET
TOTAL, not the till receipt — and both totals cover BOUGHT lines only,
so the paid-vs-estimate delta reflects price corrections on what was
bought, never the money "saved" on what was skipped.

Therefore this message:
  - calls per-line amounts "מחיר" / "סה״כ שורה", not "המחיר ששילמת",
  - labels the trip total "סך הסל שחושב" (calculated basket total),
  - labels the estimate "הערכה לנקנה" (estimate of what was bought),
  - describes the delta as "בפועל לעומת ההערכה" (actual-versus-estimate),
    phrased as 'יקר מההערכה' / 'זול מההערכה' / 'תואם להערכה',
  - says nothing about verified spending or savings-against-the-original-list.
"""
from __future__ import annotations

from app.handlers._common import _fmt_ils

# Threshold below which the delta between paid and estimate is considered
# a rounding wobble not worth narrating. 50 agorot = ₪0.50.
_DELTA_NOISE_AGOROT = 50


def _agorot_to_ils(agorot: float | int | None) -> float:
    """None / missing → 0.0. We never want the composer to raise on a
    partial payload — missing totals just get rendered as ₪0."""
    try:
        return float(agorot or 0) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _line_amount(item: dict) -> float | None:
    """Pulls the best per-line amount available: lineAgorot if present,
    else unitAgorot × quantity. Returns None if neither is usable."""
    line = item.get("lineAgorot")
    if isinstance(line, (int, float)):
        return float(line) / 100.0
    unit = item.get("unitAgorot")
    if isinstance(unit, (int, float)):
        try:
            qty = float(item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        return (float(unit) * qty) / 100.0
    return None


def _line_name(item: dict) -> str:
    """Display name — prefers the shopper's actual name over the requested
    one (a shopper who bought '3% milk' instead of '2% milk' should see
    '3% milk' in the summary)."""
    return (item.get("name") or item.get("requestedName") or "?").strip() or "?"


def _format_bought_lines(items: list[dict]) -> list[str]:
    lines: list[str] = []
    for item in items:
        name = _line_name(item)
        try:
            qty = float(item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        qty_str = f"×{int(qty)}" if qty == int(qty) else f"×{qty:g}"
        amount = _line_amount(item)
        if amount is None:
            lines.append(f"• {name} {qty_str}")
        else:
            lines.append(f"• {name} {qty_str} — {_fmt_ils(amount)}")
    return lines


def _format_unbought_lines(items: list[dict]) -> list[str]:
    lines: list[str] = []
    for item in items:
        name = _line_name(item)
        try:
            qty = float(item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        qty_str = f"×{int(qty)}" if qty == int(qty) else f"×{qty:g}"
        lines.append(f"• {name} {qty_str}")
    return lines


def _delta_phrase(paid_agorot: float, estimate_agorot: float) -> str:
    """One Hebrew line describing bought-only paid vs bought-only estimate.
    Small wobbles are collapsed into 'תואם להערכה'."""
    delta_agorot = paid_agorot - estimate_agorot
    if abs(delta_agorot) < _DELTA_NOISE_AGOROT:
        return "תואם להערכה"
    delta_ils = abs(delta_agorot) / 100.0
    direction = "יקר מההערכה" if delta_agorot > 0 else "זול מההערכה"
    return f"{direction} ב-{_fmt_ils(delta_ils)}"


def compose(summary: dict) -> str:
    """Build the full Hebrew message for one trip. Empty summary (no bought
    AND no unbought) produces a short 'nothing to report' line rather than
    an empty message — belt against a loop that fires on a degenerate trip."""
    bought = summary.get("bought") or []
    unbought = summary.get("unbought") or []

    paid = _agorot_to_ils(summary.get("paidAgorot"))
    estimate = _agorot_to_ils(summary.get("estimateAgorot"))

    if not bought and not unbought:
        return "🛒 סיום קנייה — אין פריטים בטריפ הזה."

    out: list[str] = ["🛒 סיום קנייה:"]

    if bought:
        out.append("")
        out.append(f"✅ נקנה ({len(bought)}):")
        out.extend(_format_bought_lines(bought))

    if unbought:
        out.append("")
        out.append(f"❌ לא נקנה ({len(unbought)}):")
        out.extend(_format_unbought_lines(unbought))

    out.append("")
    out.append(f"סך הסל שחושב: {_fmt_ils(paid)}")
    out.append(f"הערכה לנקנה: {_fmt_ils(estimate)}")
    # Both sides cover bought items only, so the delta commentary stays
    # honest even on trips with lots of skipped items.
    out.append(f"בפועל לעומת ההערכה: {_delta_phrase(paid * 100, estimate * 100)}")

    if unbought:
        out.append("")
        out.append(
            f"הוספתי את {len(unbought)} הפריטים שלא נקנו גם לרשימת הקניות וגם למשימות הבית."
        )

    return "\n".join(out)
