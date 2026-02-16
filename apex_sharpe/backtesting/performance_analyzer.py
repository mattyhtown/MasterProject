"""
Performance Analyzer for APEX-SHARPE Backtesting.

Comprehensive performance metrics including options-specific analytics,
Greeks attribution, and risk-adjusted returns.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Dict, Optional, Any
import sys
import os

# Add CrewTrader to path for math utilities
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../CrewTrader'))

from utils.math_utils import (
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_max_drawdown,
    calculate_calmar_ratio,
    calculate_win_rate,
    calculate_profit_factor
)


@dataclass
class TradeStatistics:
    """Statistics for individual trades."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    avg_win: Decimal
    avg_loss: Decimal
    largest_win: Decimal
    largest_loss: Decimal

    profit_factor: float
    expectancy: Decimal

    avg_holding_days: float
    avg_dte_entry: float
    avg_dte_exit: float


@dataclass
class GreeksAttribution:
    """Greeks-based P&L attribution."""
    total_pnl: Decimal

    # Attribution by Greek
    theta_pnl: Decimal
    delta_pnl: Decimal
    vega_pnl: Decimal
    gamma_pnl: Decimal
    residual_pnl: Decimal

    # As percentages
    theta_pct: float
    delta_pct: float
    vega_pct: float
    gamma_pct: float


@dataclass
class IVRankAnalysis:
    """Performance broken down by IV rank."""
    high_iv_trades: int  # IV Rank > 50
    low_iv_trades: int   # IV Rank <= 50

    high_iv_win_rate: float
    low_iv_win_rate: float

    high_iv_avg_pnl: Decimal
    low_iv_avg_pnl: Decimal

    high_iv_sharpe: float
    low_iv_sharpe: float


