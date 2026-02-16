"""ORATS Data Adapter for APEX-SHARPE Trading System.

Provides a clean interface to ORATS options data via MCP tools with caching support.
This adapter wraps the mcp__orats__* functions and transforms responses into
structured dataclasses for easy consumption by trading strategies.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
from enum import Enum
from functools import wraps
import time


class OptionType(Enum):
    """Option type enumeration."""
    CALL = "call"
    PUT = "put"


@dataclass
class OptionContract:
    """Represents a single option contract.

    Reuses the structure from CrewTrader for compatibility.
    """
    ticker: str
    expiration_date: date
    strike: float
    option_type: OptionType
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    underlying_price: float = 0.0
    trade_date: Optional[date] = None

    @property
    def days_to_expiration(self) -> int:
        """Calculate days to expiration."""
        today = date.today()
        return (self.expiration_date - today).days

    @property
    def is_itm(self) -> bool:
        """Check if option is in-the-money."""
        if self.option_type == OptionType.CALL:
            return self.underlying_price > self.strike
        return self.underlying_price < self.strike

    @property
    def is_otm(self) -> bool:
        """Check if option is out-of-the-money."""
        return not self.is_itm

    @property
    def intrinsic_value(self) -> float:
        """Calculate intrinsic value."""
        if self.option_type == OptionType.CALL:
            return max(0, self.underlying_price - self.strike)
        return max(0, self.strike - self.underlying_price)

    @property
    def extrinsic_value(self) -> float:
        """Calculate extrinsic (time) value."""
        return max(0, self.mid - self.intrinsic_value)


@dataclass
class OptionsChain:
    """Represents an options chain for a specific expiration.

    Reuses the structure from CrewTrader for compatibility.
    """
    ticker: str
    expiration_date: date
    underlying_price: float
    calls: List[OptionContract] = field(default_factory=list)
    puts: List[OptionContract] = field(default_factory=list)
    trade_date: Optional[date] = None

    @property
    def total_call_volume(self) -> int:
        """Calculate total call volume."""
        return sum(c.volume for c in self.calls)

    @property
    def total_put_volume(self) -> int:
        """Calculate total put volume."""
        return sum(p.volume for p in self.puts)

    @property
    def put_call_ratio(self) -> float:
        """Calculate put/call volume ratio."""
        call_vol = self.total_call_volume
        if call_vol == 0:
            return 0.0
        return self.total_put_volume / call_vol

    def get_atm_strike(self) -> float:
        """Get the at-the-money strike price."""
        if not self.calls:
            return self.underlying_price
        strikes = [c.strike for c in self.calls]
        return min(strikes, key=lambda x: abs(x - self.underlying_price))

    def get_calls_by_delta(self, target_delta: float, tolerance: float = 0.05) -> List[OptionContract]:
        """Get calls near a target delta."""
        return [c for c in self.calls
                if abs(abs(c.delta) - abs(target_delta)) <= tolerance]

    def get_puts_by_delta(self, target_delta: float, tolerance: float = 0.05) -> List[OptionContract]:
        """Get puts near a target delta."""
        return [p for p in self.puts
                if abs(abs(p.delta) - abs(target_delta)) <= tolerance]


@dataclass
class IVRankData:
    """IV Rank and Percentile data."""
    ticker: str
    iv_rank: float
    iv_percentile: float
    current_iv: float
    iv_52w_high: float
    iv_52w_low: float
    trade_date: Optional[date] = None

    @property
    def is_iv_elevated(self) -> bool:
        """Check if IV is elevated (rank > 50)."""
        return self.iv_rank > 50

    @property
    def is_iv_extreme(self) -> bool:
        """Check if IV is extreme (rank > 75)."""
        return self.iv_rank > 75


@dataclass
class ExpirationDate:
    """Represents an expiration date with metadata."""
    date: date
    days_to_expiration: int
    is_monthly: bool = False
    is_weekly: bool = False
    is_quarterly: bool = False


class CacheEntry:
    """Cache entry with TTL support."""

    def __init__(self, value: Any, ttl_seconds: int):
        self.value = value
        self.expires_at = time.time() + ttl_seconds

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() > self.expires_at


def cached(ttl_seconds: int = 60):
    """Decorator to cache function results with TTL.

    Args:
        ttl_seconds: Time to live in seconds (default: 60)
    """
    def decorator(func: Callable) -> Callable:
        cache: Dict[str, CacheEntry] = {}

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}:{args}:{sorted(kwargs.items())}"

            # Check cache
            if cache_key in cache:
                entry = cache[cache_key]
                if not entry.is_expired():
                    return entry.value
                else:
                    del cache[cache_key]

            # Call function and cache result
            result = func(self, *args, **kwargs)
            cache[cache_key] = CacheEntry(result, ttl_seconds)
            return result

        return wrapper
    return decorator


class ORATSAdapter:
    """Adapter for ORATS MCP tools.

    Provides a clean, cached interface to ORATS options data via MCP tools.
    All methods include 60-second TTL caching to reduce API calls.

    Usage:
        adapter = ORATSAdapter(mcp_tools)
        chain = adapter.get_live_chain("SPY", "2026-03-20")
        iv_rank = adapter.get_iv_rank("SPY")
    """

    def __init__(self, mcp_tools: Any, default_cache_ttl: int = 60):
        """Initialize the ORATS adapter.

        Args:
            mcp_tools: MCP tools object with mcp__orats__* methods
            default_cache_ttl: Default cache TTL in seconds (default: 60)
        """
        self.mcp_tools = mcp_tools
        self.default_cache_ttl = default_cache_ttl

    @cached(ttl_seconds=60)
    def get_live_chain(self, ticker: str, expiry: str) -> Optional[OptionsChain]:
        """Fetch live options chain for a specific expiration.

        Args:
            ticker: Stock ticker symbol (e.g., "SPY")
            expiry: Expiration date in YYYY-MM-DD format

        Returns:
            OptionsChain object or None if no data available

        Raises:
            Exception: If MCP tool call fails
        """
        try:
            # Call MCP tool
            response = self.mcp_tools.live_strikes_by_expiry(ticker=ticker, expiry=expiry)

            if not response or not isinstance(response, dict):
                return None

            data = response.get("data", [])
            if not data:
                return None

            # Parse first row for metadata
            first_row = data[0]
            ticker_upper = ticker.upper()
            expiration_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            underlying_price = float(first_row.get("stockPrice", 0))
            trade_date_str = first_row.get("tradeDate")
            trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date() if trade_date_str else None

            # Create chain
            chain = OptionsChain(
                ticker=ticker_upper,
                expiration_date=expiration_date,
                underlying_price=underlying_price,
                trade_date=trade_date,
            )

            # Parse contracts
            for row in data:
                strike = float(row.get("strike", 0))

                # Create call option
                call = OptionContract(
                    ticker=ticker_upper,
                    expiration_date=expiration_date,
                    strike=strike,
                    option_type=OptionType.CALL,
                    bid=float(row.get("callBidPrice", 0)),
                    ask=float(row.get("callAskPrice", 0)),
                    mid=float(row.get("callValue", row.get("callMidPrice", 0))),
                    last=float(row.get("callLastPrice", 0)),
                    volume=int(row.get("callVolume", 0)),
                    open_interest=int(row.get("callOpenInterest", 0)),
                    implied_volatility=float(row.get("callIvMean", 0)),
                    delta=float(row.get("callDelta", 0)),
                    gamma=float(row.get("callGamma", 0)),
                    theta=float(row.get("callTheta", 0)),
                    vega=float(row.get("callVega", 0)),
                    rho=float(row.get("callRho", 0)),
                    underlying_price=underlying_price,
                    trade_date=trade_date,
                )
                chain.calls.append(call)

                # Create put option
                put = OptionContract(
                    ticker=ticker_upper,
                    expiration_date=expiration_date,
                    strike=strike,
                    option_type=OptionType.PUT,
                    bid=float(row.get("putBidPrice", 0)),
                    ask=float(row.get("putAskPrice", 0)),
                    mid=float(row.get("putValue", row.get("putMidPrice", 0))),
                    last=float(row.get("putLastPrice", 0)),
                    volume=int(row.get("putVolume", 0)),
                    open_interest=int(row.get("putOpenInterest", 0)),
                    implied_volatility=float(row.get("putIvMean", 0)),
                    delta=float(row.get("putDelta", 0)),
                    gamma=float(row.get("putGamma", 0)),
                    theta=float(row.get("putTheta", 0)),
                    vega=float(row.get("putVega", 0)),
                    rho=float(row.get("putRho", 0)),
                    underlying_price=underlying_price,
                    trade_date=trade_date,
                )
                chain.puts.append(put)

            # Sort by strike
            chain.calls.sort(key=lambda c: c.strike)
            chain.puts.sort(key=lambda p: p.strike)

            return chain

        except Exception as e:
            raise Exception(f"Failed to fetch live chain for {ticker} {expiry}: {str(e)}")

    @cached(ttl_seconds=60)
    def get_iv_rank(self, ticker: str) -> Optional[IVRankData]:
        """Get IV rank and percentile data.

        Args:
            ticker: Stock ticker symbol (e.g., "SPY")

        Returns:
            IVRankData object or None if no data available

        Raises:
            Exception: If MCP tool call fails
        """
        try:
            # Call MCP tool
            response = self.mcp_tools.live_summaries(ticker=ticker)

            if not response or not isinstance(response, dict):
                return None

            data = response.get("data", [])
            if not data:
                return None

            # Parse first row
            row = data[0]
            trade_date_str = row.get("tradeDate")
            trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date() if trade_date_str else None

            return IVRankData(
                ticker=ticker.upper(),
                iv_rank=float(row.get("ivRank", 0)),
                iv_percentile=float(row.get("ivPct", 0)),
                current_iv=float(row.get("orIv", row.get("iv30", 0))),
                iv_52w_high=float(row.get("ivHigh", 0)),
                iv_52w_low=float(row.get("ivLow", 0)),
                trade_date=trade_date,
            )

        except Exception as e:
            raise Exception(f"Failed to fetch IV rank for {ticker}: {str(e)}")

    @cached(ttl_seconds=60)
    def get_expirations(self, ticker: str, include_weekly: bool = True) -> List[ExpirationDate]:
        """Get available expiration dates.

        Args:
            ticker: Stock ticker symbol (e.g., "SPY")
            include_weekly: Include weekly expirations (default: True)

        Returns:
            List of ExpirationDate objects sorted by date

        Raises:
            Exception: If MCP tool call fails
        """
        try:
            # Call MCP tool
            response = self.mcp_tools.live_expirations(ticker=ticker, include="")

            if not response or not isinstance(response, dict):
                return []

            data = response.get("data", [])
            if not data:
                return []

            expirations = []
            today = date.today()

            for row in data:
                exp_str = row.get("expirDate")
                if not exp_str:
                    continue

                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days

                # Determine expiration type
                is_monthly = row.get("isMonthly", False) or exp_date.day >= 15
                is_weekly = row.get("isWeekly", False) or (not is_monthly and exp_date.weekday() == 4)  # Friday
                is_quarterly = row.get("isQuarterly", False)

                # Filter weekly if requested
                if not include_weekly and is_weekly and not is_monthly:
                    continue

                expirations.append(ExpirationDate(
                    date=exp_date,
                    days_to_expiration=dte,
                    is_monthly=is_monthly,
                    is_weekly=is_weekly,
                    is_quarterly=is_quarterly,
                ))

            # Sort by date
            expirations.sort(key=lambda e: e.date)

            return expirations

        except Exception as e:
            raise Exception(f"Failed to fetch expirations for {ticker}: {str(e)}")

    def get_historical_chains(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        target_dte: int = 30,
    ) -> Dict[date, OptionsChain]:
        """Get historical options chains for backtesting.

        Fetches historical options data for each trading day between start_date
        and end_date. Uses the hist_strikes MCP tool.

        Args:
            ticker: Stock ticker symbol (e.g., "SPY")
            start_date: Start date for historical data
            end_date: End date for historical data
            target_dte: Target days to expiration (default: 30)

        Returns:
            Dictionary mapping trade_date to OptionsChain

        Raises:
            Exception: If MCP tool call fails
        """
        chains = {}
        current_date = start_date

        while current_date <= end_date:
            try:
                # Call MCP tool for each date
                response = self.mcp_tools.hist_strikes(
                    ticker=ticker,
                    tradeDate=current_date.strftime("%Y-%m-%d"),
                    dte=str(target_dte),
                )

                if response and isinstance(response, dict):
                    data = response.get("data", [])

                    if data:
                        # Group by expiration
                        chains_by_exp: Dict[date, OptionsChain] = {}

                        for row in data:
                            exp_str = row.get("expirDate")
                            if not exp_str:
                                continue

                            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                            strike = float(row.get("strike", 0))
                            underlying_price = float(row.get("stockPrice", 0))

                            if exp_date not in chains_by_exp:
                                chains_by_exp[exp_date] = OptionsChain(
                                    ticker=ticker.upper(),
                                    expiration_date=exp_date,
                                    underlying_price=underlying_price,
                                    trade_date=current_date,
                                )

                            chain = chains_by_exp[exp_date]

                            # Create call option
                            call = OptionContract(
                                ticker=ticker.upper(),
                                expiration_date=exp_date,
                                strike=strike,
                                option_type=OptionType.CALL,
                                bid=float(row.get("callBidPrice", 0)),
                                ask=float(row.get("callAskPrice", 0)),
                                mid=float(row.get("callValue", 0)),
                                volume=int(row.get("callVolume", 0)),
                                open_interest=int(row.get("callOpenInt", 0)),
                                implied_volatility=float(row.get("callSmvVol", 0)),
                                delta=float(row.get("callDelta", 0)),
                                gamma=float(row.get("gamma", 0)),
                                theta=float(row.get("callTheta", 0)),
                                vega=float(row.get("vega", 0)),
                                rho=float(row.get("callRho", 0)),
                                underlying_price=underlying_price,
                                trade_date=current_date,
                            )
                            chain.calls.append(call)

                            # Create put option
                            put = OptionContract(
                                ticker=ticker.upper(),
                                expiration_date=exp_date,
                                strike=strike,
                                option_type=OptionType.PUT,
                                bid=float(row.get("putBidPrice", 0)),
                                ask=float(row.get("putAskPrice", 0)),
                                mid=float(row.get("putValue", 0)),
                                volume=int(row.get("putVolume", 0)),
                                open_interest=int(row.get("putOpenInt", 0)),
                                implied_volatility=float(row.get("putSmvVol", 0)),
                                delta=float(row.get("putDelta", 0)),
                                gamma=float(row.get("gamma", 0)),
                                theta=float(row.get("putTheta", 0)),
                                vega=float(row.get("vega", 0)),
                                rho=float(row.get("putRho", 0)),
                                underlying_price=underlying_price,
                                trade_date=current_date,
                            )
                            chain.puts.append(put)

                        # Store the chain closest to target DTE
                        if chains_by_exp:
                            best_chain = min(
                                chains_by_exp.values(),
                                key=lambda c: abs((c.expiration_date - current_date).days - target_dte)
                            )
                            best_chain.calls.sort(key=lambda c: c.strike)
                            best_chain.puts.sort(key=lambda p: p.strike)
                            chains[current_date] = best_chain

            except Exception as e:
                # Log error but continue with next date
                print(f"Warning: Failed to fetch historical chain for {ticker} on {current_date}: {str(e)}")

            # Move to next day (skip weekends)
            current_date += timedelta(days=1)
            while current_date.weekday() >= 5:  # Saturday=5, Sunday=6
                current_date += timedelta(days=1)

        return chains

    def get_current_price(self, ticker: str) -> Optional[float]:
        """Get current underlying stock price.

        Args:
            ticker: Stock ticker symbol (e.g., "SPY")

        Returns:
            Current stock price or None if unavailable
        """
        try:
            response = self.mcp_tools.live_summaries(ticker=ticker)

            if not response or not isinstance(response, dict):
                return None

            data = response.get("data", [])
            if not data:
                return None

            return float(data[0].get("stockPrice", 0))

        except Exception:
            return None

    def get_nearest_expiration(
        self,
        ticker: str,
        min_dte: int = 7,
        max_dte: int = 60,
        monthly_only: bool = False,
    ) -> Optional[ExpirationDate]:
        """Get the nearest expiration date within constraints.

        Args:
            ticker: Stock ticker symbol
            min_dte: Minimum days to expiration
            max_dte: Maximum days to expiration
            monthly_only: Only return monthly expirations

        Returns:
            Nearest ExpirationDate or None if no matches
        """
        expirations = self.get_expirations(ticker)

        # Filter by constraints
        filtered = [
            exp for exp in expirations
            if min_dte <= exp.days_to_expiration <= max_dte
            and (not monthly_only or exp.is_monthly)
        ]

        if not filtered:
            return None

        # Return nearest
        return filtered[0]


# Convenience functions

def create_adapter(mcp_tools: Any, cache_ttl: int = 60) -> ORATSAdapter:
    """Create an ORATS adapter instance.

    Args:
        mcp_tools: MCP tools object
        cache_ttl: Cache TTL in seconds

    Returns:
        Configured ORATSAdapter
    """
    return ORATSAdapter(mcp_tools, default_cache_ttl=cache_ttl)


__all__ = [
    "ORATSAdapter",
    "OptionContract",
    "OptionsChain",
    "IVRankData",
    "ExpirationDate",
    "OptionType",
    "create_adapter",
]
