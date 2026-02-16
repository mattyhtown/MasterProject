"""
InfraAgent — infrastructure health checks and deployment readiness.

Verifies all dependencies are available (ORATS, Supabase, yfinance),
checks system resources, validates environment configuration, and
reports Docker container status.
"""

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

from ..base import BaseAgent
from ...types import AgentResult, C


class InfraAgent(BaseAgent):
    """Infrastructure health and deployment readiness."""

    def __init__(self, config=None):
        super().__init__("Infra", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "health_check")

        if action == "health_check":
            return self._health_check(context.get("orats"))
        elif action == "validate_env":
            return self._validate_env()
        elif action == "docker_status":
            return self._docker_status()
        else:
            return self._full_report(context.get("orats"))

    def _health_check(self, orats=None) -> AgentResult:
        """Verify all system dependencies are available."""
        checks = {}

        # 1. Python version
        ver = sys.version_info
        py_ok = ver.major == 3 and ver.minor >= 11
        checks["python"] = {
            "status": "OK" if py_ok else "WARNING",
            "detail": f"Python {ver.major}.{ver.minor}.{ver.micro}",
        }

        # 2. ORATS API
        if orats:
            try:
                resp = orats.summaries("SPY")
                orats_ok = resp is not None and resp.get("data") is not None
                checks["orats_api"] = {
                    "status": "OK" if orats_ok else "FAIL",
                    "detail": "Reachable" if orats_ok else "No data returned",
                }
            except Exception as e:
                checks["orats_api"] = {"status": "FAIL", "detail": str(e)}
        else:
            checks["orats_api"] = {"status": "SKIP", "detail": "No client provided"}

        # 3. yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker("SPY")
            hist = ticker.history(period="1d")
            yf_ok = len(hist) > 0
            checks["yfinance"] = {
                "status": "OK" if yf_ok else "WARNING",
                "detail": f"SPY last={hist['Close'].iloc[-1]:.2f}" if yf_ok else "No data",
            }
        except ImportError:
            checks["yfinance"] = {"status": "WARNING", "detail": "yfinance not installed"}
        except Exception as e:
            checks["yfinance"] = {"status": "WARNING", "detail": str(e)[:60]}

        # 4. Supabase
        try:
            from ...database.supabase_sync import SupabaseSync
            checks["supabase_import"] = {"status": "OK", "detail": "Module importable"}
        except ImportError:
            checks["supabase_import"] = {"status": "WARNING", "detail": "supabase not installed"}

        # 5. Disk space
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        disk_ok = free_gb > 1.0
        checks["disk_space"] = {
            "status": "OK" if disk_ok else "WARNING",
            "detail": f"{free_gb:.1f} GB free",
        }

        # 6. Required imports
        for mod_name in ["json", "urllib.request", "dataclasses", "pathlib"]:
            try:
                __import__(mod_name)
                checks[f"import_{mod_name}"] = {"status": "OK", "detail": "Available"}
            except ImportError:
                checks[f"import_{mod_name}"] = {"status": "FAIL", "detail": "Missing"}

        # Aggregate
        statuses = [c["status"] for c in checks.values()]
        overall = "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else "OK"

        return self._result(
            success=True,
            data={
                "status": overall,
                "checks": checks,
                "ok_count": statuses.count("OK"),
                "warn_count": statuses.count("WARNING"),
                "fail_count": statuses.count("FAIL"),
            },
        )

    def _validate_env(self) -> AgentResult:
        """Check all required environment variables are set."""
        required = {
            "ORATS_TOKEN": "ORATS API authentication",
        }
        optional = {
            "SUPABASE_URL": "Supabase project URL",
            "SUPABASE_KEY": "Supabase anon key",
        }

        # Try loading .env first
        from ...config import _load_env
        _load_env()

        missing_required = []
        missing_optional = []
        present = []

        for key, desc in required.items():
            val = os.environ.get(key)
            if val:
                present.append(f"{key}: set ({len(val)} chars)")
            else:
                missing_required.append(f"{key}: {desc}")

        for key, desc in optional.items():
            val = os.environ.get(key)
            if val:
                present.append(f"{key}: set ({len(val)} chars)")
            else:
                missing_optional.append(f"{key}: {desc} (optional)")

        # Try loading full config
        config_ok = True
        config_error = ""
        try:
            from ...config import load_config
            load_config()
        except Exception as e:
            config_ok = False
            config_error = str(e)

        status = "FAIL" if missing_required else "WARNING" if missing_optional else "OK"

        return self._result(
            success=True,
            data={
                "status": status,
                "present": present,
                "missing_required": missing_required,
                "missing_optional": missing_optional,
                "config_loads": config_ok,
                "config_error": config_error,
            },
        )

    def _docker_status(self) -> AgentResult:
        """Check if running in a Docker container and report resource info."""
        in_docker = (
            os.path.exists("/.dockerenv") or
            os.environ.get("DOCKER_CONTAINER") == "1"
        )

        return self._result(
            success=True,
            data={
                "in_docker": in_docker,
                "platform": platform.system(),
                "arch": platform.machine(),
                "python": platform.python_version(),
                "hostname": platform.node(),
                "pid": os.getpid(),
            },
        )

    def _full_report(self, orats=None) -> AgentResult:
        """Combined health + env + docker report."""
        health = self._health_check(orats)
        env = self._validate_env()
        docker = self._docker_status()

        statuses = [health.data["status"], env.data["status"]]
        overall = "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else "OK"

        return self._result(
            success=True,
            data={
                "status": overall,
                "health": health.data,
                "env": env.data,
                "docker": docker.data,
            },
        )

    def print_report(self, result: AgentResult) -> None:
        d = result.data
        status = d.get("status", "UNKNOWN")
        clr = C.GREEN if status == "OK" else C.YELLOW if status == "WARNING" else C.RED

        print(f"\n{C.BOLD}{'='*60}")
        print(f"  INFRASTRUCTURE HEALTH")
        print(f"{'='*60}{C.RESET}")
        print(f"  Overall: {clr}{status}{C.RESET}")

        checks = d.get("checks", d.get("health", {}).get("checks", {}))
        if checks:
            for name, c in checks.items():
                s = c["status"]
                sc = C.GREEN if s == "OK" else C.YELLOW if s == "WARNING" else C.RED
                print(f"  {sc}{s:>7}{C.RESET}  {name}: {c['detail']}")

        docker = d.get("docker", {})
        if docker:
            container = "Yes" if docker.get("in_docker") else "No"
            print(f"\n  Docker: {container}")
            print(f"  Platform: {docker.get('platform')} {docker.get('arch')}")
            print(f"  Python: {docker.get('python')}")
