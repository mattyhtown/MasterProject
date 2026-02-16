"""Tests for Tax, Margin, and Treasury agents.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
No real market data, brokerage data, or tax records are used. P&L amounts,
account capital, margin requirements, and T-bill yields are fabricated to
test agent logic. Real tax treatment depends on actual trade dates, wash
sale rules, and IRS guidance. Real margin depends on broker-specific SPAN
or portfolio margin calculations.
"""

from apex_sharpe.agents.tax import TaxAgent
from apex_sharpe.agents.margin import MarginAgent
from apex_sharpe.agents.treasury import TreasuryAgent


class TestTaxAgent:
    def test_empty_summary(self):
        t = TaxAgent()
        result = t.run({
            "action": "summary",
            "positions": [],
            "closed_ytd": [],
        })
        assert result.success
        assert result.data["ytd_net"] == 0

    def test_1256_treatment(self):
        t = TaxAgent()
        closed = [
            {"ticker": "SPX", "exit_pnl": 10000, "status": "CLOSED"},
            {"ticker": "SPY", "exit_pnl": 5000, "status": "CLOSED"},
        ]
        result = t.run({
            "action": "summary",
            "positions": [],
            "closed_ytd": closed,
        })
        assert result.success
        assert result.data["gains_1256"] == 10000
        assert result.data["gains_equity"] == 5000
        # SPX gets better tax rate
        assert result.data["tax_1256"] < result.data["tax_equity"] * 2

    def test_loss_harvest(self):
        t = TaxAgent()
        positions = [
            {"status": "OPEN", "ticker": "SPY", "structure": "IC",
             "unrealized_pnl": -1000},
            {"status": "OPEN", "ticker": "SPX", "structure": "CDS",
             "unrealized_pnl": -200},
        ]
        result = t.run({"action": "harvest", "positions": positions})
        assert result.success
        assert result.data["count"] == 1  # Only -1000 exceeds -500 threshold


class TestMarginAgent:
    def test_empty_status(self):
        m = MarginAgent()
        result = m.run({
            "action": "status",
            "positions": [],
            "account_capital": 250000.0,
        })
        assert result.success
        assert result.data["utilization_pct"] == 0.0
        assert result.data["status"] == "OK"

    def test_trade_check(self):
        m = MarginAgent()
        result = m.run({
            "action": "check_trade",
            "positions": [],
            "account_capital": 250000.0,
            "proposed_trade": {
                "structure": "call spread",
                "width": 50,
                "qty": 1,
                "max_risk": 5000,
            },
        })
        assert result.success
        assert result.data["approved"]


class TestTreasuryAgent:
    def test_full_idle(self):
        t = TreasuryAgent()
        result = t.run({
            "account_capital": 250000.0,
            "deployed": 0.0,
            "positions": [],
        })
        assert result.success
        assert result.data["idle_cash"] == 250000.0
        assert result.data["tbill_allocation"] == 225000.0  # 250K - 10% reserve
        assert result.data["annual_yield"] == 11250.0  # 225K * 5%

    def test_ladder(self):
        t = TreasuryAgent()
        result = t.run({
            "account_capital": 250000.0,
            "deployed": 0.0,
            "positions": [],
        })
        ladder = result.data["ladder"]
        assert len(ladder) == 4  # 4 rungs
        total = sum(r["amount"] for r in ladder)
        assert abs(total - 225000.0) < 1.0

    def test_deployed_reduces_tbills(self):
        t = TreasuryAgent()
        result = t.run({
            "account_capital": 250000.0,
            "deployed": 100000.0,
            "positions": [],
        })
        assert result.data["idle_cash"] == 150000.0
        assert result.data["tbill_allocation"] == 125000.0  # 150K - 25K reserve
