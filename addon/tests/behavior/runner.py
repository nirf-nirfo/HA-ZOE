"""Runs the canonical behavior scenarios against the real ``run_model``.

Public entry point: :func:`run_all`. It iterates through
:data:`scenarios.SCENARIOS`, invokes the ACTUAL ``run_model`` from
``app.claude_agent`` (so the harness exercises the deployed prompt, tools and
model routing), captures the resulting tool_use blocks, and reports pass/fail
per run plus an aggregate.

The harness never *executes* a returned tool call — it only inspects that the
model chose the right tool with the right arguments. Real tool execution
would require a full HA + WhatsApp stack; the harness is about the model's
routing decision, which is what "she's inconsistent" complaints boil down to.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app import claude_agent

from . import scenarios as _scenarios


@dataclass
class RunResult:
    passed: bool
    tool_call: str | None
    args: dict[str, Any]
    elapsed_s: float
    reason: str = ""  # populated on failure


@dataclass
class ScenarioResult:
    name: str
    runs: list[RunResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def total(self) -> int:
        return len(self.runs)

    @property
    def pass_rate(self) -> float:
        return self.pass_count / self.total if self.total else 0.0

    @property
    def avg_elapsed(self) -> float:
        if not self.runs:
            return 0.0
        return sum(r.elapsed_s for r in self.runs) / len(self.runs)


def _first_tool_use(resp: Any) -> tuple[str | None, dict[str, Any]]:
    """Extracts the first tool_use block from an Anthropic Message."""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            return block.name, dict(block.input or {})
    return None, {}


def _args_match(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, str]:
    """Checks that every expected key/value is satisfied by actual args."""
    for key, expected_val in expected.items():
        if key not in actual:
            return False, f"missing arg '{key}'"
        got = actual[key]
        if expected_val == "__any__":
            continue
        if isinstance(expected_val, str) and isinstance(got, str):
            if expected_val.lower() not in got.lower() and got.lower() != expected_val.lower():
                return False, f"arg '{key}' = {got!r}, expected to contain {expected_val!r}"
        elif isinstance(expected_val, dict) and isinstance(got, dict):
            for sub_k, sub_v in expected_val.items():
                if got.get(sub_k) != sub_v:
                    return False, f"arg '{key}.{sub_k}' = {got.get(sub_k)!r}, expected {sub_v!r}"
        else:
            if got != expected_val:
                return False, f"arg '{key}' = {got!r}, expected {expected_val!r}"
    return True, ""


def _run_one(scenario: dict[str, Any]) -> RunResult:
    """Sends the scenario's user message through run_model once."""
    # Minimal user turn — the harness does NOT prepend a real device catalog
    # or known-facts block. Scenarios are chosen so that decision is unambiguous
    # from the message text alone; adding a device catalog would only be needed
    # for device-control scenarios, and even there the model tolerates a bare
    # message enough to pick the right tool. This keeps the harness cheap and
    # deterministic.
    messages = [{"role": "user", "content": scenario["user_message"]}]

    start = time.perf_counter()
    try:
        resp = claude_agent.run_model(messages)
    except Exception as exc:  # noqa: BLE001
        return RunResult(
            passed=False,
            tool_call=None,
            args={},
            elapsed_s=time.perf_counter() - start,
            reason=f"run_model raised: {exc!r}",
        )
    elapsed = time.perf_counter() - start

    tool_name, args = _first_tool_use(resp)
    expected_tool = scenario["expected_tool_call"]

    if expected_tool is None:
        if tool_name is None:
            return RunResult(True, None, {}, elapsed)
        return RunResult(
            False,
            tool_name,
            args,
            elapsed,
            reason=f"expected no tool call, got {tool_name!r}",
        )

    if tool_name != expected_tool:
        return RunResult(
            False,
            tool_name,
            args,
            elapsed,
            reason=f"expected tool {expected_tool!r}, got {tool_name!r}",
        )

    expected_args = scenario.get("expected_args_contains") or {}
    ok, why = _args_match(expected_args, args)
    if not ok:
        return RunResult(False, tool_name, args, elapsed, reason=why)
    return RunResult(True, tool_name, args, elapsed)


def run_all(scenarios: list[dict[str, Any]] | None = None) -> list[ScenarioResult]:
    """Runs every scenario ``runs`` times and returns per-scenario results."""
    scen_list = scenarios if scenarios is not None else _scenarios.SCENARIOS
    results: list[ScenarioResult] = []
    for scen in scen_list:
        sr = ScenarioResult(name=scen["name"])
        for _ in range(scen.get("runs", 3)):
            sr.runs.append(_run_one(scen))
        results.append(sr)
    return results


def format_table(results: list[ScenarioResult]) -> str:
    """Renders a human-friendly summary table (used by tests + manual runs)."""
    lines = []
    width = max((len(r.name) for r in results), default=10) + 2
    for r in results:
        status = "PASS" if r.pass_rate >= 0.99 else "FAIL" if r.pass_rate == 0 else "FLAKY"
        line = (
            f"{r.name.ljust(width)} {r.pass_count}/{r.total} {status:<5} "
            f"(avg {r.avg_elapsed:.1f}s)"
        )
        if r.pass_rate < 1.0:
            for run in r.runs:
                if not run.passed:
                    line += f"\n{'':<{width}}   - {run.reason}"
                    break
        lines.append(line)
    total_runs = sum(r.total for r in results)
    total_pass = sum(r.pass_count for r in results)
    overall = total_pass / total_runs if total_runs else 0.0
    lines.append("")
    lines.append(f"OVERALL: {total_pass}/{total_runs} ({overall:.0%})")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    import sys

    results = run_all()
    print(format_table(results))
    total = sum(r.total for r in results)
    passed = sum(r.pass_count for r in results)
    sys.exit(0 if total and passed / total >= 0.90 else 1)