@dataclass
class BacktestResults:
    """
    Comprehensive backtest results.

    Contains all performance metrics, trade statistics,
    Greeks attribution, and risk metrics.
    """
    # Configuration
    ticker: str
    start_date: date
    end_date: date
    initial_capital: Decimal
    final_capital: Decimal

    # Core Performance
    total_return: Decimal
    total_return_pct: float
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: Optional[float]

    # Risk Metrics
    max_drawdown: float
    max_drawdown_date: Optional[date]
    volatility: float
    downside_deviation: float

    # Trade Statistics
    trade_stats: TradeStatistics

    # Options-Specific
    greeks_attribution: Optional[GreeksAttribution] = None
    iv_rank_analysis: Optional[IVRankAnalysis] = None

    # Time Series
    equity_curve: List[Decimal] = field(default_factory=list)
    equity_dates: List[date] = field(default_factory=list)
    returns: List[float] = field(default_factory=list)

    # Additional Metrics
    avg_positions: float = 0.0
    max_positions: int = 0
    days_in_market: int = 0

    def summary(self) -> str:
        """Generate a formatted summary report."""
        lines = []
        lines.append("=" * 70)
        lines.append("BACKTEST RESULTS SUMMARY")
        lines.append("=" * 70)

        # Overview
        lines.append(f"\nTicker: {self.ticker}")
        lines.append(f"Period: {self.start_date} to {self.end_date}")
        lines.append(f"Duration: {(self.end_date - self.start_date).days} days")

        # Performance
        lines.append(f"\n{'PERFORMANCE':-<70}")
        lines.append(f"Initial Capital:      ${self.initial_capital:>15,.2f}")
        lines.append(f"Final Capital:        ${self.final_capital:>15,.2f}")
        lines.append(f"Total P&L:            ${self.total_return:>15,.2f}")
        lines.append(f"Total Return:         {self.total_return_pct:>15.2f}%")
        lines.append(f"Annualized Return:    {self.annualized_return:>15.2f}%")

        # Risk-Adjusted
        lines.append(f"\n{'RISK-ADJUSTED RETURNS':-<70}")
        lines.append(f"Sharpe Ratio:         {self.sharpe_ratio:>15.3f}")
        lines.append(f"Sortino Ratio:        {self.sortino_ratio:>15.3f}")
        if self.calmar_ratio:
            lines.append(f"Calmar Ratio:         {self.calmar_ratio:>15.3f}")

        # Risk
        lines.append(f"\n{'RISK METRICS':-<70}")
        lines.append(f"Maximum Drawdown:     {self.max_drawdown:>15.2f}%")
        lines.append(f"Volatility:           {self.volatility:>15.2f}%")
        lines.append(f"Downside Deviation:   {self.downside_deviation:>15.2f}%")

        # Trade Stats
        stats = self.trade_stats
        lines.append(f"\n{'TRADE STATISTICS':-<70}")
        lines.append(f"Total Trades:         {stats.total_trades:>15}")
        lines.append(f"Winning Trades:       {stats.winning_trades:>15}")
        lines.append(f"Losing Trades:        {stats.losing_trades:>15}")
        lines.append(f"Win Rate:             {stats.win_rate:>15.2f}%")
        lines.append(f"Profit Factor:        {stats.profit_factor:>15.3f}")
        lines.append(f"Expectancy:           ${stats.expectancy:>14,.2f}")

        lines.append(f"\nAverage Win:          ${stats.avg_win:>14,.2f}")
        lines.append(f"Average Loss:         ${stats.avg_loss:>14,.2f}")
        lines.append(f"Largest Win:          ${stats.largest_win:>14,.2f}")
        lines.append(f"Largest Loss:         ${stats.largest_loss:>14,.2f}")

        # Options-Specific
        lines.append(f"\n{'OPTIONS METRICS':-<70}")
        lines.append(f"Avg Holding Days:     {stats.avg_holding_days:>15.1f}")
        lines.append(f"Avg DTE at Entry:     {stats.avg_dte_entry:>15.1f}")
        lines.append(f"Avg DTE at Exit:      {stats.avg_dte_exit:>15.1f}")

        # Greeks Attribution
        if self.greeks_attribution:
            ga = self.greeks_attribution
            lines.append(f"\n{'GREEKS ATTRIBUTION':-<70}")
            lines.append(f"Theta P&L:            ${ga.theta_pnl:>14,.2f} ({ga.theta_pct:>6.1f}%)")
            lines.append(f"Delta P&L:            ${ga.delta_pnl:>14,.2f} ({ga.delta_pct:>6.1f}%)")
            lines.append(f"Vega P&L:             ${ga.vega_pnl:>14,.2f} ({ga.vega_pct:>6.1f}%)")
            lines.append(f"Gamma P&L:            ${ga.gamma_pnl:>14,.2f} ({ga.gamma_pct:>6.1f}%)")

        # IV Rank Analysis
        if self.iv_rank_analysis:
            iv = self.iv_rank_analysis
            lines.append(f"\n{'IV RANK ANALYSIS':-<70}")
            lines.append(f"High IV Trades:       {iv.high_iv_trades:>15}")
            lines.append(f"High IV Win Rate:     {iv.high_iv_win_rate:>15.2f}%")
            lines.append(f"High IV Avg P&L:      ${iv.high_iv_avg_pnl:>14,.2f}")
            lines.append(f"High IV Sharpe:       {iv.high_iv_sharpe:>15.3f}")
            lines.append(f"\nLow IV Trades:        {iv.low_iv_trades:>15}")
            lines.append(f"Low IV Win Rate:      {iv.low_iv_win_rate:>15.2f}%")
            lines.append(f"Low IV Avg P&L:       ${iv.low_iv_avg_pnl:>14,.2f}")
            lines.append(f"Low IV Sharpe:        {iv.low_iv_sharpe:>15.3f}")

        lines.append("\n" + "=" * 70)

        return "\n".join(lines)


