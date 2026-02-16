"""Tests for IB integration — IBClient, IBExecutorAgent, IBSyncAgent.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
All IB responses are mocked. No real IB connection is made.
Strike prices, fills, and account data are fabricated.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from apex_sharpe.config import IBCfg, ExecutorCfg, MonitorCfg


# ---------------------------------------------------------------------------
# Mock IB data
# ---------------------------------------------------------------------------

MOCK_ACCOUNT_SUMMARY = [
    MagicMock(tag="NetLiquidation", value="250000.00"),
    MagicMock(tag="BuyingPower", value="500000.00"),
    MagicMock(tag="TotalCashValue", value="180000.00"),
    MagicMock(tag="GrossPositionValue", value="70000.00"),
    MagicMock(tag="MaintMarginReq", value="15000.00"),
    MagicMock(tag="AvailableFunds", value="200000.00"),
    MagicMock(tag="InitMarginReq", value="20000.00"),
    MagicMock(tag="ExcessLiquidity", value="190000.00"),
]


def _mock_ib_position(symbol, sec_type, strike=0, right="", expiry="",
                       qty=1, avg_cost=100.0):
    """Build a mock IB position object."""
    pos = MagicMock()
    pos.account = "DU12345"
    pos.position = qty
    pos.avgCost = avg_cost
    c = MagicMock()
    c.symbol = symbol
    c.secType = sec_type
    c.exchange = "SMART"
    c.conId = 12345
    c.strike = strike
    c.right = right
    c.lastTradeDateOrContractMonth = expiry
    c.multiplier = "100"
    c.tradingClass = symbol
    pos.contract = c
    return pos


def _mock_fill(con_id, price, qty, commission=0.65):
    """Build a mock fill dict (matches IBClient.place_combo_order output)."""
    return {
        "conId": con_id,
        "price": price,
        "qty": qty,
        "commission": commission,
        "time": "2026-02-16 10:30:00",
    }


# ---------------------------------------------------------------------------
# IBClient tests
# ---------------------------------------------------------------------------

class TestIBClient:
    """Test IBClient with mocked ib_async module."""

    def _make_client(self, connected=True):
        """Create an IBClient with mocked internals."""
        from apex_sharpe.data.ib_client import IBClient
        cfg = IBCfg(enabled=True, host="127.0.0.1", port=4002,
                    client_id=1, paper=True)
        client = IBClient(cfg)

        # Mock the internal IB instance
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = connected
        mock_ib.managedAccounts.return_value = ["DU12345"]
        mock_ib.accountSummary.return_value = MOCK_ACCOUNT_SUMMARY
        mock_ib.positions.return_value = []
        mock_ib.pnl.return_value = []
        mock_ib.qualifyContracts = MagicMock(side_effect=lambda *args: args)

        client._ib = mock_ib
        client._connected = connected
        return client

    def test_init(self):
        from apex_sharpe.data.ib_client import IBClient
        cfg = IBCfg(port=4002, paper=True)
        client = IBClient(cfg)
        assert client.config.port == 4002
        assert client.config.paper is True
        assert not client.is_connected

    def test_is_connected_false_by_default(self):
        from apex_sharpe.data.ib_client import IBClient
        client = IBClient(IBCfg())
        assert not client.is_connected

    def test_is_connected_true_when_connected(self):
        client = self._make_client(connected=True)
        assert client.is_connected

    def test_disconnect(self):
        client = self._make_client(connected=True)
        client.disconnect()
        assert not client.is_connected
        assert client._ib is None

    def test_account_summary(self):
        client = self._make_client()
        summary = client.account_summary()
        assert summary["account"] == "DU12345"
        assert summary["NetLiquidation"] == 250000.0
        assert summary["BuyingPower"] == 500000.0
        assert summary["AvailableFunds"] == 200000.0

    def test_positions_empty(self):
        client = self._make_client()
        positions = client.positions()
        assert positions == []

    def test_positions_with_options(self):
        client = self._make_client()
        client._ib.positions.return_value = [
            _mock_ib_position("SPY", "OPT", strike=660, right="P",
                              expiry="20260313", qty=-1, avg_cost=320.0),
            _mock_ib_position("SPY", "OPT", strike=610, right="P",
                              expiry="20260313", qty=1, avg_cost=100.0),
        ]
        positions = client.positions()
        assert len(positions) == 2
        assert positions[0]["symbol"] == "SPY"
        assert positions[0]["secType"] == "OPT"
        assert positions[0]["strike"] == 660
        assert positions[0]["right"] == "P"
        assert positions[0]["qty"] == -1.0

    def test_portfolio_pnl_empty(self):
        client = self._make_client()
        pnl = client.portfolio_pnl()
        assert pnl["daily_pnl"] == 0
        assert pnl["unrealized_pnl"] == 0
        assert pnl["realized_pnl"] == 0

    def test_portfolio_pnl_with_data(self):
        client = self._make_client()
        mock_pnl = MagicMock()
        mock_pnl.dailyPnL = 500.0
        mock_pnl.unrealizedPnL = 200.0
        mock_pnl.realizedPnL = 300.0
        client._ib.pnl.return_value = [mock_pnl]
        pnl = client.portfolio_pnl()
        assert pnl["daily_pnl"] == 500.0
        assert pnl["unrealized_pnl"] == 200.0

    def test_historical_bars(self):
        client = self._make_client()
        mock_bar = MagicMock()
        mock_bar.date = "2026-02-14"
        mock_bar.open = 690.0
        mock_bar.high = 695.0
        mock_bar.low = 688.0
        mock_bar.close = 693.0
        mock_bar.volume = 50000000
        mock_bar.barCount = 100
        client._ib.reqHistoricalData.return_value = [mock_bar]
        bars = client.historical_bars("SPY", "30 D", "1 day")
        assert len(bars) == 1
        assert bars[0]["close"] == 693.0
        assert bars[0]["volume"] == 50000000

    def test_live_quote(self):
        client = self._make_client()
        mock_ticker = MagicMock()
        mock_ticker.bid = 690.50
        mock_ticker.ask = 690.80
        mock_ticker.last = 690.65
        mock_ticker.volume = 1000000
        mock_ticker.time = "2026-02-16 10:00:00"
        client._ib.reqTickers.return_value = [mock_ticker]
        quote = client.live_quote("SPY")
        assert quote["symbol"] == "SPY"
        assert quote["bid"] == 690.50
        assert quote["ask"] == 690.80


# ---------------------------------------------------------------------------
# IBExecutorAgent tests
# ---------------------------------------------------------------------------

class TestIBExecutorAgent:
    """Test IBExecutorAgent with mocked IBClient."""

    def _make_executor(self, connected=True, positions_count=0):
        """Create IBExecutorAgent with mocked IBClient."""
        from apex_sharpe.agents.ib_executor import IBExecutorAgent
        from apex_sharpe.data.ib_client import IBClient

        cfg = IBCfg(enabled=True, max_positions=10, order_timeout=5)
        mock_client = MagicMock(spec=IBClient)
        mock_client.config = cfg
        mock_client.is_connected = connected
        mock_client.positions.return_value = [
            {"secType": "OPT"} for _ in range(positions_count * 4)
        ]
        mock_client.account_summary.return_value = {
            "AvailableFunds": 200000.0,
        }
        return IBExecutorAgent(mock_client, ExecutorCfg(), MonitorCfg()), mock_client

    def test_init(self):
        executor, _ = self._make_executor()
        assert executor.name == "IBExecutor"

    def test_not_connected_returns_failure(self):
        executor, _ = self._make_executor(connected=False)
        result = executor.run({"decisions": []})
        assert not result.success
        assert "IB not connected" in result.errors

    def test_skip_blocked_candidate(self, sample_candidate):
        executor, _ = self._make_executor()
        decision = {"candidate": sample_candidate, "decision": "BLOCK",
                     "reasons": ["Risk too high"]}
        result = executor.run({"decisions": [decision]})
        assert result.success
        assert len(result.data["new_positions"]) == 0

    def test_position_limit_enforcement(self, sample_candidate):
        executor, client = self._make_executor(positions_count=10)
        decision = {"candidate": sample_candidate, "decision": "ALLOW",
                     "reasons": ["OK"]}
        result = executor.run({"decisions": [decision]})
        # Should not place order — position limit reached
        assert len(result.data["new_positions"]) == 0
        assert any("Position limit" in e for e in result.errors)

    def test_margin_check_blocks_excessive(self, sample_candidate):
        executor, client = self._make_executor()
        # whatIfOrder returns margin > 50% of available funds
        client.what_if_order.return_value = {
            "init_margin_change": 150000.0,  # 75% of $200K
            "maint_margin_change": 100000.0,
            "commission": 2.60,
        }
        decision = {"candidate": sample_candidate, "decision": "ALLOW",
                     "reasons": ["OK"]}
        result = executor.run({"decisions": [decision]})
        assert len(result.data["new_positions"]) == 0
        assert any("BLOCKED" in e for e in result.errors)

    def test_successful_fill(self, sample_candidate):
        executor, client = self._make_executor()
        client.what_if_order.return_value = {
            "init_margin_change": 5000.0,
            "maint_margin_change": 3000.0,
            "commission": 2.60,
        }
        client.place_combo_order.return_value = {
            "order_id": 42,
            "perm_id": 123456,
            "status": "Filled",
            "fills": [
                _mock_fill(1, 3.20, 1, 0.65),
                _mock_fill(2, 1.00, 1, 0.65),
                _mock_fill(3, 1.50, 1, 0.65),
                _mock_fill(4, 0.50, 1, 0.65),
            ],
            "avg_price": 3.20,
            "filled_qty": 1,
        }
        decision = {"candidate": sample_candidate, "decision": "ALLOW",
                     "reasons": ["OK"]}
        result = executor.run({"decisions": [decision]})
        assert result.success
        positions = result.data["new_positions"]
        assert len(positions) == 1
        pos = positions[0]
        assert pos["status"] == "OPEN"
        assert pos["execution_method"] == "IB"
        assert pos["ib_order_id"] == 42
        assert pos["symbol"] == "SPY"

    def test_timeout_cancels_order(self, sample_candidate):
        executor, client = self._make_executor()
        client.what_if_order.return_value = {
            "init_margin_change": 5000.0,
            "maint_margin_change": 3000.0,
            "commission": 2.60,
        }
        client.place_combo_order.return_value = {
            "order_id": 43,
            "perm_id": 0,
            "status": "CANCELLED_TIMEOUT",
            "fills": [],
            "avg_price": 0,
            "filled_qty": 0,
        }
        decision = {"candidate": sample_candidate, "decision": "ALLOW",
                     "reasons": ["OK"]}
        result = executor.run({"decisions": [decision]})
        assert len(result.data["new_positions"]) == 0
        assert any("Timeout" in e for e in result.errors)

    def test_run_close_ib_connected(self, sample_position):
        executor, client = self._make_executor()
        client.place_combo_order.return_value = {
            "order_id": 44,
            "perm_id": 123457,
            "status": "Filled",
            "fills": [
                _mock_fill(1, 0.50, 1, 0.65),
                _mock_fill(2, 0.30, 1, 0.65),
                _mock_fill(3, 0.40, 1, 0.65),
                _mock_fill(4, 0.10, 1, 0.65),
            ],
            "avg_price": 0.30,
            "filled_qty": 1,
        }
        closed = executor.run_close(sample_position, "PROFIT_TARGET", 200.0)
        assert closed["status"] == "CLOSED"
        assert closed["exit_reason"] == "PROFIT_TARGET"
        assert "realized_pnl" in closed

    def test_run_close_ib_disconnected(self, sample_position):
        executor, client = self._make_executor(connected=False)
        closed = executor.run_close(sample_position, "DTE", 100.0)
        # Falls back to simulated close
        assert closed["status"] == "CLOSED"
        assert closed["exit_reason"] == "DTE"
        assert "realized_pnl" in closed

    def test_status_action(self):
        executor, client = self._make_executor()
        # Set _ib on the mock client so status check finds it
        client._ib = MagicMock()
        client._ib.openTrades.return_value = []
        result = executor.run({"action": "status"})
        assert result.success
        assert result.data["count"] == 0

    def test_unknown_action(self):
        executor, _ = self._make_executor()
        result = executor.run({"action": "explode"})
        assert not result.success


# ---------------------------------------------------------------------------
# IBSyncAgent tests
# ---------------------------------------------------------------------------

class TestIBSyncAgent:
    """Test IBSyncAgent with mocked IBClient."""

    def _make_sync_agent(self, connected=True):
        """Create IBSyncAgent with mocked IBClient."""
        from apex_sharpe.agents.ib_sync import IBSyncAgent
        from apex_sharpe.data.ib_client import IBClient

        cfg = IBCfg(enabled=True)
        mock_client = MagicMock(spec=IBClient)
        mock_client.config = cfg
        mock_client.is_connected = connected
        mock_client.positions.return_value = []
        mock_client.account_summary.return_value = {
            "account": "DU12345",
            "NetLiquidation": 250000.0,
            "BuyingPower": 500000.0,
        }
        mock_client.portfolio_pnl.return_value = {
            "daily_pnl": 100.0,
            "unrealized_pnl": 50.0,
            "realized_pnl": 50.0,
        }
        return IBSyncAgent(mock_client), mock_client

    def test_init(self):
        agent, _ = self._make_sync_agent()
        assert agent.name == "IBSync"

    def test_not_connected(self):
        agent, _ = self._make_sync_agent(connected=False)
        result = agent.run({"action": "sync"})
        assert not result.success

    def test_sync_no_positions(self, sample_position):
        agent, client = self._make_sync_agent()
        result = agent.run({"action": "sync", "positions": [sample_position]})
        assert result.success
        assert result.data["matched"] == 0
        assert result.data["only_local"] == 1
        assert result.data["only_ib"] == 0

    def test_sync_matched_positions(self, sample_position):
        agent, client = self._make_sync_agent()
        # IB has matching position (plain dicts, as IBClient.positions() returns)
        client.positions.return_value = [
            {"symbol": "SPY", "secType": "OPT", "strike": 605, "right": "P",
             "expiry": "2026-03-13", "qty": 1, "avg_cost": 93.0, "conId": 1,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "SPY", "account": "DU12345"},
            {"symbol": "SPY", "secType": "OPT", "strike": 659, "right": "P",
             "expiry": "2026-03-13", "qty": -1, "avg_cost": 362.0, "conId": 2,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "SPY", "account": "DU12345"},
            {"symbol": "SPY", "secType": "OPT", "strike": 721, "right": "C",
             "expiry": "2026-03-13", "qty": -1, "avg_cost": 169.0, "conId": 3,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "SPY", "account": "DU12345"},
            {"symbol": "SPY", "secType": "OPT", "strike": 735, "right": "C",
             "expiry": "2026-03-13", "qty": 1, "avg_cost": 40.0, "conId": 4,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "SPY", "account": "DU12345"},
        ]
        result = agent.run({"action": "sync", "positions": [sample_position]})
        assert result.success
        assert result.data["matched"] == 1
        assert result.data["only_local"] == 0

    def test_sync_ib_only_positions(self):
        agent, client = self._make_sync_agent()
        client.positions.return_value = [
            {"symbol": "QQQ", "secType": "OPT", "strike": 400, "right": "P",
             "expiry": "20260320", "qty": -1, "avg_cost": 500.0, "conId": 10,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "QQQ", "account": "DU12345"},
        ]
        result = agent.run({"action": "sync", "positions": []})
        assert result.success
        assert result.data["only_ib"] == 1

    def test_import_positions(self):
        agent, client = self._make_sync_agent()
        client.positions.return_value = [
            {"symbol": "SPY", "secType": "OPT", "strike": 660, "right": "P",
             "expiry": "20260320", "qty": -1, "avg_cost": 320.0, "conId": 20,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "SPY", "account": "DU12345"},
            {"symbol": "SPY", "secType": "OPT", "strike": 610, "right": "P",
             "expiry": "20260320", "qty": 1, "avg_cost": 100.0, "conId": 21,
             "exchange": "SMART", "multiplier": "100", "tradingClass": "SPY", "account": "DU12345"},
        ]
        result = agent.run({"action": "import"})
        assert result.success
        assert result.data["count"] == 1
        imported = result.data["imported"][0]
        assert imported["symbol"] == "SPY"
        assert imported["execution_method"] == "IB_IMPORT"
        assert imported["status"] == "OPEN"
        assert len(imported["legs"]) == 2

    def test_account_status(self):
        agent, client = self._make_sync_agent()
        result = agent.run({"action": "account"})
        assert result.success
        assert result.data["summary"]["account"] == "DU12345"
        assert result.data["pnl"]["daily_pnl"] == 100.0

    def test_unknown_action(self):
        agent, _ = self._make_sync_agent()
        result = agent.run({"action": "invalid"})
        assert not result.success

    def test_detect_iron_condor(self):
        from apex_sharpe.agents.ib_sync import IBSyncAgent
        legs = [
            {"type": "PUT", "action": "BUY", "strike": 610},
            {"type": "PUT", "action": "SELL", "strike": 660},
            {"type": "CALL", "action": "SELL", "strike": 720},
            {"type": "CALL", "action": "BUY", "strike": 740},
        ]
        assert IBSyncAgent._detect_structure(legs) == "IRON_CONDOR"

    def test_detect_put_spread(self):
        from apex_sharpe.agents.ib_sync import IBSyncAgent
        legs = [
            {"type": "PUT", "action": "BUY", "strike": 610},
            {"type": "PUT", "action": "SELL", "strike": 660},
        ]
        assert IBSyncAgent._detect_structure(legs) == "PUT_SPREAD"

    def test_detect_call_spread(self):
        from apex_sharpe.agents.ib_sync import IBSyncAgent
        legs = [
            {"type": "CALL", "action": "BUY", "strike": 700},
            {"type": "CALL", "action": "SELL", "strike": 720},
        ]
        assert IBSyncAgent._detect_structure(legs) == "CALL_SPREAD"

    def test_detect_long_call(self):
        from apex_sharpe.agents.ib_sync import IBSyncAgent
        legs = [{"type": "CALL", "action": "BUY", "strike": 700}]
        assert IBSyncAgent._detect_structure(legs) == "LONG_CALL"

    def test_detect_short_put(self):
        from apex_sharpe.agents.ib_sync import IBSyncAgent
        legs = [{"type": "PUT", "action": "SELL", "strike": 660}]
        assert IBSyncAgent._detect_structure(legs) == "SHORT_PUT"


# ---------------------------------------------------------------------------
# IBCfg tests
# ---------------------------------------------------------------------------

class TestIBCfg:
    def test_defaults(self):
        cfg = IBCfg()
        assert cfg.enabled is False
        assert cfg.port == 4002
        assert cfg.paper is True
        assert cfg.max_positions == 10
        assert cfg.order_timeout == 60

    def test_frozen(self):
        cfg = IBCfg()
        with pytest.raises(AttributeError):
            cfg.enabled = True

    def test_custom_values(self):
        cfg = IBCfg(enabled=True, port=4001, paper=False, max_positions=5)
        assert cfg.enabled is True
        assert cfg.port == 4001
        assert cfg.paper is False
        assert cfg.max_positions == 5


# ---------------------------------------------------------------------------
# ICPipeline IB integration tests
# ---------------------------------------------------------------------------

class TestICPipelineIBIntegration:
    """Test that ICPipeline correctly selects executor based on IB config."""

    def test_simulated_executor_when_ib_disabled(self, mock_orats):
        from apex_sharpe.pipelines.ic_pipeline import ICPipeline
        from apex_sharpe.agents.executor import ExecutorAgent
        from apex_sharpe.config import load_config
        import os

        # Ensure IB disabled
        old = os.environ.get("IB_ENABLED")
        os.environ["IB_ENABLED"] = "false"
        try:
            config = load_config()
            state = MagicMock()
            pipeline = ICPipeline(config, mock_orats, state)
            assert isinstance(pipeline.executor, ExecutorAgent)
            assert pipeline._ib_mode is False
        finally:
            if old is not None:
                os.environ["IB_ENABLED"] = old
            elif "IB_ENABLED" in os.environ:
                del os.environ["IB_ENABLED"]

    def test_ib_executor_when_ib_enabled_with_client(self, mock_orats):
        from apex_sharpe.pipelines.ic_pipeline import ICPipeline
        from apex_sharpe.agents.ib_executor import IBExecutorAgent
        from apex_sharpe.config import AppConfig, IBCfg

        config = AppConfig(ib=IBCfg(enabled=True))
        state = MagicMock()
        mock_ib_client = MagicMock()
        mock_ib_client.config = IBCfg(enabled=True)
        pipeline = ICPipeline(config, mock_orats, state, ib_client=mock_ib_client)
        assert isinstance(pipeline.executor, IBExecutorAgent)
        assert pipeline._ib_mode is True

    def test_simulated_executor_when_ib_enabled_no_client(self, mock_orats):
        from apex_sharpe.pipelines.ic_pipeline import ICPipeline
        from apex_sharpe.agents.executor import ExecutorAgent
        from apex_sharpe.config import AppConfig, IBCfg

        config = AppConfig(ib=IBCfg(enabled=True))
        state = MagicMock()
        # No ib_client passed
        pipeline = ICPipeline(config, mock_orats, state)
        assert isinstance(pipeline.executor, ExecutorAgent)
        assert pipeline._ib_mode is False
