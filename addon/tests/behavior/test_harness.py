"""Single pytest entry for the behavior harness.

Skipped unless ``ANTHROPIC_API_KEY_TEST`` is set (see ``conftest.py``). When
enabled, it runs every scenario in :data:`scenarios.SCENARIOS` and asserts
that the aggregate pass rate is >= 90%, matching Item 17's acceptance
criterion.

The full formatted table is printed to stdout on both success and failure so
the pass-rate-per-scenario is inspectable in the pytest output.
"""
from __future__ import annotations

from .runner import format_table, run_all


def test_behavior_harness_pass_rate(behavior_api_key: str, capsys) -> None:
    results = run_all()
    table = format_table(results)
    # Use print so the table shows up in `pytest -s` output.
    print("\n" + table)

    total = sum(r.total for r in results)
    passed = sum(r.pass_count for r in results)
    pass_rate = passed / total if total else 0.0

    assert pass_rate >= 0.90, (
        f"Behavior harness pass rate {pass_rate:.0%} below 90% threshold\n{table}"
    )
