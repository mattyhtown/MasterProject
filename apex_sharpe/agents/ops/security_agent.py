"""
SecurityAgent — audits configuration, credentials, and operational safety.

Checks for exposed secrets, validates agent permissions, monitors for
anomalous trading activity, and ensures audit trail completeness.
"""

import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List

from ..base import BaseAgent
from ...types import AgentResult, C


# Patterns that look like API tokens / secrets
_SECRET_PATTERNS = [
    re.compile(r'["\'][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}["\']'),  # UUID tokens
    re.compile(r'(token|key|secret|password)\s*=\s*["\'][^"\']{10,}["\']', re.IGNORECASE),
    re.compile(r'sk_[a-zA-Z0-9]{20,}'),   # Stripe-style keys
    re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.]{20,}'),
]


class SecurityAgent(BaseAgent):
    """Security auditing and anomaly detection."""

    def __init__(self, config=None):
        super().__init__("Security", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "report")

        if action == "audit_config":
            return self._audit_config(context.get("project_root"))
        elif action == "audit_positions":
            return self._audit_positions(
                context.get("positions", []),
                context.get("account_capital", 250000.0),
            )
        elif action == "audit_permissions":
            return self._audit_permissions(context.get("agent_registry", {}))
        else:
            return self._full_audit(context)

    def _audit_config(self, project_root: str = None) -> AgentResult:
        """Check for exposed secrets and misconfigured permissions."""
        if not project_root:
            project_root = str(Path(__file__).resolve().parents[3])
        root = Path(project_root)

        findings = []
        passed = []

        # 1. Check .env exists and has restrictive permissions
        env_file = root / ".env"
        if env_file.exists():
            mode = env_file.stat().st_mode
            world_readable = mode & stat.S_IROTH
            if world_readable:
                findings.append({
                    "severity": "HIGH",
                    "check": "env_permissions",
                    "detail": f".env is world-readable (mode {oct(mode)}). Run: chmod 600 .env",
                })
            else:
                passed.append("env_permissions: .env has restrictive permissions")

            # Check .env has required keys
            env_content = env_file.read_text()
            for key in ["ORATS_TOKEN", "SUPABASE_URL", "SUPABASE_KEY"]:
                if key not in env_content:
                    findings.append({
                        "severity": "MEDIUM",
                        "check": "env_keys",
                        "detail": f"Missing {key} in .env",
                    })
        else:
            findings.append({
                "severity": "HIGH",
                "check": "env_exists",
                "detail": ".env file not found",
            })

        # 2. Check .gitignore covers .env
        gitignore = root / ".gitignore"
        if gitignore.exists():
            gi_content = gitignore.read_text()
            if ".env" in gi_content:
                passed.append("gitignore: .env is in .gitignore")
            else:
                findings.append({
                    "severity": "HIGH",
                    "check": "gitignore_env",
                    "detail": ".env not listed in .gitignore — secrets may be committed",
                })

        # 3. Scan Python source for hardcoded secrets
        secrets_found = 0
        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file) or "test" in py_file.name.lower():
                continue
            try:
                content = py_file.read_text()
                for pattern in _SECRET_PATTERNS:
                    matches = pattern.findall(content)
                    if matches:
                        # Exclude .env loader patterns and test fixtures
                        if "os.environ" not in content[max(0, content.find(matches[0]) - 100):]:
                            secrets_found += 1
                            findings.append({
                                "severity": "HIGH",
                                "check": "hardcoded_secret",
                                "detail": f"Possible secret in {py_file.relative_to(root)}",
                            })
            except (OSError, UnicodeDecodeError):
                pass

        if secrets_found == 0:
            passed.append("source_scan: No hardcoded secrets found in source")

        severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            severity_counts[f["severity"]] += 1

        status = "FAIL" if severity_counts["HIGH"] > 0 else "WARNING" if severity_counts["MEDIUM"] > 0 else "PASS"

        return self._result(
            success=True,
            data={
                "status": status,
                "findings": findings,
                "passed": passed,
                "severity_counts": severity_counts,
            },
        )

    def _audit_positions(self, positions: List[Dict],
                         account_capital: float) -> AgentResult:
        """Check for position anomalies."""
        findings = []
        open_positions = [p for p in positions if p.get("status") == "OPEN"]

        # Check individual position sizes
        max_single_pct = 0.10  # 10% max per position
        for pos in open_positions:
            risk = pos.get("max_risk", pos.get("max_loss", 0))
            if risk > account_capital * max_single_pct:
                findings.append({
                    "severity": "HIGH",
                    "check": "position_size",
                    "detail": (f"Position {pos.get('id', '?')} risk ${risk:,.0f} "
                               f"> {max_single_pct:.0%} of capital"),
                })

        # Check total deployed
        total_risk = sum(
            p.get("max_risk", p.get("max_loss", 0))
            for p in open_positions
        )
        if total_risk > account_capital * 0.50:
            findings.append({
                "severity": "HIGH",
                "check": "total_exposure",
                "detail": f"Total risk ${total_risk:,.0f} > 50% of capital",
            })

        # Count trades today
        from datetime import date
        today = date.today().isoformat()
        today_trades = [p for p in positions if p.get("entry_date", "").startswith(today)]
        if len(today_trades) > 10:
            findings.append({
                "severity": "MEDIUM",
                "check": "trade_frequency",
                "detail": f"{len(today_trades)} trades today — exceeds 10/day threshold",
            })

        status = "FAIL" if any(f["severity"] == "HIGH" for f in findings) else "PASS"

        return self._result(
            success=True,
            data={
                "status": status,
                "open_count": len(open_positions),
                "total_risk": round(total_risk, 2),
                "risk_pct": round(total_risk / account_capital * 100, 1) if account_capital else 0,
                "findings": findings,
            },
        )

    def _audit_permissions(self, agent_registry: Dict) -> AgentResult:
        """Verify agent risk levels match their capabilities."""
        findings = []

        for agent_name, caps in agent_registry.items():
            risk_level = caps.get("risk_level", "LOW")
            requires_approval = caps.get("requires_approval", False)
            actions = caps.get("trade_actions", [])

            # HIGH risk agents must require approval
            if risk_level == "HIGH" and not requires_approval:
                findings.append({
                    "severity": "HIGH",
                    "check": "permission_mismatch",
                    "detail": f"{agent_name}: HIGH risk but requires_approval=False",
                })

            # Agents that can execute trades must be HIGH risk
            execution_actions = {"open_position", "close_position", "execute_trade"}
            if execution_actions & set(actions) and risk_level != "HIGH":
                findings.append({
                    "severity": "MEDIUM",
                    "check": "risk_underclassified",
                    "detail": f"{agent_name}: has execution actions but risk_level={risk_level}",
                })

        status = "FAIL" if any(f["severity"] == "HIGH" for f in findings) else "PASS"

        return self._result(
            success=True,
            data={"status": status, "findings": findings, "agents_checked": len(agent_registry)},
        )

    def _full_audit(self, context: Dict) -> AgentResult:
        """Run all security checks and produce summary."""
        config_result = self._audit_config(context.get("project_root"))
        position_result = self._audit_positions(
            context.get("positions", []),
            context.get("account_capital", 250000.0),
        )

        all_findings = config_result.data["findings"] + position_result.data["findings"]
        high = sum(1 for f in all_findings if f["severity"] == "HIGH")
        medium = sum(1 for f in all_findings if f["severity"] == "MEDIUM")

        status = "FAIL" if high > 0 else "WARNING" if medium > 0 else "PASS"

        return self._result(
            success=True,
            data={
                "status": status,
                "total_findings": len(all_findings),
                "high": high,
                "medium": medium,
                "config_status": config_result.data["status"],
                "position_status": position_result.data["status"],
                "findings": all_findings,
                "passed": config_result.data.get("passed", []),
            },
        )

    def print_report(self, result: AgentResult) -> None:
        d = result.data
        status = d.get("status", "UNKNOWN")
        clr = C.GREEN if status == "PASS" else C.YELLOW if status == "WARNING" else C.RED

        print(f"\n{C.BOLD}{'='*60}")
        print(f"  SECURITY AUDIT")
        print(f"{'='*60}{C.RESET}")
        print(f"  Status: {clr}{status}{C.RESET}")
        print(f"  Findings: {d.get('high', 0)} HIGH, {d.get('medium', 0)} MEDIUM")

        for check in d.get("passed", []):
            print(f"  {C.GREEN}PASS{C.RESET}  {check}")

        for finding in d.get("findings", []):
            sev = finding["severity"]
            clr = C.RED if sev == "HIGH" else C.YELLOW
            print(f"  {clr}{sev}{C.RESET}  [{finding['check']}] {finding['detail']}")
