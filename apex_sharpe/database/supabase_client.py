"""
Supabase client for APEX-SHARPE Trading System.
Handles all database interactions for positions, trades, and performance tracking.
"""

from typing import List, Dict, Optional, Any
from datetime import date, datetime
from decimal import Decimal
import os
from dataclasses import dataclass, asdict
from supabase import create_client, Client


@dataclass
class Position:
    """Represents an options position in the database."""
    symbol: str
    position_type: str
    entry_date: date
    entry_time: datetime
    entry_premium: Decimal
    entry_iv_rank: Optional[Decimal] = None
    entry_dte: Optional[int] = None
    status: str = 'OPEN'
    strategy_id: Optional[str] = None
    id: Optional[str] = None


@dataclass
class PositionLeg:
    """Represents a single leg of an options position."""
    position_id: str
    leg_index: int
    option_type: str  # 'CALL' or 'PUT'
    strike: Decimal
    expiration_date: date
    quantity: int
    action: str  # 'BTO', 'STO', 'BTC', 'STC'
    entry_price: Decimal
    entry_fill_time: datetime
    entry_delta: Optional[Decimal] = None
    entry_gamma: Optional[Decimal] = None
    entry_theta: Optional[Decimal] = None
    entry_vega: Optional[Decimal] = None
    entry_iv: Optional[Decimal] = None
    commission: Optional[Decimal] = None


@dataclass
class GreeksSnapshot:
    """Snapshot of portfolio Greeks at a point in time."""
    position_id: str
    trade_date: date
    dte: int
    underlying_price: Decimal
    portfolio_delta: Decimal
    portfolio_gamma: Decimal
    portfolio_theta: Decimal
    portfolio_vega: Decimal
    position_value: Decimal
    unrealized_pnl: Decimal


