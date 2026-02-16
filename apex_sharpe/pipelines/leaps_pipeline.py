"""
LEAPSPipeline — LEAPS / Poor Man's Covered Call management pipeline.

Modes:
  - scan: Find new LEAPS entry opportunities
  - manage: Check rolls and expirations
  - status: Display current PMCC positions
"""

from ..agents.leaps import LEAPSAgent
from ..agents.database import DatabaseAgent
from ..config import AppConfig
from ..data.orats_client import ORATSClient
from ..data.state import StateManager
from ..types import C


class LEAPSPipeline:
    """LEAPS / PMCC management pipeline."""

    def __init__(self, config: AppConfig, orats: ORATSClient,
                 state: StateManager):
        self.config = config
        self.orats = orats
        self.state = state
        self.agent = LEAPSAgent(config.leaps)
        self.db = DatabaseAgent(config.supabase)

    def run_scan(self, ticker: str = "SPY") -> None:
        """Scan for new LEAPS entry."""
        positions = self.state.load_positions()
        result = self.agent.run({
            "action": "scan",
            "orats": self.orats,
            "positions": positions,
            "ticker": ticker,
        })

        if result.success:
            self.agent.print_scan(result.data)
        else:
            for msg in result.messages:
                print(f"  {C.YELLOW}{msg}{C.RESET}")
            for err in result.errors:
                print(f"  {C.RED}{err}{C.RESET}")

    def run_manage(self) -> None:
        """Check for rolls and maintenance."""
        positions = self.state.load_positions()

        # Check short leg rolls
        short_result = self.agent.run({
            "action": "roll_short",
            "orats": self.orats,
            "positions": positions,
        })
        for msg in short_result.messages:
            print(f"  {msg}")
        for roll in short_result.data.get("rolls", []):
            print(f"    {C.YELLOW}ROLL: {roll['ticker']} "
                  f"{roll['short_expiry']} ${roll['short_strike']:.0f}C "
                  f"— {roll['reason']}{C.RESET}")

        # Check LEAPS rolls
        leaps_result = self.agent.run({
            "action": "roll_leaps",
            "orats": self.orats,
            "positions": positions,
        })
        for msg in leaps_result.messages:
            print(f"  {msg}")
        for roll in leaps_result.data.get("rolls", []):
            print(f"    {C.RED}ROLL LEAPS: {roll['ticker']} "
                  f"{roll['leaps_expiry']} ${roll['leaps_strike']:.0f}C "
                  f"— {roll['reason']}{C.RESET}")

    def run_status(self) -> None:
        """Display LEAPS positions."""
        positions = self.state.load_positions()
        result = self.agent.run({
            "action": "status",
            "orats": self.orats,
            "positions": positions,
        })
        for msg in result.messages:
            print(f"  {msg}")
