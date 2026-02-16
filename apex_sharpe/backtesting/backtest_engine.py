"""
Event-Driven Backtesting Engine for APEX-SHARPE Trading System.

This module provides the core backtesting infrastructure with event-driven
architecture, position tracking, and strategy integration.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Dict, Optional, Any, Callable
from queue import PriorityQueue
import sys
import os

# Add paths for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    # Try package-relative imports first
    from ..strategies.base_strategy import (
        BaseStrategy,
        MultiLegSpread,
        OptionsChain as StrategyOptionsChain,
        MarketData,
        IVData,
        SignalAction,
        StrategySignal
    )
    from ..data.orats_adapter import OptionsChain, OptionContract
    from ..greeks.greeks_calculator import (
        GreeksCalculator,
        PortfolioGreeksCalculator,
        OptionContract as GreeksOptionContract,
        OptionType as GreeksOptionType
    )
except ImportError:
    # Fall back to absolute imports
    from strategies.base_strategy import (
        BaseStrategy,
        MultiLegSpread,
        OptionsChain as StrategyOptionsChain,
        MarketData,
        IVData,
        SignalAction,
        StrategySignal
    )
    from data.orats_adapter import OptionsChain, OptionContract
    from greeks.greeks_calculator import (
        GreeksCalculator,
        PortfolioGreeksCalculator,
        OptionContract as GreeksOptionContract,
        OptionType as GreeksOptionType
    )


# ============================================================================
# EVENT TYPES
# ============================================================================

class EventType(Enum):
    """Types of events in the backtesting system."""
    MARKET_DATA = "MARKET_DATA"
    EXPIRATION = "EXPIRATION"
    SIGNAL = "SIGNAL"
    EXIT = "EXIT"


class BaseEvent:
    """Base class for all events."""
    def __init__(self, event_type: EventType, timestamp: datetime, priority: int = 0):
        self.event_type = event_type
        self.timestamp = timestamp
        self.priority = priority

    def __lt__(self, other):
        """For priority queue ordering."""
        return (self.priority, self.timestamp) < (other.priority, other.timestamp)


@dataclass
class MarketDataEvent:
    """Market data update event."""
    timestamp: datetime
    trade_date: date
    chain: OptionsChain
    underlying_price: Decimal
    iv_rank: Optional[Decimal] = None
    event_type: EventType = EventType.MARKET_DATA
    priority: int = 1

    def __lt__(self, other):
        """For priority queue ordering."""
        return (self.priority, self.timestamp) < (other.priority, other.timestamp)


@dataclass
class ExpirationEvent:
    """Options expiration event."""
    timestamp: datetime
    expiration_date: date
    positions_expiring: List[str] = field(default_factory=list)  # Position IDs
    event_type: EventType = EventType.EXPIRATION
    priority: int = 0  # Handle expirations first

    def __lt__(self, other):
        """For priority queue ordering."""
        return (self.priority, self.timestamp) < (other.priority, other.timestamp)


@dataclass
class SignalEvent:
    """Trading signal event from strategy."""
    timestamp: datetime
    signal: StrategySignal
    chain: OptionsChain
    event_type: EventType = EventType.SIGNAL
    priority: int = 2

    def __lt__(self, other):
        """For priority queue ordering."""
        return (self.priority, self.timestamp) < (other.priority, other.timestamp)


# ============================================================================
# BACKTEST CONFIGURATION
# ============================================================================

@dataclass
class BacktestConfig:
    """Configuration for backtest execution."""
    start_date: date
    end_date: date
    initial_capital: Decimal
    ticker: str = "SPY"

    # Costs and slippage
    commission_per_contract: Decimal = Decimal("0.65")
    slippage_pct: Decimal = Decimal("0.0005")  # 5 basis points

    # Risk parameters
    max_positions: int = 5
    max_capital_per_trade: Decimal = Decimal("0.20")  # 20% max per trade

    # Greeks calculation
    risk_free_rate: float = 0.045
    dividend_yield: float = 0.018

    # Performance tracking
    track_greeks_daily: bool = True
    calculate_attribution: bool = True


# ============================================================================
# POSITION TRACKING
# ============================================================================

@dataclass
class BacktestPosition:
    """Enhanced position tracking for backtesting."""
    position: MultiLegSpread
    entry_date: date
    entry_greeks_snapshot: Optional[Dict[str, Decimal]] = None

    # Daily tracking
    daily_values: List[Decimal] = field(default_factory=list)
    daily_greeks: List[Dict[str, Decimal]] = field(default_factory=list)
    daily_dates: List[date] = field(default_factory=list)

    # Attribution tracking
    theta_pnl: Decimal = Decimal("0")
    delta_pnl: Decimal = Decimal("0")
    vega_pnl: Decimal = Decimal("0")
    gamma_pnl: Decimal = Decimal("0")


# ============================================================================
# BACKTEST ENGINE
# ============================================================================

class BacktestEngine:
    """
    Event-driven backtesting engine for options strategies.

    Features:
    - Event queue processing (market data, expirations, signals)
    - Position lifecycle management
    - Greeks calculation and tracking
    - Commission and slippage modeling
    - Daily performance tracking

    Example:
        >>> from apex_sharpe.strategies import IronCondorStrategy
        >>> from apex_sharpe.data import ORATSAdapter
        >>>
        >>> config = BacktestConfig(
        ...     start_date=date(2023, 1, 1),
        ...     end_date=date(2023, 12, 31),
        ...     initial_capital=Decimal("100000")
        ... )
        >>>
        >>> strategy = IronCondorStrategy(...)
        >>> engine = BacktestEngine(config, strategy, data_manager)
        >>> results = engine.run()
    """

    def __init__(
        self,
        config: BacktestConfig,
        strategy: BaseStrategy,
        data_manager: Any,  # HistoricalDataManager
    ):
        """
        Initialize backtesting engine.

        Args:
            config: Backtest configuration
            strategy: Trading strategy instance
            data_manager: Historical data manager
        """
        self.config = config
        self.strategy = strategy
        self.data_manager = data_manager

        # Event queue
        self.event_queue: PriorityQueue = PriorityQueue()

        # Position tracking
        self.open_positions: Dict[str, BacktestPosition] = {}
        self.closed_positions: List[BacktestPosition] = []
        self.position_counter = 0

        # Capital tracking
        self.current_capital = config.initial_capital
        self.equity_curve: List[Decimal] = [config.initial_capital]
        self.equity_dates: List[date] = []

        # Greeks calculators
        self.greeks_calc = GreeksCalculator(
            risk_free_rate=config.risk_free_rate,
            dividend_yield=config.dividend_yield
        )
        self.portfolio_greeks_calc = PortfolioGreeksCalculator(self.greeks_calc)

        # Performance tracking
        self.daily_stats: List[Dict[str, Any]] = []
        self.current_date: Optional[date] = None

    def run(self) -> 'BacktestResults':
        """
        Run the backtest.

        Returns:
            BacktestResults with comprehensive performance metrics
        """
        print(f"\n{'='*70}")
        print(f"Starting Backtest: {self.config.ticker}")
        print(f"Period: {self.config.start_date} to {self.config.end_date}")
        print(f"Initial Capital: ${self.config.initial_capital:,.2f}")
        print(f"{'='*70}\n")

        # Load historical data
        print("Loading historical data...")
        historical_chains = self.data_manager.get_date_range(
            self.config.ticker,
            self.config.start_date,
            self.config.end_date
        )

        if not historical_chains:
            raise ValueError(f"No historical data available for {self.config.ticker}")

        print(f"Loaded {len(historical_chains)} trading days\n")

        # Generate events from historical data
        self._generate_events(historical_chains)

        # Process event queue
        events_processed = 0
        while not self.event_queue.empty():
            event = self.event_queue.get()
            self._process_event(event)
            events_processed += 1

            if events_processed % 100 == 0:
                print(f"Processed {events_processed} events...")

        # Close any remaining positions
        self._close_all_positions()

        print(f"\nBacktest complete! Processed {events_processed} events")
        print(f"Final Capital: ${self.current_capital:,.2f}")
        print(f"Total Trades: {len(self.closed_positions)}")

        # Generate results
        from .performance_analyzer import PerformanceAnalyzer
        analyzer = PerformanceAnalyzer(self.config)
        results = analyzer.analyze(
            closed_positions=self.closed_positions,
            equity_curve=self.equity_curve,
            equity_dates=self.equity_dates,
            daily_stats=self.daily_stats
        )

        return results

    def _generate_events(self, historical_chains: Dict[date, OptionsChain]) -> None:
        """Generate events from historical data."""
        sorted_dates = sorted(historical_chains.keys())

        for trade_date in sorted_dates:
            chain = historical_chains[trade_date]

            # Market data event
            event = MarketDataEvent(
                timestamp=datetime.combine(trade_date, datetime.min.time()),
                trade_date=trade_date,
                chain=chain,
                underlying_price=Decimal(str(chain.underlying_price))
            )
            self.event_queue.put(event)

            # Check for expirations
            expiring_positions = [
                pos_id for pos_id, bt_pos in self.open_positions.items()
                if any(leg.contract.expiration_date == trade_date
                       for leg in bt_pos.position.legs)
            ]

            if expiring_positions:
                exp_event = ExpirationEvent(
                    timestamp=datetime.combine(trade_date, datetime.max.time()),
                    expiration_date=trade_date,
                    positions_expiring=expiring_positions
                )
                self.event_queue.put(exp_event)

    def _process_event(self, event: BaseEvent) -> None:
        """Process a single event."""
        if event.event_type == EventType.MARKET_DATA:
            self._handle_market_data(event)
        elif event.event_type == EventType.EXPIRATION:
            self._handle_expiration(event)
        elif event.event_type == EventType.SIGNAL:
            self._handle_signal(event)

    def _handle_market_data(self, event: MarketDataEvent) -> None:
        """Handle market data event."""
        self.current_date = event.trade_date
        self.greeks_calc.valuation_date = event.trade_date

        # Update existing positions
        self._update_positions(event)

        # Check exit conditions for open positions
        self._check_exits(event)

        # Generate new signals
        self._generate_signals(event)

        # Track daily performance
        self._record_daily_stats(event)

    def _handle_expiration(self, event: ExpirationEvent) -> None:
        """Handle expiration event."""
        for pos_id in event.positions_expiring:
            if pos_id in self.open_positions:
                bt_pos = self.open_positions[pos_id]
                self._close_position(bt_pos, "EXPIRATION", event.expiration_date)

    def _handle_signal(self, event: SignalEvent) -> None:
        """Handle trading signal event."""
        signal = event.signal

        if signal.action != SignalAction.ENTER:
            return

        # Check if we can trade (Sharpe filtering)
        if not self.strategy.can_trade():
            return

        # Check position limits
        if len(self.open_positions) >= self.config.max_positions:
            return

        # Select strikes and build position
        legs = self.strategy.select_strikes(
            self._convert_chain_to_strategy_format(event.chain),
            signal
        )

        if not legs:
            return

        # Create position
        self._open_position(legs, event.chain, signal)

    def _update_positions(self, event: MarketDataEvent) -> None:
        """Update all open positions with current market data."""
        for bt_pos in self.open_positions.values():
            # Update contract prices from current chain
            self._update_position_prices(bt_pos, event.chain)

            # Calculate current value
            bt_pos.position.calculate_current_value()
            bt_pos.position.calculate_portfolio_greeks()

            # Track daily values
            if self.config.track_greeks_daily:
                bt_pos.daily_dates.append(event.trade_date)
                bt_pos.daily_values.append(bt_pos.position.current_value or Decimal("0"))
                bt_pos.daily_greeks.append({
                    'delta': bt_pos.position.portfolio_delta or Decimal("0"),
                    'theta': bt_pos.position.portfolio_theta or Decimal("0"),
                    'vega': bt_pos.position.portfolio_vega or Decimal("0"),
                    'gamma': bt_pos.position.portfolio_gamma or Decimal("0"),
                })

    def _update_position_prices(
        self,
        bt_pos: BacktestPosition,
        current_chain: OptionsChain
    ) -> None:
        """Update position leg prices from current chain."""
        for leg in bt_pos.position.legs:
            # Find matching contract in current chain
            matching = self._find_matching_contract(leg.contract, current_chain)

            if matching:
                # Update prices with slippage
                leg.contract.bid = matching.bid * (1 - float(self.config.slippage_pct))
                leg.contract.ask = matching.ask * (1 + float(self.config.slippage_pct))
                leg.contract.last = matching.mid

                # Update Greeks
                leg.contract.delta = matching.delta
                leg.contract.gamma = matching.gamma
                leg.contract.theta = matching.theta
                leg.contract.vega = matching.vega

    def _find_matching_contract(
        self,
        target: Any,
        chain: OptionsChain
    ) -> Optional[OptionContract]:
        """Find matching contract in chain."""
        from apex_sharpe.strategies.base_strategy import OptionType as StrategyOptionType

        # Determine which list to search
        contracts = chain.calls if target.option_type == StrategyOptionType.CALL else chain.puts

        # Find by strike and expiration
        for contract in contracts:
            if (abs(contract.strike - float(target.strike)) < 0.01 and
                contract.expiration_date == target.expiration):
                return contract

        return None

    def _check_exits(self, event: MarketDataEvent) -> None:
        """Check exit conditions for all open positions."""
        positions_to_close = []

        for pos_id, bt_pos in self.open_positions.items():
            # Convert chain to strategy format
            strategy_chain = self._convert_chain_to_strategy_format(event.chain)

            # Check strategy exit conditions
            should_exit, exit_reason = self.strategy.should_exit(
                bt_pos.position,
                strategy_chain
            )

            if should_exit:
                positions_to_close.append((pos_id, exit_reason))

        # Close positions
        for pos_id, exit_reason in positions_to_close:
            bt_pos = self.open_positions[pos_id]
            self._close_position(bt_pos, exit_reason, event.trade_date)

    def _generate_signals(self, event: MarketDataEvent) -> None:
        """Generate trading signals from strategy."""
        # Convert to strategy format
        strategy_chain = self._convert_chain_to_strategy_format(event.chain)

        # Create IVData
        iv_data = IVData(
            symbol=self.config.ticker,
            timestamp=event.timestamp,
            current_iv=Decimal(str(event.chain.calls[0].implied_volatility)) if event.chain.calls else Decimal("0.20"),
            iv_rank=event.iv_rank or Decimal("50"),
            iv_percentile=Decimal("50")
        )

        # Create MarketData
        market_data = MarketData(
            symbol=self.config.ticker,
            timestamp=event.timestamp,
            price=event.underlying_price,
            bid=event.underlying_price,
            ask=event.underlying_price,
            volume=0
        )

        # Analyze
        signal = self.strategy.analyze(strategy_chain, iv_data, market_data)

        # Create signal event if actionable
        if signal.action == SignalAction.ENTER:
            signal_event = SignalEvent(
                timestamp=event.timestamp,
                signal=signal,
                chain=event.chain
            )
            self.event_queue.put(signal_event)

    def _open_position(
        self,
        legs: List[Any],
        chain: OptionsChain,
        signal: StrategySignal
    ) -> None:
        """Open a new position."""
        from apex_sharpe.strategies.base_strategy import MultiLegSpread

        # Create position
        position = MultiLegSpread(
            legs=legs,
            spread_type=signal.spread_type,
            entry_time=datetime.combine(self.current_date, datetime.min.time()),
            underlying_price=Decimal(str(chain.underlying_price)),
            position_id=f"POS_{self.position_counter:04d}"
        )

        self.position_counter += 1

        # Calculate entry premium (with slippage and commissions)
        entry_premium = self._calculate_entry_premium(position)
        position.entry_premium = entry_premium

        # Calculate portfolio Greeks
        position.calculate_portfolio_greeks()

        # Create backtest position
        bt_pos = BacktestPosition(
            position=position,
            entry_date=self.current_date,
            entry_greeks_snapshot={
                'delta': position.portfolio_delta or Decimal("0"),
                'theta': position.portfolio_theta or Decimal("0"),
                'vega': position.portfolio_vega or Decimal("0"),
                'gamma': position.portfolio_gamma or Decimal("0"),
            }
        )

        # Add to tracking
        self.open_positions[position.position_id] = bt_pos

        # Update strategy tracking
        self.strategy.open_positions.append(position)

    def _close_position(
        self,
        bt_pos: BacktestPosition,
        exit_reason: str,
        exit_date: date
    ) -> None:
        """Close a position."""
        position = bt_pos.position

        # Calculate exit value
        exit_value = position.calculate_current_value()

        # Apply commissions
        commission = self._calculate_commission(position)
        exit_value -= commission

        # Calculate P&L
        pnl = position.entry_premium - exit_value

        # Update position
        position.exit_time = datetime.combine(exit_date, datetime.max.time())
        position.exit_premium = exit_value
        position.exit_reason = exit_reason
        position.realized_pnl = pnl

        # Update capital
        self.current_capital += pnl

        # Record in strategy
        self.strategy.record_trade(position, exit_value)

        # Move to closed
        self.closed_positions.append(bt_pos)
        del self.open_positions[position.position_id]

    def _close_all_positions(self) -> None:
        """Close all remaining open positions."""
        if not self.open_positions:
            return

        print(f"\nClosing {len(self.open_positions)} remaining positions...")

        for bt_pos in list(self.open_positions.values()):
            self._close_position(bt_pos, "END_OF_BACKTEST", self.current_date)

    def _calculate_entry_premium(self, position: MultiLegSpread) -> Decimal:
        """Calculate entry premium including slippage and commissions."""
        total = Decimal("0")

        for leg in position.legs:
            # Use ask for buys, bid for sells
            if leg.is_long:
                price = leg.contract.ask
            else:
                price = leg.contract.bid

            leg_value = price * Decimal(str(leg.quantity))

            # Add/subtract based on direction
            if leg.is_short:
                total += leg_value
            else:
                total -= leg_value

        # Subtract commission
        commission = self._calculate_commission(position)
        total -= commission

        return total

    def _calculate_commission(self, position: MultiLegSpread) -> Decimal:
        """Calculate total commission for position."""
        total_contracts = sum(leg.quantity for leg in position.legs)
        return self.config.commission_per_contract * Decimal(str(total_contracts))

    def _record_daily_stats(self, event: MarketDataEvent) -> None:
        """Record daily performance statistics."""
        # Calculate total position value
        total_position_value = Decimal("0")
        for bt_pos in self.open_positions.values():
            total_position_value += bt_pos.position.current_value or Decimal("0")

        # Calculate equity
        equity = self.current_capital + total_position_value

        # Record
        self.equity_curve.append(equity)
        self.equity_dates.append(event.trade_date)

        stats = {
            'date': event.trade_date,
            'equity': equity,
            'cash': self.current_capital,
            'position_value': total_position_value,
            'num_positions': len(self.open_positions),
            'underlying_price': event.underlying_price,
        }

        self.daily_stats.append(stats)

    def _convert_chain_to_strategy_format(self, chain: OptionsChain) -> StrategyOptionsChain:
        """Convert ORATS chain to strategy chain format."""
        from strategies.base_strategy import (
            OptionsChain as StrategyChain,
            OptionContract as StrategyContract,
            OptionType as StrategyOptionType
        )

        strategy_chain = StrategyChain(
            symbol=chain.ticker,
            timestamp=datetime.combine(chain.trade_date or date.today(), datetime.min.time()),
            underlying_price=Decimal(str(chain.underlying_price)),
            expirations=[chain.expiration_date]
        )

        # Convert calls
        strategy_chain.calls[chain.expiration_date] = [
            StrategyContract(
                symbol=f"{chain.ticker}_{c.strike}C",
                strike=Decimal(str(c.strike)),
                expiration=c.expiration_date,
                option_type=StrategyOptionType.CALL,
                bid=Decimal(str(c.bid)),
                ask=Decimal(str(c.ask)),
                last=Decimal(str(c.mid)),
                volume=c.volume,
                open_interest=c.open_interest,
                implied_volatility=Decimal(str(c.implied_volatility)),
                delta=Decimal(str(c.delta)),
                gamma=Decimal(str(c.gamma)),
                theta=Decimal(str(c.theta)),
                vega=Decimal(str(c.vega)),
                dte=(c.expiration_date - chain.trade_date).days if chain.trade_date else 30
            )
            for c in chain.calls
        ]

        # Convert puts
        strategy_chain.puts[chain.expiration_date] = [
            StrategyContract(
                symbol=f"{chain.ticker}_{p.strike}P",
                strike=Decimal(str(p.strike)),
                expiration=p.expiration_date,
                option_type=StrategyOptionType.PUT,
                bid=Decimal(str(p.bid)),
                ask=Decimal(str(p.ask)),
                last=Decimal(str(p.mid)),
                volume=p.volume,
                open_interest=p.open_interest,
                implied_volatility=Decimal(str(p.implied_volatility)),
                delta=Decimal(str(p.delta)),
                gamma=Decimal(str(p.gamma)),
                theta=Decimal(str(p.theta)),
                vega=Decimal(str(p.vega)),
                dte=(p.expiration_date - chain.trade_date).days if chain.trade_date else 30
            )
            for p in chain.puts
        ]

        return strategy_chain