class SupabaseClient:
    """Client for interacting with Supabase database."""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        """
        Initialize Supabase client.

        Args:
            url: Supabase project URL (or from SUPABASE_URL env var)
            key: Supabase API key (or from SUPABASE_KEY env var)
        """
        self.url = url or os.getenv('SUPABASE_URL')
        self.key = key or os.getenv('SUPABASE_KEY')

        if not self.url or not self.key:
            raise ValueError(
                "Supabase URL and KEY required. "
                "Set SUPABASE_URL and SUPABASE_KEY environment variables."
            )

        self.client: Client = create_client(self.url, self.key)

    # ========================================================================
    # STRATEGIES
    # ========================================================================

    def create_strategy(
        self,
        name: str,
        strategy_type: str,
        description: str,
        parameters: Dict[str, Any]
    ) -> Dict:
        """Create a new trading strategy."""
        data = {
            'name': name,
            'strategy_type': strategy_type,
            'description': description,
            'parameters': parameters
        }

        response = self.client.table('strategies').insert(data).execute()
        return response.data[0] if response.data else None

    def get_strategy(self, strategy_id: str) -> Optional[Dict]:
        """Get strategy by ID."""
        response = self.client.table('strategies').select('*').eq('id', strategy_id).execute()
        return response.data[0] if response.data else None

    def get_active_strategies(self) -> List[Dict]:
        """Get all active strategies."""
        response = self.client.table('strategies').select('*').eq('is_active', True).execute()
        return response.data

    # ========================================================================
    # POSITIONS
    # ========================================================================

    def create_position(self, position: Position) -> Dict:
        """Create a new position."""
        data = {k: v for k, v in asdict(position).items() if v is not None and k != 'id'}

        # Convert date/datetime objects to ISO format strings
        if 'entry_date' in data:
            data['entry_date'] = data['entry_date'].isoformat()
        if 'entry_time' in data:
            data['entry_time'] = data['entry_time'].isoformat()

        response = self.client.table('positions').insert(data).execute()
        return response.data[0] if response.data else None

    def update_position(
        self,
        position_id: str,
        updates: Dict[str, Any]
    ) -> Dict:
        """Update a position."""
        # Convert date/datetime objects to ISO format strings
        if 'exit_date' in updates and updates['exit_date']:
            updates['exit_date'] = updates['exit_date'].isoformat()
        if 'exit_time' in updates and updates['exit_time']:
            updates['exit_time'] = updates['exit_time'].isoformat()

        response = self.client.table('positions').update(updates).eq('id', position_id).execute()
        return response.data[0] if response.data else None

    def close_position(
        self,
        position_id: str,
        exit_reason: str,
        realized_pnl: Decimal,
        exit_dte: int
    ) -> Dict:
        """Close a position."""
        updates = {
            'status': 'CLOSED',
            'exit_date': date.today().isoformat(),
            'exit_time': datetime.now().isoformat(),
            'exit_reason': exit_reason,
            'realized_pnl': float(realized_pnl),
            'exit_dte': exit_dte
        }
        return self.update_position(position_id, updates)

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open positions, optionally filtered by symbol."""
        query = self.client.table('positions').select('*').eq('status', 'OPEN')

        if symbol:
            query = query.eq('symbol', symbol)

        response = query.execute()
        return response.data

    def get_position_by_id(self, position_id: str) -> Optional[Dict]:
        """Get position by ID."""
        response = self.client.table('positions').select('*').eq('id', position_id).execute()
        return response.data[0] if response.data else None

    # ========================================================================
    # POSITION LEGS
    # ========================================================================

    def add_position_leg(self, leg: PositionLeg) -> Dict:
        """Add a leg to a position."""
        data = asdict(leg)

        # Convert date/datetime to ISO format
        if 'expiration_date' in data:
            data['expiration_date'] = data['expiration_date'].isoformat()
        if 'entry_fill_time' in data:
            data['entry_fill_time'] = data['entry_fill_time'].isoformat()

        # Convert Decimals to floats
        for key in ['strike', 'entry_price', 'entry_delta', 'entry_gamma',
                    'entry_theta', 'entry_vega', 'entry_iv', 'commission']:
            if key in data and data[key] is not None:
                data[key] = float(data[key])

        response = self.client.table('position_legs').insert(data).execute()
        return response.data[0] if response.data else None

    def get_position_legs(self, position_id: str) -> List[Dict]:
        """Get all legs for a position."""
        response = (
            self.client.table('position_legs')
            .select('*')
            .eq('position_id', position_id)
            .order('leg_index')
            .execute()
        )
        return response.data

    def update_leg_exit(
        self,
        leg_id: str,
        exit_price: Decimal,
        exit_fill_time: datetime
    ) -> Dict:
        """Update leg with exit information."""
        updates = {
            'exit_price': float(exit_price),
            'exit_fill_time': exit_fill_time.isoformat()
        }
        response = self.client.table('position_legs').update(updates).eq('id', leg_id).execute()
        return response.data[0] if response.data else None

    # ========================================================================
    # GREEKS HISTORY
    # ========================================================================

    def record_greeks_snapshot(self, snapshot: GreeksSnapshot) -> Dict:
        """Record a Greeks snapshot for a position."""
        data = asdict(snapshot)

        # Convert date to ISO format
        data['trade_date'] = data['trade_date'].isoformat()

        # Convert Decimals to floats
        for key in ['underlying_price', 'portfolio_delta', 'portfolio_gamma',
                    'portfolio_theta', 'portfolio_vega', 'position_value', 'unrealized_pnl']:
            if key in data and data[key] is not None:
                data[key] = float(data[key])

        response = self.client.table('greeks_history').insert(data).execute()
        return response.data[0] if response.data else None

    def get_greeks_history(
        self,
        position_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict]:
        """Get Greeks history for a position."""
        query = self.client.table('greeks_history').select('*').eq('position_id', position_id)

        if start_date:
            query = query.gte('trade_date', start_date.isoformat())
        if end_date:
            query = query.lte('trade_date', end_date.isoformat())

        response = query.order('trade_date').execute()
        return response.data

    # ========================================================================
    # PERFORMANCE METRICS
    # ========================================================================

    def record_performance_metrics(
        self,
        date: date,
        period_type: str,
        starting_capital: Decimal,
        ending_capital: Decimal,
        metrics: Dict[str, Any],
        strategy_id: Optional[str] = None
    ) -> Dict:
        """Record performance metrics for a period."""
        data = {
            'date': date.isoformat(),
            'period_type': period_type,
            'starting_capital': float(starting_capital),
            'ending_capital': float(ending_capital),
            'total_pnl': float(ending_capital - starting_capital),
            **{k: float(v) if isinstance(v, Decimal) else v for k, v in metrics.items()}
        }

        if strategy_id:
            data['strategy_id'] = strategy_id

        response = self.client.table('performance_metrics').insert(data).execute()
        return response.data[0] if response.data else None

    def get_performance_metrics(
        self,
        start_date: date,
        end_date: date,
        period_type: str = 'DAILY',
        strategy_id: Optional[str] = None
    ) -> List[Dict]:
        """Get performance metrics for a date range."""
        query = (
            self.client.table('performance_metrics')
            .select('*')
            .eq('period_type', period_type)
            .gte('date', start_date.isoformat())
            .lte('date', end_date.isoformat())
        )

        if strategy_id:
            query = query.eq('strategy_id', strategy_id)

        response = query.order('date').execute()
        return response.data

    # ========================================================================
    # BACKTEST RUNS
    # ========================================================================

    def create_backtest_run(
        self,
        run_name: str,
        strategy_id: str,
        start_date: date,
        end_date: date,
        initial_capital: Decimal,
        strategy_parameters: Dict[str, Any],
        results: Dict[str, Any]
    ) -> Dict:
        """Record a backtest run and its results."""
        data = {
            'run_name': run_name,
            'strategy_id': strategy_id,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'initial_capital': float(initial_capital),
            'strategy_parameters': strategy_parameters,
            **{k: float(v) if isinstance(v, (Decimal, int)) else v
               for k, v in results.items()}
        }

        response = self.client.table('backtest_runs').insert(data).execute()
        return response.data[0] if response.data else None

    def get_backtest_runs(
        self,
        strategy_id: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict]:
        """Get recent backtest runs."""
        query = self.client.table('backtest_runs').select('*')

        if strategy_id:
            query = query.eq('strategy_id', strategy_id)

        response = query.order('run_at', desc=True).limit(limit).execute()
        return response.data

    # ========================================================================
    # IV RANK HISTORY
    # ========================================================================

    def record_iv_rank(
        self,
        symbol: str,
        trade_date: date,
        current_iv: Decimal,
        iv_rank: Decimal,
        underlying_price: Decimal,
        **kwargs
    ) -> Dict:
        """Record IV rank data for a symbol."""
        data = {
            'symbol': symbol,
            'trade_date': trade_date.isoformat(),
            'current_iv': float(current_iv),
            'iv_rank': float(iv_rank),
            'underlying_price': float(underlying_price),
            **{k: float(v) if isinstance(v, Decimal) else v for k, v in kwargs.items()}
        }

        # Upsert to handle duplicate dates
        response = self.client.table('iv_rank_history').upsert(data).execute()
        return response.data[0] if response.data else None

    def get_iv_rank_history(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict]:
        """Get IV rank history for a symbol."""
        query = self.client.table('iv_rank_history').select('*').eq('symbol', symbol)

        if start_date:
            query = query.gte('trade_date', start_date.isoformat())
        if end_date:
            query = query.lte('trade_date', end_date.isoformat())

        response = query.order('trade_date').execute()
        return response.data

    # ========================================================================
    # VIEWS AND AGGREGATIONS
    # ========================================================================

    def get_open_positions_summary(self) -> List[Dict]:
        """Get summary of all open positions."""
        response = self.client.table('open_positions_summary').select('*').execute()
        return response.data

    def get_daily_performance(
        self,
        start_date: date,
        end_date: date
    ) -> List[Dict]:
        """Get daily performance summary."""
        response = (
            self.client.table('daily_performance')
            .select('*')
            .gte('date', start_date.isoformat())
            .lte('date', end_date.isoformat())
            .execute()
        )
        return response.data

    def get_strategy_performance_comparison(self) -> List[Dict]:
        """Get performance comparison across all strategies."""
        response = self.client.table('strategy_performance_comparison').select('*').execute()
        return response.data
