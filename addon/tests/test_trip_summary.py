"""Tests for the Hebrew post-shop summary composer.

Pins the wording that the price-semantics brief calls load-bearing:
- totals are labeled 'סך הסל שחושב' / 'הערכה לנקנה', never 'what you paid',
- per-line amounts render as a plain price, never 'actual paid',
- the delta phrase is scoped to bought-only, phrased as
  יקר / זול / תואם, and a small wobble collapses to 'תואם להערכה'.
Also covers degenerate shapes (missing fields, empty trips) so a
partial payload can't throw inside the loop.
"""
from app import trip_summary


def _trip(bought=None, unbought=None, paid=0, estimate=0):
    return {
        "tripId": "t1",
        "endedAt": "2026-11-18T09:00:00Z",
        "storeId": "s1",
        "paidAgorot": paid,
        "estimateAgorot": estimate,
        "bought": bought or [],
        "unbought": unbought or [],
    }


def test_bought_lines_include_quantity_and_price():
    out = trip_summary.compose(_trip(
        bought=[{"name": "חלב", "quantity": 2, "unitAgorot": 600}],
        paid=1200, estimate=1200,
    ))
    assert "חלב" in out
    assert "×2" in out
    assert "₪12" in out


def test_unbought_lines_render_without_prices():
    out = trip_summary.compose(_trip(
        unbought=[{"name": "לחם", "quantity": 1}],
    ))
    assert "לחם" in out
    assert "❌" in out


def test_totals_use_the_mandated_labels():
    out = trip_summary.compose(_trip(
        bought=[{"name": "חלב", "quantity": 1, "unitAgorot": 500}],
        paid=500, estimate=500,
    ))
    assert "סך הסל שחושב" in out
    assert "הערכה לנקנה" in out
    # The forbidden phrasings must not appear.
    assert "שילמת" not in out
    assert "actual" not in out.lower()


def test_delta_phrase_cheap():
    out = trip_summary.compose(_trip(
        bought=[{"name": "חלב", "quantity": 1, "unitAgorot": 500}],
        paid=500, estimate=1000,
    ))
    assert "זול מההערכה" in out


def test_delta_phrase_expensive():
    out = trip_summary.compose(_trip(
        bought=[{"name": "חלב", "quantity": 1, "unitAgorot": 500}],
        paid=1000, estimate=500,
    ))
    assert "יקר מההערכה" in out


def test_delta_phrase_matching_within_noise():
    # 20 agorot off → below the 50-agorot noise floor.
    out = trip_summary.compose(_trip(
        bought=[{"name": "חלב", "quantity": 1, "unitAgorot": 500}],
        paid=520, estimate=500,
    ))
    assert "תואם להערכה" in out


def test_empty_trip_still_renders_short_line():
    out = trip_summary.compose(_trip())
    assert "אין פריטים" in out


def test_missing_totals_do_not_crash():
    summary = _trip(bought=[{"name": "חלב", "quantity": 1}])
    summary.pop("paidAgorot")
    summary.pop("estimateAgorot")
    out = trip_summary.compose(summary)
    assert "₪0" in out  # totals degrade to zero, not to an error


def test_unbought_footer_mentions_both_destinations():
    out = trip_summary.compose(_trip(unbought=[{"name": "לחם", "quantity": 1}]))
    assert "רשימת הקניות" in out
    assert "משימות הבית" in out


def test_quantity_defaults_to_one_on_missing_or_bad_input():
    out = trip_summary.compose(_trip(bought=[
        {"name": "חלב", "unitAgorot": 500},  # no quantity
        {"name": "לחם", "quantity": "junk", "unitAgorot": 300},
    ]))
    assert "×1" in out


def test_line_prefers_shopper_name_over_requested():
    out = trip_summary.compose(_trip(bought=[
        {"name": "חלב 3%", "requestedName": "חלב", "quantity": 1, "unitAgorot": 500},
    ]))
    assert "חלב 3%" in out
