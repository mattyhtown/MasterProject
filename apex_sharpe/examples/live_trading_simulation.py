"""
Live Trading Simulation for APEX-SHARPE Trading System.

This example simulates a complete live trading day with the Iron Condor strategy:
1. Initialize connections (ORATS, Broker, Database)
2. Fetch current market data and IV rank
3. Check Sharpe ratio filter status
4. Analyze with Iron Condor strategy
5. Size positions based on risk limits
6. Execute trades (simulated paper trading)
7. Monitor open positions and Greeks
8. Check exit conditions
9. Generate alerts for position adjustments
10. Store all activity in database

This simulation demonstrates the real-time trading loop that would run
during market hours.
"""

import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List, Dict, Optional
import time as time_module

# Import APEX-SHARPE components
from apex_sharpe.data.orats_adapter import ORATSAdapter, OptionsChain, IVRankData
from apex_sharpe.strategies.iron_condor_strategy import IronCondorStrategy
from apex_sharpe.strategies.base_strategy import MultiLegSpread, SignalAction
from apex_sharpe.risk import (
    OptionsRiskManager,
    GreeksLimits,
    OptionsPositionSizer,
    ExposureMonitor,
    AlertLevel
)
from apex_sharpe.execution import OptionsPaperBroker
from apex_sharpe.greeks.greeks_calculator import GreeksCalculator
from apex_sharpe.database.supabase_client import SupabaseClient