class PerformanceAnalyzer:
    """
    Analyzer for backtest performance metrics.

    Calculates comprehensive performance statistics including
    options-specific metrics and Greeks attribution.
    """

    def __init__(self, config: 'BacktestConfig'):
        """
        Initialize performance analyzer.

        Args:
            config: Backtest configuration
        """
        self.config = config

    def analyze(
        self,
        closed_positions: List['BacktestPosition'],
        equity_curve: List[Decimal],
        equity_dates: List[date],
        daily_stats: List[Dict[str, Any]]
    ) -> BacktestResults:
        """
        Analyze backtest results.

        Args:
            closed_positions: List of closed positions
            equity_curve: Daily equity values
            equity_dates: Dates for equity curve
            daily_stats: Daily statistics

        Returns:
            BacktestResults with comprehensive metrics
        """
        # Calculate returns
        returns = self._calculate_returns(equity_curve)

        # Core performance
        total_return = equity_curve[-1] - equity_curve[0]
        total_return_pct = float(total_return / equity_curve[0] * 100)

        # Annualize
        days = (self.config.end_date - self.config.start_date).days
        years = days / 365.25
        annualized_return = (float(equity_curve[-1] / equity_curve[0]) ** (1 / years) - 1) * 100

        # Risk-adjusted metrics
        sharpe = calculate_sharpe_ratio(returns, risk_free_rate=0.02, periods_per_year=252)
        sortino = calculate_sortino_ratio(returns, risk_free_rate=0.02, periods_per_year=252)

        # Drawdown
        equity_floats = [float(e) for e in equity_curve]
        max_dd = calculate_max_drawdown(equity_floats)
        max_dd_date = self._find_max_drawdown_date(equity_curve, equity_dates)

        # Calmar
        calmar = calculate_calmar_ratio(annualized_return / 100, max_dd) if max_dd > 0 else None

        # Volatility
        import numpy as np
        volatility = np.std(returns, ddof=1) * np.sqrt(252) * 100 if len(returns) > 1 else 0

        # Downside deviation
        downside_returns = [r for r in returns if r < 0]
        downside_deviation = np.std(downside_returns, ddof=1) * np.sqrt(252) * 100 if len(downside_returns) > 1 else 0

        # Trade statistics
        trade_stats = self._calculate_trade_stats(closed_positions)

        # Greeks attribution
        greeks_attr = None
        if self.config.calculate_attribution:
            greeks_attr = self._calculate_greeks_attribution(closed_positions)

        # IV rank analysis
        iv_analysis = None
        # Would need IV rank data in positions to calculate

        # Position metrics
        avg_positions = np.mean([s['num_positions'] for s in daily_stats]) if daily_stats else 0
        max_positions = max([s['num_positions'] for s in daily_stats]) if daily_stats else 0
        days_in_market = sum(1 for s in daily_stats if s['num_positions'] > 0)

        return BacktestResults(
            ticker=self.config.ticker,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            initial_capital=self.config.initial_capital,
            final_capital=equity_curve[-1],
            total_return=total_return,
            total_return_pct=total_return_pct,
            annualized_return=annualized_return,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown=max_dd * 100,
            max_drawdown_date=max_dd_date,
            volatility=volatility,
            downside_deviation=downside_deviation,
            trade_stats=trade_stats,
            greeks_attribution=greeks_attr,
            iv_rank_analysis=iv_analysis,
            equity_curve=equity_curve,
            equity_dates=equity_dates,
            returns=returns,
            avg_positions=avg_positions,
            max_positions=max_positions,
            days_in_market=days_in_market
        )

    def _calculate_returns(self, equity_curve: List[Decimal]) -> List[float]:
        """Calculate daily returns from equity curve."""
        if len(equity_curve) < 2:
            return []

        returns = []
        for i in range(1, len(equity_curve)):
            if equity_curve[i - 1] > 0:
                ret = float((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])
                returns.append(ret)

        return returns

    def _find_max_drawdown_date(
        self,
        equity_curve: List[Decimal],
        equity_dates: List[date]
    ) -> Optional[date]:
        """Find the date of maximum drawdown."""
        if len(equity_curve) < 2:
            return None

        running_max = equity_curve[0]
        max_dd = Decimal("0")
        max_dd_date = None

        for i, equity in enumerate(equity_curve):
            running_max = max(running_max, equity)
            if running_max > 0:
                dd = (running_max - equity) / running_max
                if dd > max_dd:
                    max_dd = dd
                    max_dd_date = equity_dates[i] if i < len(equity_dates) else None

        return max_dd_date

    def _calculate_trade_stats(
        self,
        closed_positions: List['BacktestPosition']
    ) -> TradeStatistics:
        """Calculate trade statistics."""
        if not closed_positions:
            return TradeStatistics(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                avg_win=Decimal("0"),
                avg_loss=Decimal("0"),
                largest_win=Decimal("0"),
                largest_loss=Decimal("0"),
                profit_factor=0.0,
                expectancy=Decimal("0"),
                avg_holding_days=0.0,
                avg_dte_entry=0.0,
                avg_dte_exit=0.0
            )

        # Extract P&Ls
        pnls = [bt_pos.position.realized_pnl for bt_pos in closed_positions
                if bt_pos.position.realized_pnl is not None]

        if not pnls:
            return TradeStatistics(
                total_trades=len(closed_positions),
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                avg_win=Decimal("0"),
                avg_loss=Decimal("0"),
                largest_win=Decimal("0"),
                largest_loss=Decimal("0"),
                profit_factor=0.0,
                expectancy=Decimal("0"),
                avg_holding_days=0.0,
                avg_dte_entry=0.0,
                avg_dte_exit=0.0
            )

        # Winners and losers
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]

        winning_trades = len(winners)
        losing_trades = len(losers)
        win_rate = calculate_win_rate([float(p) for p in pnls]) * 100

        # Averages
        avg_win = sum(winners) / len(winners) if winners else Decimal("0")
        avg_loss = sum(losers) / len(losers) if losers else Decimal("0")

        # Extremes
        largest_win = max(winners) if winners else Decimal("0")
        largest_loss = min(losers) if losers else Decimal("0")

        # Profit factor
        profit_factor = calculate_profit_factor([float(p) for p in pnls])

        # Expectancy
        expectancy = sum(pnls) / len(pnls)

        # Holding period
        holding_days = []
        dte_entry = []
        dte_exit = []

        for bt_pos in closed_positions:
            pos = bt_pos.position

            # Days in trade
            if pos.exit_time and pos.entry_time:
                days = (pos.exit_time - pos.entry_time).days
                holding_days.append(days)

            # DTE at entry
            if pos.legs:
                first_leg = pos.legs[0]
                dte = (first_leg.contract.expiration - bt_pos.entry_date).days
                dte_entry.append(dte)

                # DTE at exit
                if pos.exit_time:
                    exit_date = pos.exit_time.date()
                    dte_at_exit = (first_leg.contract.expiration - exit_date).days
                    dte_exit.append(max(0, dte_at_exit))

        avg_holding = sum(holding_days) / len(holding_days) if holding_days else 0
        avg_dte_entry_val = sum(dte_entry) / len(dte_entry) if dte_entry else 0
        avg_dte_exit_val = sum(dte_exit) / len(dte_exit) if dte_exit else 0

        return TradeStatistics(
            total_trades=len(closed_positions),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            profit_factor=profit_factor,
            expectancy=expectancy,
            avg_holding_days=avg_holding,
            avg_dte_entry=avg_dte_entry_val,
            avg_dte_exit=avg_dte_exit_val
        )

    def _calculate_greeks_attribution(
        self,
        closed_positions: List['BacktestPosition']
    ) -> GreeksAttribution:
        """Calculate P&L attribution by Greeks."""
        total_theta = Decimal("0")
        total_delta = Decimal("0")
        total_vega = Decimal("0")
        total_gamma = Decimal("0")

        for bt_pos in closed_positions:
            total_theta += bt_pos.theta_pnl
            total_delta += bt_pos.delta_pnl
            total_vega += bt_pos.vega_pnl
            total_gamma += bt_pos.gamma_pnl

        total_pnl = sum(
            bt_pos.position.realized_pnl
            for bt_pos in closed_positions
            if bt_pos.position.realized_pnl is not None
        )

        residual = total_pnl - (total_theta + total_delta + total_vega + total_gamma)

        # Calculate percentages
        if total_pnl != 0:
            theta_pct = float(total_theta / total_pnl * 100)
            delta_pct = float(total_delta / total_pnl * 100)
            vega_pct = float(total_vega / total_pnl * 100)
            gamma_pct = float(total_gamma / total_pnl * 100)
        else:
            theta_pct = delta_pct = vega_pct = gamma_pct = 0.0

        return GreeksAttribution(
            total_pnl=total_pnl,
            theta_pnl=total_theta,
            delta_pnl=total_delta,
            vega_pnl=total_vega,
            gamma_pnl=total_gamma,
            residual_pnl=residual,
            theta_pct=theta_pct,
            delta_pct=delta_pct,
            vega_pct=vega_pct,
            gamma_pct=gamma_pct
        )
