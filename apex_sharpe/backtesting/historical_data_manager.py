"""
Historical Data Manager for APEX-SHARPE Backtesting.

Manages loading, caching, and retrieval of historical options data
from ORATS with local persistence for performance.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Optional, List
import pickle
import os
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from ..data.orats_adapter import ORATSAdapter, OptionsChain
except ImportError:
    from data.orats_adapter import ORATSAdapter, OptionsChain


@dataclass
class DataCache:
    """Configuration for data caching."""
    cache_dir: str = ".cache/apex_sharpe"
    use_cache: bool = True
    cache_ttl_days: int = 30


class HistoricalDataManager:
    """
    Manager for historical options data with caching.

    Loads historical chains from ORATS and caches locally for performance.
    Supports date range queries and iterative data access.

    Example:
        >>> manager = HistoricalDataManager(orats_adapter)
        >>> chains = manager.get_date_range("SPY", start_date, end_date)
        >>> for trade_date, chain in chains.items():
        ...     # Process chain
        ...     pass
    """

    def __init__(
        self,
        orats_adapter: ORATSAdapter,
        cache_config: Optional[DataCache] = None
    ):
        """
        Initialize historical data manager.

        Args:
            orats_adapter: ORATS data adapter
            cache_config: Cache configuration (uses defaults if None)
        """
        self.adapter = orats_adapter
        self.cache_config = cache_config or DataCache()

        # Create cache directory
        if self.cache_config.use_cache:
            Path(self.cache_config.cache_dir).mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self._memory_cache: Dict[str, Dict[date, OptionsChain]] = {}

    def get_date_range(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        target_dte: int = 45,
        force_reload: bool = False
    ) -> Dict[date, OptionsChain]:
        """
        Get historical chains for a date range.

        Args:
            ticker: Stock ticker symbol
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            target_dte: Target days to expiration for chains
            force_reload: Force reload from ORATS, bypass cache

        Returns:
            Dictionary mapping trade_date to OptionsChain
        """
        cache_key = f"{ticker}_{start_date}_{end_date}_{target_dte}"

        # Check memory cache
        if not force_reload and cache_key in self._memory_cache:
            print(f"Using in-memory cache for {ticker}")
            return self._memory_cache[cache_key]

        # Check disk cache
        if not force_reload and self.cache_config.use_cache:
            cached_data = self._load_from_cache(cache_key)
            if cached_data:
                print(f"Loaded {len(cached_data)} days from disk cache")
                self._memory_cache[cache_key] = cached_data
                return cached_data

        # Fetch from ORATS
        print(f"Fetching historical data from ORATS: {ticker} {start_date} to {end_date}")
        chains = self.adapter.get_historical_chains(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            target_dte=target_dte
        )

        if not chains:
            print(f"Warning: No data returned from ORATS for {ticker}")
            return {}

        print(f"Fetched {len(chains)} trading days")

        # Save to cache
        if self.cache_config.use_cache:
            self._save_to_cache(cache_key, chains)

        # Store in memory
        self._memory_cache[cache_key] = chains

        return chains

    def get_single_date(
        self,
        ticker: str,
        trade_date: date,
        target_dte: int = 45
    ) -> Optional[OptionsChain]:
        """
        Get chain for a single date.

        Args:
            ticker: Stock ticker symbol
            trade_date: Trade date
            target_dte: Target days to expiration

        Returns:
            OptionsChain or None if not available
        """
        # Try to get from existing cache
        cache_key = f"{ticker}_{trade_date}_{trade_date}_{target_dte}"

        if cache_key in self._memory_cache:
            chains = self._memory_cache[cache_key]
            return chains.get(trade_date)

        # Fetch single day
        chains = self.get_date_range(ticker, trade_date, trade_date, target_dte)
        return chains.get(trade_date)

    def preload_data(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        target_dte: int = 45
    ) -> None:
        """
        Preload and cache data for a date range.

        Args:
            ticker: Stock ticker symbol
            start_date: Start date
            end_date: End date
            target_dte: Target days to expiration
        """
        print(f"\nPreloading data for {ticker}: {start_date} to {end_date}")
        self.get_date_range(ticker, start_date, end_date, target_dte)
        print("Preload complete\n")

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        """
        Clear cached data.

        Args:
            ticker: Clear only this ticker (or all if None)
        """
        if ticker:
            # Clear memory cache for ticker
            keys_to_remove = [k for k in self._memory_cache.keys() if k.startswith(ticker)]
            for key in keys_to_remove:
                del self._memory_cache[key]
            print(f"Cleared memory cache for {ticker}")
        else:
            # Clear all memory cache
            self._memory_cache.clear()
            print("Cleared all memory cache")

        # Clear disk cache
        if self.cache_config.use_cache:
            cache_dir = Path(self.cache_config.cache_dir)
            if ticker:
                pattern = f"{ticker}_*.pkl"
                for cache_file in cache_dir.glob(pattern):
                    cache_file.unlink()
                print(f"Cleared disk cache for {ticker}")
            else:
                for cache_file in cache_dir.glob("*.pkl"):
                    cache_file.unlink()
                print("Cleared all disk cache")

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get path to cache file."""
        return Path(self.cache_config.cache_dir) / f"{cache_key}.pkl"

    def _load_from_cache(self, cache_key: str) -> Optional[Dict[date, OptionsChain]]:
        """Load data from disk cache."""
        cache_path = self._get_cache_path(cache_key)

        if not cache_path.exists():
            return None

        # Check age
        if self.cache_config.cache_ttl_days > 0:
            age_days = (date.today() - date.fromtimestamp(cache_path.stat().st_mtime)).days
            if age_days > self.cache_config.cache_ttl_days:
                print(f"Cache expired (age: {age_days} days)")
                return None

        try:
            with open(cache_path, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Warning: Failed to load cache: {e}")
            return None

    def _save_to_cache(self, cache_key: str, data: Dict[date, OptionsChain]) -> None:
        """Save data to disk cache."""
        cache_path = self._get_cache_path(cache_key)

        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"Saved to cache: {cache_path.name}")
        except Exception as e:
            print(f"Warning: Failed to save cache: {e}")

    def get_available_dates(
        self,
        ticker: str,
        start_date: date,
        end_date: date
    ) -> List[date]:
        """
        Get list of available trading dates (without loading full chains).

        Args:
            ticker: Stock ticker symbol
            start_date: Start date
            end_date: End date

        Returns:
            List of dates with available data
        """
        # Generate list of potential trading dates (excluding weekends)
        dates = []
        current = start_date

        while current <= end_date:
            # Skip weekends
            if current.weekday() < 5:  # Monday=0, Friday=4
                dates.append(current)
            current += timedelta(days=1)

        return dates

    def estimate_data_size(
        self,
        ticker: str,
        start_date: date,
        end_date: date
    ) -> Dict[str, int]:
        """
        Estimate size of data for a date range.

        Args:
            ticker: Stock ticker symbol
            start_date: Start date
            end_date: End date

        Returns:
            Dictionary with estimated counts
        """
        trading_days = len(self.get_available_dates(ticker, start_date, end_date))

        # Rough estimates
        avg_strikes_per_chain = 100
        avg_bytes_per_contract = 200

        estimated_contracts = trading_days * avg_strikes_per_chain * 2  # Calls + Puts
        estimated_bytes = estimated_contracts * avg_bytes_per_contract

        return {
            'trading_days': trading_days,
            'estimated_contracts': estimated_contracts,
            'estimated_mb': estimated_bytes / (1024 * 1024)
        }

    def get_cache_stats(self) -> Dict[str, any]:
        """
        Get statistics about cached data.

        Returns:
            Dictionary with cache statistics
        """
        stats = {
            'memory_cached_ranges': len(self._memory_cache),
            'disk_cached_files': 0,
            'total_cache_size_mb': 0.0
        }

        if self.cache_config.use_cache:
            cache_dir = Path(self.cache_config.cache_dir)
            if cache_dir.exists():
                cache_files = list(cache_dir.glob("*.pkl"))
                stats['disk_cached_files'] = len(cache_files)
                total_size = sum(f.stat().st_size for f in cache_files)
                stats['total_cache_size_mb'] = total_size / (1024 * 1024)

        return stats


def create_data_manager(
    orats_adapter: ORATSAdapter,
    cache_dir: str = ".cache/apex_sharpe",
    use_cache: bool = True
) -> HistoricalDataManager:
    """
    Factory function to create a HistoricalDataManager.

    Args:
        orats_adapter: ORATS data adapter
        cache_dir: Directory for cache files
        use_cache: Whether to enable caching

    Returns:
        Configured HistoricalDataManager
    """
    cache_config = DataCache(
        cache_dir=cache_dir,
        use_cache=use_cache
    )

    return HistoricalDataManager(orats_adapter, cache_config)