class LiveTradingSimulation:
    """Simulates a live trading session for the APEX-SHARPE system."""

    def __init__(self):
        """Initialize the live trading system."""
        self.adapter: Optional[ORATSAdapter] = None
        self.strategy: Optional[IronCondorStrategy] = None
        self.risk_manager: Optional[OptionsRiskManager] = None
        self.broker: Optional[OptionsPaperBroker] = None
        self.exposure_monitor: Optional[ExposureMonitor] = None
        self.greeks_calc: Optional[GreeksCalculator] = None
        self.db: Optional[SupabaseClient] = None
        self.open_positions: List[MultiLegSpread] = []

    def initialize_system(self):
        """Initialize all system components."""
        print("=" * 80)
        print("  APEX-SHARPE Live Trading System - Initializing...")
        print("=" * 80)

        # Initialize ORATS adapter
        print("\n[1/7] Connecting to ORATS data feed...")
        # In production: mcp_tools = get_mcp_tools()
        # self.adapter = ORATSAdapter(mcp_tools)
        print("  ✓ ORATS adapter initialized")

        # Initialize strategy
        print("\n[2/7] Loading Iron Condor strategy...")
        self.strategy = IronCondorStrategy(
            name="LiveIronCondor",
            symbol="SPY",
            initial_capital=Decimal("100000"),
            sharpe_threshold=1.0,
            sharpe_window=30,
            iv_rank_min=Decimal("0.70"),
            delta_short_target=Decimal("0.15"),
            width=Decimal("5.00"),
            max_risk_per_trade_pct=0.01,
        )
        print(f"  ✓ Strategy loaded: {self.strategy.name}")
        print(f"    - Sharpe Threshold: {self.strategy.sharpe_threshold}")
        print(f"    - IV Rank Min: {self.strategy.iv_rank_min * 100}%")

        # Initialize risk manager
        print("\n[3/7] Setting up risk management...")
        greeks_limits = GreeksLimits(
            max_portfolio_delta=Decimal("100.0"),
            max_portfolio_gamma=Decimal("50.0"),
            max_portfolio_theta=Decimal("-500.0"),
            max_portfolio_vega=Decimal("1000.0"),
        )
        self.risk_manager = OptionsRiskManager(
            initial_capital=Decimal("100000"),
            greeks_limits=greeks_limits,
        )
        print("  ✓ Risk manager configured")
        print(f"    - Max Portfolio Delta: ±{greeks_limits.max_portfolio_delta}")

        # Initialize broker
        print("\n[4/7] Connecting to paper broker...")
        self.broker = OptionsPaperBroker(
            initial_capital=Decimal("100000"),
            commission_per_contract=Decimal("0.65"),
        )
        print("  ✓ Paper broker connected")
        print(f"    - Available Capital: ${self.broker.get_available_capital():,.2f}")

        # Initialize exposure monitor
        print("\n[5/7] Starting exposure monitor...")
        self.exposure_monitor = ExposureMonitor(
            portfolio_value=Decimal("100000"),
            greeks_limits=greeks_limits,
        )
        print("  ✓ Exposure monitor active")

        # Initialize Greeks calculator
        print("\n[6/7] Loading Greeks calculator...")
        self.greeks_calc = GreeksCalculator(risk_free_rate=0.02)
        print("  ✓ Greeks calculator ready")

        # Initialize database connection
        print("\n[7/7] Connecting to database...")
        try:
            # In production: self.db = SupabaseClient()
            print("  ✗ Database not configured (set SUPABASE_URL and SUPABASE_KEY)")
        except Exception as e:
            print(f"  ✗ Database connection failed: {e}")

        print("\n" + "=" * 80)
        print("  System Initialization Complete")
        print("=" * 80)

    def fetch_market_data(self) -> Dict:
        """Fetch current market data for SPY."""
        print("\n" + "-" * 80)
        print("Fetching Current Market Data")
        print("-" * 80)

        # In production, would fetch from ORATS
        # current_price = self.adapter.get_current_price("SPY")
        # iv_rank = self.adapter.get_iv_rank("SPY")
        # expirations = self.adapter.get_expirations("SPY")

        # Simulated data
        market_data = {
            "symbol": "SPY",
            "current_price": Decimal("450.25"),
            "timestamp": datetime.now(),
            "iv_rank": Decimal("0.75"),  # 75% IV Rank
            "iv_percentile": Decimal("0.72"),
            "current_iv": Decimal("0.18"),  # 18% IV
        }

        print(f"\nMarket Data as of {market_data['timestamp'].strftime('%H:%M:%S')}:")
        print(f"  Symbol: {market_data['symbol']}")
        print(f"  Price: ${market_data['current_price']:.2f}")
        print(f"  IV Rank: {market_data['iv_rank'] * 100:.1f}%  {'✓ Above threshold' if market_data['iv_rank'] >= self.strategy.iv_rank_min else '✗ Below threshold'}")
        print(f"  Current IV: {market_data['current_iv'] * 100:.1f}%")

        return market_data

    def check_sharpe_filter(self) -> bool:
        """Check if Sharpe ratio filter allows trading."""
        print("\n" + "-" * 80)
        print("Checking Sharpe Ratio Filter")
        print("-" * 80)

        # In production, would calculate from recent returns
        current_sharpe = 1.45  # Simulated

        can_trade = self.strategy.can_trade()

        print(f"\nRolling 30-Day Sharpe Ratio: {current_sharpe:.2f}")
        print(f"Sharpe Threshold: {self.strategy.sharpe_threshold:.2f}")

        if can_trade:
            print("✓ TRADING ALLOWED - Sharpe ratio above threshold")
        else:
            print("✗ TRADING BLOCKED - Sharpe ratio below threshold")

        return can_trade

    def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Analyze market for trading opportunities."""
        print("\n" + "-" * 80)
        print("Analyzing Trading Opportunity")
        print("-" * 80)

        # Check if strategy can trade
        if not self.check_sharpe_filter():
            return None

        # Simulate strategy analysis
        # In production:
        # chain = self.adapter.get_live_chain("SPY", target_expiration)
        # signal = self.strategy.analyze(chain, iv_data, market_data)

        print("\nStrategy Analysis:")
        print("  Looking for Iron Condor setup...")
        print("  Target DTE: 30-60 days")
        print("  Target Short Delta: 0.15")
        print("  Target Long Delta: 0.05")

        # Simulated signal
        signal = {
            "action": SignalAction.ENTER,
            "confidence": 0.85,
            "spread_type": "IRON_CONDOR",
            "expiration": date.today() + timedelta(days=45),
            "strikes": {
                "short_call": Decimal("465.00"),
                "long_call": Decimal("470.00"),
                "short_put": Decimal("435.00"),
                "long_put": Decimal("430.00"),
            },
            "premium": Decimal("2.45"),  # $2.45 credit per spread
        }

        print(f"\n✓ SIGNAL GENERATED: {signal['action']}")
        print(f"  Confidence: {signal['confidence'] * 100:.0f}%")
        print(f"  Spread Type: {signal['spread_type']}")
        print(f"  Expiration: {signal['expiration']} ({(signal['expiration'] - date.today()).days} DTE)")
        print(f"\n  Strikes:")
        print(f"    Short Call: ${signal['strikes']['short_call']:.2f}")
        print(f"    Long Call:  ${signal['strikes']['long_call']:.2f}")
        print(f"    Short Put:  ${signal['strikes']['short_put']:.2f}")
        print(f"    Long Put:   ${signal['strikes']['long_put']:.2f}")
        print(f"\n  Expected Credit: ${signal['premium']:.2f} per spread")

        return signal

    def size_position(self, signal: Dict) -> int:
        """Calculate position size based on risk limits."""
        print("\n" + "-" * 80)
        print("Position Sizing")
        print("-" * 80)

        # Calculate max risk per spread
        width = Decimal("5.00")  # $5 wide spreads
        credit = signal["premium"]
        max_risk_per_spread = (width - credit) * Decimal("100")

        print(f"\nRisk Calculation:")
        print(f"  Spread Width: ${width:.2f}")
        print(f"  Credit Received: ${credit:.2f}")
        print(f"  Max Risk per Spread: ${max_risk_per_spread:.2f}")

        # Calculate quantity based on 1% risk
        max_allocatable_risk = self.broker.get_available_capital() * Decimal("0.01")
        quantity = int(max_allocatable_risk / max_risk_per_spread)

        print(f"\n  Available Capital: ${self.broker.get_available_capital():,.2f}")
        print(f"  Max Risk (1% of capital): ${max_allocatable_risk:,.2f}")
        print(f"  Calculated Quantity: {quantity} spreads")

        # Risk manager check
        print(f"\nRisk Manager Approval:")
        # In production: risk_check = self.risk_manager.assess_trade(...)
        print(f"  ✓ Position size approved")
        print(f"  ✓ Greeks limits checked")
        print(f"  ✓ Portfolio heat acceptable")

        return quantity

    def execute_trade(self, signal: Dict, quantity: int) -> str:
        """Execute the trade via paper broker."""
        print("\n" + "-" * 80)
        print("Trade Execution")
        print("-" * 80)

        print(f"\nSubmitting Order:")
        print(f"  Strategy: Iron Condor")
        print(f"  Quantity: {quantity} spreads")
        print(f"  Expected Credit: ${signal['premium'] * quantity:.2f}")

        # Simulate order execution
        position_id = f"POS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        print(f"\n  [09:31:05] Order submitted to broker")
        time_module.sleep(0.5)
        print(f"  [09:31:05] Order working...")
        time_module.sleep(0.5)
        print(f"  [09:31:06] Order filled - All legs executed")

        fill_details = {
            "position_id": position_id,
            "fill_time": datetime.now(),
            "quantity": quantity,
            "filled_credit": signal['premium'] * quantity,
            "commission": Decimal("0.65") * 4 * quantity,  # 4 legs
            "legs": [
                {"type": "STO", "strike": signal['strikes']['short_call'], "price": Decimal("1.25")},
                {"type": "BTO", "strike": signal['strikes']['long_call'], "price": Decimal("0.45")},
                {"type": "STO", "strike": signal['strikes']['short_put'], "price": Decimal("1.30")},
                {"type": "BTO", "strike": signal['strikes']['long_put'], "price": Decimal("0.50")},
            ]
        }

        print(f"\n✓ POSITION OPENED")
        print(f"  Position ID: {position_id}")
        print(f"  Credit Received: ${fill_details['filled_credit']:.2f}")
        print(f"  Commission: ${fill_details['commission']:.2f}")
        print(f"  Net Credit: ${fill_details['filled_credit'] - fill_details['commission']:.2f}")

        # Add to open positions
        # In production: position = MultiLegSpread(...)
        # self.open_positions.append(position)

        # Store in database
        if self.db:
            print(f"\n  Storing position in database...")
            # self.db.create_position(...)
            print(f"  ✓ Position saved to database")

        return position_id

    def monitor_positions(self):
        """Monitor open positions and calculate current Greeks."""
        print("\n" + "-" * 80)
        print("Position Monitoring")
        print("-" * 80)

        # In production: fetch current positions and update Greeks
        # positions = self.broker.get_open_positions()

        # Simulated position data
        print(f"\nOpen Positions: 2")
        print("\n  Position 1: POS_20260205_093106")
        print("    Spread: Iron Condor")
        print("    DTE: 45 days")
        print("    Entry: 2 days ago")
        print("    Entry Credit: $490.00")
        print("    Current Value: $435.00")
        print("    Unrealized P&L: +$55.00 (+11.2%)")
        print("    Greeks:")
        print("      Delta:  +2.4")
        print("      Gamma:  +0.8")
        print("      Theta:  -12.5 (collecting $12.50/day)")
        print("      Vega:   +18.3")

        print("\n  Position 2: POS_20260203_140522")
        print("    Spread: Iron Condor")
        print("    DTE: 37 days")
        print("    Entry: 4 days ago")
        print("    Entry Credit: $735.00")
        print("    Current Value: $620.00")
        print("    Unrealized P&L: +$115.00 (+15.6%)")
        print("    Greeks:")
        print("      Delta:  -1.8")
        print("      Gamma:  +1.2")
        print("      Theta:  -18.7 (collecting $18.70/day)")
        print("      Vega:   +27.4")

        print("\n  Portfolio Greeks:")
        print("    Total Delta:  +0.6   (Limit: ±100)")
        print("    Total Gamma:  +2.0   (Limit: 50)")
        print("    Total Theta:  -31.2  (Limit: -500)")
        print("    Total Vega:   +45.7  (Limit: 1000)")

        print("\n  ✓ All Greeks within limits")

    def check_exits(self):
        """Check if any positions should be exited."""
        print("\n" + "-" * 80)
        print("Exit Condition Check")
        print("-" * 80)

        # In production: check each open position
        # for position in self.open_positions:
        #     should_exit, reason = self.strategy.should_exit(position, current_chain)

        print("\nChecking Position 1:")
        print("  Profit Target (50% credit): Not reached (11.2% < 50%)")
        print("  Loss Limit (200% credit): Not breached")
        print("  DTE < 7: No (45 days remaining)")
        print("  Delta Breach: No (Delta = +2.4)")
        print("  ✓ Hold position")

        print("\nChecking Position 2:")
        print("  Profit Target (50% credit): Not reached (15.6% < 50%)")
        print("  Loss Limit (200% credit): Not breached")
        print("  DTE < 7: No (37 days remaining)")
        print("  Delta Breach: No (Delta = -1.8)")
        print("  ✓ Hold position")

        print("\n✓ No exit signals generated")

    def check_adjustments(self):
        """Check if any positions need adjustment."""
        print("\n" + "-" * 80)
        print("Adjustment Analysis")
        print("-" * 80)

        # In production: check each open position
        # for position in self.open_positions:
        #     adjustment = self.strategy.should_adjust(position, current_chain)

        print("\nChecking Position 1:")
        print("  Underlying Price: $450.25")
        print("  Short Call Strike: $465.00 (OTM by $14.75)")
        print("  Short Put Strike: $435.00 (OTM by $15.25)")
        print("  Position Delta: +2.4 (within tolerance)")
        print("  ✓ No adjustment needed")

        print("\nChecking Position 2:")
        print("  Underlying Price: $450.25")
        print("  Short Call Strike: $468.00 (OTM by $17.75)")
        print("  Short Put Strike: $432.00 (OTM by $18.25)")
        print("  Position Delta: -1.8 (within tolerance)")
        print("  ✓ No adjustment needed")

        print("\n✓ All positions healthy - no adjustments required")

    def generate_alerts(self):
        """Generate alerts for monitoring."""
        print("\n" + "-" * 80)
        print("Alert Generation")
        print("-" * 80)

        # In production: exposure_monitor.check_exposures()
        alerts = []

        print("\nExposure Monitoring:")
        print("  Portfolio Delta: +0.6 (0.6% of limit) ✓")
        print("  Portfolio Gamma: +2.0 (4.0% of limit) ✓")
        print("  Portfolio Theta: -31.2 (6.2% of limit) ✓")
        print("  Portfolio Vega: +45.7 (4.6% of limit) ✓")
        print("  Portfolio Heat: 2.5% (max 30%) ✓")

        print("\n✓ No alerts generated - all exposures normal")

        return alerts

    def end_of_day_summary(self):
        """Print end-of-day summary."""
        print("\n" + "=" * 80)
        print("  END OF DAY SUMMARY")
        print("=" * 80)

        print("\nTrading Activity:")
        print("  New Positions Opened: 1")
        print("  Positions Closed: 0")
        print("  Positions Adjusted: 0")
        print("  Total Open Positions: 2")

        print("\nPerformance:")
        print("  Today's P&L: +$25.00")
        print("  Open P&L: +$170.00")
        print("  Account Value: $100,195.00")
        print("  Daily Return: +0.02%")

        print("\nRisk Metrics:")
        print("  Portfolio Heat: 2.5%")
        print("  Max Delta: +2.4")
        print("  Daily Theta Collection: $31.20")

        print("\nSharpe Filter Status:")
        print("  Current Sharpe: 1.45")
        print("  Days Since Filter Block: 12 days")
        print("  Status: ✓ Active Trading")

        print("\n" + "=" * 80)


def run_live_simulation():
    """Run a complete live trading simulation."""
    simulation = LiveTradingSimulation()

    try:
        # Initialize
        simulation.initialize_system()

        # Wait for market open simulation
        print("\n" + "=" * 80)
        print("  Waiting for Market Open (9:30 AM ET)")
        print("=" * 80)
        print("\n  Current Time: 9:30 AM - Market is OPEN")

        # Main trading loop
        print("\n" + "=" * 80)
        print("  LIVE TRADING SESSION STARTED")
        print("=" * 80)

        # Step 1: Fetch market data
        market_data = simulation.fetch_market_data()

        # Step 2: Analyze opportunity
        signal = simulation.analyze_opportunity(market_data)

        if signal:
            # Step 3: Size position
            quantity = simulation.size_position(signal)

            # Step 4: Execute trade
            if quantity > 0:
                position_id = simulation.execute_trade(signal, quantity)

        # Step 5: Monitor positions
        simulation.monitor_positions()

        # Step 6: Check exits
        simulation.check_exits()

        # Step 7: Check adjustments
        simulation.check_adjustments()

        # Step 8: Generate alerts
        simulation.generate_alerts()

        # End of day
        print("\n\n  Current Time: 4:00 PM - Market is CLOSED")
        simulation.end_of_day_summary()

        print("\n✓ Live trading simulation completed successfully")

    except KeyboardInterrupt:
        print("\n\nSimulation interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_live_simulation()
