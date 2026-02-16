"""
LibrarianAgent — scientific data formatting and report generation.

Formats research output into clean, structured reports:
  - Summary statistics tables
  - Formatted data series with proper sig figs
  - Cross-reference reports
  - Exportable research notes
"""

import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class LibrarianAgent(BaseAgent):
    """Formats research data into clean, structured reports."""

    def __init__(self, config=None):
        super().__init__("Librarian", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "format")

        if action == "format":
            return self._format_report(context.get("data", {}),
                                       context.get("title", "Research Report"),
                                       context.get("sections", []))
        elif action == "summary_stats":
            return self._summary_stats(context.get("values", []),
                                       context.get("label", ""))
        elif action == "compare_table":
            return self._compare_table(context.get("rows", []),
                                       context.get("columns", []),
                                       context.get("title", ""))
        elif action == "time_series":
            return self._time_series_report(context.get("series", {}),
                                            context.get("title", ""))
        elif action == "research_note":
            return self._research_note(context)
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _format_report(self, data: Dict, title: str,
                       sections: List[str]) -> AgentResult:
        """Format arbitrary data into a structured report."""
        lines = []
        lines.append(f"{'='*74}")
        lines.append(f"  {title.upper()}")
        lines.append(f"{'='*74}")
        lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        if sections:
            for section in sections:
                if section in data:
                    lines.append(f"  --- {section.replace('_', ' ').title()} ---")
                    lines.extend(self._format_value(data[section], indent=2))
                    lines.append("")
        else:
            lines.extend(self._format_value(data, indent=2))

        report = "\n".join(lines)

        return self._result(
            success=True,
            data={
                "report": report,
                "title": title,
                "lines": len(lines),
            },
        )

    def _summary_stats(self, values: List[float],
                       label: str) -> AgentResult:
        """Compute and format summary statistics."""
        if not values:
            return self._result(success=False, errors=["No values provided"])

        n = len(values)
        sorted_v = sorted(values)

        stats = {
            "label": label,
            "n": n,
            "mean": self._fmt(self._mean(values)),
            "std": self._fmt(self._std_val(values)),
            "min": self._fmt(min(values)),
            "p5": self._fmt(sorted_v[int(n * 0.05)]),
            "p25": self._fmt(sorted_v[int(n * 0.25)]),
            "median": self._fmt(sorted_v[n // 2]),
            "p75": self._fmt(sorted_v[int(n * 0.75)]),
            "p95": self._fmt(sorted_v[int(n * 0.95)]),
            "max": self._fmt(max(values)),
            "skewness": self._fmt(self._skewness(values)),
            "kurtosis": self._fmt(self._kurtosis(values)),
        }

        # Formatted output
        lines = [
            f"  Summary Statistics: {label}",
            f"  {'─'*40}",
            f"  N           {stats['n']:>12,}",
            f"  Mean        {stats['mean']:>12}",
            f"  Std Dev     {stats['std']:>12}",
            f"  Min         {stats['min']:>12}",
            f"  P5          {stats['p5']:>12}",
            f"  P25         {stats['p25']:>12}",
            f"  Median      {stats['median']:>12}",
            f"  P75         {stats['p75']:>12}",
            f"  P95         {stats['p95']:>12}",
            f"  Max         {stats['max']:>12}",
            f"  Skewness    {stats['skewness']:>12}",
            f"  Kurtosis    {stats['kurtosis']:>12}",
        ]

        return self._result(
            success=True,
            data={
                "stats": stats,
                "formatted": "\n".join(lines),
            },
        )

    def _compare_table(self, rows: List[Dict], columns: List[str],
                       title: str) -> AgentResult:
        """Format a comparison table with aligned columns."""
        if not rows or not columns:
            return self._result(success=False,
                                errors=["No rows or columns provided"])

        # Compute column widths
        widths = {}
        for col in columns:
            vals = [str(r.get(col, "")) for r in rows]
            widths[col] = max(len(col), max(len(v) for v in vals) if vals else 0) + 2

        # Header
        lines = []
        if title:
            lines.append(f"  {title}")
            lines.append(f"  {'─'*sum(widths.values())}")

        header = "  "
        for col in columns:
            header += f"{col:>{widths[col]}}"
        lines.append(header)
        lines.append(f"  {'─'*sum(widths.values())}")

        # Data rows
        for row in rows:
            line = "  "
            for col in columns:
                val = row.get(col, "")
                if isinstance(val, float):
                    val = self._fmt(val)
                line += f"{str(val):>{widths[col]}}"
            lines.append(line)

        return self._result(
            success=True,
            data={
                "table": "\n".join(lines),
                "rows": len(rows),
                "columns": columns,
            },
        )

    def _time_series_report(self, series: Dict[str, List],
                            title: str) -> AgentResult:
        """Format time series data with period stats."""
        if not series:
            return self._result(success=False, errors=["No series provided"])

        lines = [f"  {title}", f"  {'─'*60}"]
        stats = {}

        for name, values in series.items():
            if not values:
                continue

            numeric = [v for v in values if isinstance(v, (int, float))]
            if not numeric:
                continue

            s = {
                "count": len(numeric),
                "mean": round(self._mean(numeric), 6),
                "std": round(self._std_val(numeric), 6),
                "min": round(min(numeric), 6),
                "max": round(max(numeric), 6),
                "last": round(numeric[-1], 6),
            }
            stats[name] = s

            lines.append(f"\n  {name}:")
            lines.append(f"    Count: {s['count']:,}  "
                         f"Mean: {self._fmt(s['mean'])}  "
                         f"Std: {self._fmt(s['std'])}  "
                         f"Range: [{self._fmt(s['min'])}, {self._fmt(s['max'])}]")

        return self._result(
            success=True,
            data={
                "formatted": "\n".join(lines),
                "stats": stats,
            },
        )

    def _research_note(self, context: Dict) -> AgentResult:
        """Generate a formatted research note."""
        lines = []
        lines.append(f"{'='*74}")
        lines.append(f"  RESEARCH NOTE")
        lines.append(f"{'='*74}")
        lines.append(f"  Date: {datetime.now().strftime('%Y-%m-%d')}")

        if context.get("title"):
            lines.append(f"  Title: {context['title']}")
        if context.get("tickers"):
            lines.append(f"  Universe: {', '.join(context['tickers'])}")
        if context.get("period"):
            lines.append(f"  Period: {context['period']}")

        lines.append("")

        if context.get("hypothesis"):
            lines.append(f"  HYPOTHESIS")
            lines.append(f"  {'-'*40}")
            lines.append(f"  {context['hypothesis']}")
            lines.append("")

        if context.get("findings"):
            lines.append(f"  FINDINGS")
            lines.append(f"  {'-'*40}")
            for i, finding in enumerate(context["findings"], 1):
                lines.append(f"  {i}. {finding}")
            lines.append("")

        if context.get("data_summary"):
            lines.append(f"  DATA SUMMARY")
            lines.append(f"  {'-'*40}")
            lines.extend(self._format_value(context["data_summary"], indent=2))
            lines.append("")

        if context.get("conclusion"):
            lines.append(f"  CONCLUSION")
            lines.append(f"  {'-'*40}")
            lines.append(f"  {context['conclusion']}")
            lines.append("")

        lines.append(f"{'='*74}")

        return self._result(
            success=True,
            data={
                "note": "\n".join(lines),
                "lines": len(lines),
            },
        )

    def _format_value(self, value: Any, indent: int = 0) -> List[str]:
        """Recursively format a value for display."""
        prefix = "  " * indent
        lines = []

        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{prefix}{k}:")
                    lines.extend(self._format_value(v, indent + 1))
                elif isinstance(v, float):
                    lines.append(f"{prefix}{k}: {self._fmt(v)}")
                else:
                    lines.append(f"{prefix}{k}: {v}")
        elif isinstance(value, list):
            for item in value[:20]:  # Cap at 20 items
                if isinstance(item, dict):
                    lines.extend(self._format_value(item, indent))
                    lines.append(f"{prefix}{'─'*30}")
                else:
                    lines.append(f"{prefix}  {item}")
            if len(value) > 20:
                lines.append(f"{prefix}  ... ({len(value) - 20} more)")
        else:
            lines.append(f"{prefix}{value}")

        return lines

    @staticmethod
    def _fmt(value: float, sig_figs: int = 4) -> str:
        """Format a float with appropriate significant figures."""
        if value == 0:
            return "0.0000"
        if abs(value) >= 1000:
            return f"{value:,.2f}"
        if abs(value) >= 1:
            return f"{value:.4f}"
        if abs(value) >= 0.01:
            return f"{value:.6f}"
        return f"{value:.2e}"

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _std_val(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = sum(values) / len(values)
        return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))

    @staticmethod
    def _skewness(values: List[float]) -> float:
        n = len(values)
        if n < 3:
            return 0.0
        m = sum(values) / n
        s2 = sum((v - m) ** 2 for v in values) / (n - 1)
        if s2 == 0:
            return 0.0
        s = math.sqrt(s2)
        return (n / ((n - 1) * (n - 2))) * sum(((v - m) / s) ** 3 for v in values)

    @staticmethod
    def _kurtosis(values: List[float]) -> float:
        n = len(values)
        if n < 4:
            return 0.0
        m = sum(values) / n
        s2 = sum((v - m) ** 2 for v in values) / (n - 1)
        if s2 == 0:
            return 0.0
        s = math.sqrt(s2)
        k = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * \
            sum(((v - m) / s) ** 4 for v in values)
        return k - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))

    def print_report(self, result: AgentResult) -> None:
        """Print any formatted report."""
        d = result.data
        report = d.get("report") or d.get("formatted") or d.get("table") or d.get("note")
        if report:
            print(f"\n{C.BOLD}{report}{C.RESET}")
        else:
            print(f"\n{C.BOLD}{'='*74}")
            print(f"  LIBRARIAN REPORT")
            print(f"{'='*74}{C.RESET}")
            for k, v in d.items():
                print(f"  {k}: {v}")
        print()
