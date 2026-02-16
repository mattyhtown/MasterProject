"""
LatencyAgent — monitors timing-critical paths in the system.

Benchmarks ORATS API calls, measures signal-to-trade latency, and
detects stale data during market hours.
"""

import time
from collections import deque
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class LatencyAgent(BaseAgent):
    """System latency monitoring and benchmarking."""

    def __init__(self, config=None):
        super().__init__("Latency", config)
        self._timings: Dict[str, deque] = {}

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "benchmark")

        if action == "benchmark":
            return self._benchmark(context.get("orats"), context.get("iterations", 3))
        elif action == "pipeline_timing":
            return self._pipeline_timing(context)
        elif action == "data_freshness":
            return self._data_freshness(context.get("summary"), context.get("wall_clock"))
        else:
            return self._report()

    def _benchmark(self, orats, iterations: int = 3) -> AgentResult:
        """Benchmark ORATS API endpoints."""
        if orats is None:
            return self._result(success=False, errors=["No ORATS client provided"])

        endpoints = {
            "summaries": lambda: orats.summaries("SPY"),
            "iv_rank": lambda: orats.iv_rank("SPY"),
            "expirations": lambda: orats.expirations("SPY"),
        }

        results = {}
        for name, fn in endpoints.items():
            times = []
            for _ in range(iterations):
                start = time.perf_counter()
                resp = fn()
                elapsed_ms = (time.perf_counter() - start) * 1000
                times.append(elapsed_ms)
                success = resp is not None and resp.get("data") is not None

            times.sort()
            self._record(name, times)

            results[name] = {
                "p50_ms": round(times[len(times) // 2], 1),
                "p95_ms": round(times[int(len(times) * 0.95)] if len(times) > 1 else times[-1], 1),
                "min_ms": round(times[0], 1),
                "max_ms": round(times[-1], 1),
                "success": success,
            }

        # Classify overall status
        max_p95 = max(r["p95_ms"] for r in results.values())
        status = "OK" if max_p95 < 5000 else "WARNING" if max_p95 < 10000 else "CRITICAL"

        return self._result(
            success=True,
            data={
                "status": status,
                "endpoints": results,
                "iterations": iterations,
                "worst_p95_ms": round(max_p95, 1),
            },
        )

    def _pipeline_timing(self, context: Dict) -> AgentResult:
        """Measure end-to-end pipeline latency from timing marks."""
        marks = context.get("timing_marks", {})
        if not marks:
            return self._result(
                success=True,
                data={"status": "NO_TIMING_DATA"},
                messages=["No timing_marks in context"],
            )

        # Expected marks: data_fetch, signal_compute, portfolio_decision,
        #                 structure_select, fill_simulate
        stages = {}
        ordered = sorted(marks.items(), key=lambda x: x[1])
        for i in range(1, len(ordered)):
            prev_name, prev_time = ordered[i - 1]
            curr_name, curr_time = ordered[i]
            stage = f"{prev_name}_to_{curr_name}"
            stages[stage] = round((curr_time - prev_time) * 1000, 1)

        total = round((ordered[-1][1] - ordered[0][1]) * 1000, 1) if len(ordered) > 1 else 0

        return self._result(
            success=True,
            data={
                "total_ms": total,
                "stages": stages,
                "stage_count": len(stages),
            },
        )

    def _data_freshness(self, summary: Optional[Dict],
                        wall_clock: Optional[float] = None) -> AgentResult:
        """Check if ORATS data is stale."""
        if not summary:
            return self._result(success=False, errors=["No summary data to check"])

        now = wall_clock or time.time()
        warnings = []

        # Check if stockPrice field exists and is reasonable
        stock_price = summary.get("stockPrice", 0)
        if stock_price <= 0:
            warnings.append("stockPrice is 0 or missing — likely stale")

        # Check trade date if present
        trade_date = summary.get("tradeDate")
        if trade_date:
            # Basic staleness: if tradeDate is not today
            from datetime import date
            today = date.today().isoformat()
            if trade_date != today:
                warnings.append(f"tradeDate={trade_date}, today={today} — data may be stale")

        status = "STALE" if warnings else "FRESH"

        return self._result(
            success=True,
            data={
                "status": status,
                "stock_price": stock_price,
                "trade_date": trade_date,
                "warnings": warnings,
            },
            messages=warnings,
        )

    def _report(self) -> AgentResult:
        """Report stored timing data."""
        report = {}
        for name, times in self._timings.items():
            sorted_t = sorted(times)
            n = len(sorted_t)
            report[name] = {
                "samples": n,
                "p50_ms": round(sorted_t[n // 2], 1) if n else 0,
                "p95_ms": round(sorted_t[int(n * 0.95)], 1) if n > 1 else (round(sorted_t[-1], 1) if n else 0),
                "min_ms": round(sorted_t[0], 1) if n else 0,
                "max_ms": round(sorted_t[-1], 1) if n else 0,
            }
        return self._result(success=True, data={"endpoints": report})

    def _record(self, name: str, times: List[float]) -> None:
        """Store timing samples in a rolling buffer."""
        if name not in self._timings:
            self._timings[name] = deque(maxlen=100)
        self._timings[name].extend(times)

    @staticmethod
    def time_call(fn, *args, **kwargs):
        """Utility: time a function call, return (result, elapsed_ms)."""
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return result, elapsed_ms

    def print_report(self, result: AgentResult) -> None:
        """Pretty-print latency report."""
        d = result.data
        print(f"\n{C.BOLD}{'='*60}")
        print(f"  LATENCY REPORT")
        print(f"{'='*60}{C.RESET}")

        endpoints = d.get("endpoints", {})
        if endpoints:
            print(f"\n  {'Endpoint':<20} {'p50':>8} {'p95':>8} {'min':>8} {'max':>8}")
            print(f"  {'-'*52}")
            for name, m in endpoints.items():
                p95 = m.get("p95_ms", 0)
                clr = C.GREEN if p95 < 2000 else C.YELLOW if p95 < 5000 else C.RED
                print(f"  {name:<20} {m.get('p50_ms', 0):>7.0f}ms"
                      f" {clr}{p95:>7.0f}ms{C.RESET}"
                      f" {m.get('min_ms', 0):>7.0f}ms {m.get('max_ms', 0):>7.0f}ms")

        status = d.get("status", "")
        if status:
            clr = C.GREEN if status == "OK" else C.YELLOW if status == "WARNING" else C.RED
            print(f"\n  Status: {clr}{status}{C.RESET}")
