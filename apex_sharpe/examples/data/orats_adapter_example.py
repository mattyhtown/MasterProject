"""Example usage of the ORATS adapter for APEX-SHARPE.

This file demonstrates how to use the ORATSAdapter to fetch options data
using the MCP tools.
"""

from datetime import date, timedelta
from orats_adapter import ORATSAdapter, create_adapter


def example_live_data():
    """Example: Fetch live options data."""
    # Note: In production, mcp_tools would be the actual MCP tools object
    # For this example, we'll show the interface

    # Create adapter (pass in actual mcp_tools object)
    # adapter = create_adapter(mcp_tools)

    print("Example: Fetching live options chain for SPY")
    print("=" * 60)

    # Get nearest expiration
    # exp = adapter.get_nearest_expiration("SPY", min_dte=30, max_dte=45)
    # print(f"Nearest expiration: {exp.date} ({exp.days_to_expiration} DTE)")

    # Get options chain
    # chain = adapter.get_live_chain("SPY", exp.date.strftime("%Y-%m-%d"))
    # print(f"Underlying price: ${chain.underlying_price:.2f}")
    # print(f"ATM strike: ${chain.get_atm_strike():.2f}")
    # print(f"Total calls: {len(chain.calls)}")
    # print(f"Total puts: {len(chain.puts)}")
    # print(f"Put/Call ratio: {chain.put_call_ratio:.2f}")

    # Get ATM options
    # atm_calls = chain.get_calls_by_delta(0.5, tolerance=0.1)
    # atm_puts = chain.get_puts_by_delta(-0.5, tolerance=0.1)

    # print(f"\nATM Call Options (~0.5 delta):")
    # for call in atm_calls[:3]:
    #     print(f"  Strike ${call.strike:.2f}: IV={call.implied_volatility:.1%}, "
    #           f"Delta={call.delta:.3f}, Mid=${call.mid:.2f}")

    # print(f"\nATM Put Options (~-0.5 delta):")
    # for put in atm_puts[:3]:
    #     print(f"  Strike ${put.strike:.2f}: IV={put.implied_volatility:.1%}, "
    #           f"Delta={put.delta:.3f}, Mid=${put.mid:.2f}")


def example_iv_rank():
    """Example: Fetch IV rank data."""
    # adapter = create_adapter(mcp_tools)

    print("\nExample: Fetching IV Rank for SPY")
    print("=" * 60)

    # iv_data = adapter.get_iv_rank("SPY")
    # print(f"Current IV: {iv_data.current_iv:.1%}")
    # print(f"IV Rank: {iv_data.iv_rank:.1f}")
    # print(f"IV Percentile: {iv_data.iv_percentile:.1f}")
    # print(f"52-Week High: {iv_data.iv_52w_high:.1%}")
    # print(f"52-Week Low: {iv_data.iv_52w_low:.1%}")
    # print(f"Is IV Elevated: {iv_data.is_iv_elevated}")
    # print(f"Is IV Extreme: {iv_data.is_iv_extreme}")


def example_expirations():
    """Example: Fetch available expirations."""
    # adapter = create_adapter(mcp_tools)

    print("\nExample: Fetching available expirations for SPY")
    print("=" * 60)

    # Get all expirations (including weeklies)
    # expirations = adapter.get_expirations("SPY", include_weekly=True)

    # print(f"Total expirations: {len(expirations)}")
    # print("\nNext 10 expirations:")
    # for exp in expirations[:10]:
    #     exp_type = []
    #     if exp.is_monthly:
    #         exp_type.append("Monthly")
    #     if exp.is_weekly:
    #         exp_type.append("Weekly")
    #     if exp.is_quarterly:
    #         exp_type.append("Quarterly")
    #     type_str = ", ".join(exp_type) if exp_type else "Standard"
    #     print(f"  {exp.date} - {exp.days_to_expiration:3d} DTE - {type_str}")


def example_historical_backtest():
    """Example: Fetch historical data for backtesting."""
    # adapter = create_adapter(mcp_tools)

    print("\nExample: Fetching historical options data for backtesting")
    print("=" * 60)

    # Define backtest period
    # start_date = date.today() - timedelta(days=90)
    # end_date = date.today() - timedelta(days=1)

    # print(f"Backtest period: {start_date} to {end_date}")

    # Fetch historical chains (30 DTE)
    # chains = adapter.get_historical_chains(
    #     ticker="SPY",
    #     start_date=start_date,
    #     end_date=end_date,
    #     target_dte=30,
    # )

    # print(f"Retrieved {len(chains)} trading days of data")

    # Analyze first chain
    # if chains:
    #     first_date = min(chains.keys())
    #     first_chain = chains[first_date]
    #     print(f"\nFirst chain ({first_date}):")
    #     print(f"  Expiration: {first_chain.expiration_date}")
    #     print(f"  Underlying: ${first_chain.underlying_price:.2f}")
    #     print(f"  Calls: {len(first_chain.calls)}")
    #     print(f"  Puts: {len(first_chain.puts)}")


def example_strategy_integration():
    """Example: Integrate with a trading strategy."""
    # adapter = create_adapter(mcp_tools)

    print("\nExample: Strategy integration")
    print("=" * 60)

    # 1. Check IV environment
    # iv_data = adapter.get_iv_rank("SPY")
    # if not iv_data.is_iv_elevated:
    #     print("IV is low - consider premium buying strategies")
    # else:
    #     print("IV is elevated - consider premium selling strategies")

    # 2. Find optimal expiration
    # exp = adapter.get_nearest_expiration(
    #     "SPY",
    #     min_dte=30,
    #     max_dte=45,
    #     monthly_only=True
    # )

    # 3. Get options chain
    # chain = adapter.get_live_chain("SPY", exp.date.strftime("%Y-%m-%d"))

    # 4. Find trade candidates (e.g., 0.30 delta puts for credit spreads)
    # puts_30_delta = chain.get_puts_by_delta(0.30, tolerance=0.05)

    # print(f"\nTrade candidates (0.30 delta puts):")
    # for put in puts_30_delta[:5]:
    #     credit = put.bid
    #     print(f"  Sell ${put.strike:.2f} put @ ${credit:.2f} "
    #           f"(Delta: {put.delta:.3f}, IV: {put.implied_volatility:.1%})")


def example_caching():
    """Example: Demonstrate caching behavior."""
    # adapter = create_adapter(mcp_tools, cache_ttl=60)

    print("\nExample: Caching demonstration")
    print("=" * 60)

    # First call - fetches from API
    # print("First call (fresh from API)...")
    # start = time.time()
    # chain1 = adapter.get_live_chain("SPY", "2026-03-20")
    # elapsed1 = time.time() - start
    # print(f"Elapsed: {elapsed1:.3f}s")

    # Second call - returns from cache
    # print("\nSecond call (from cache)...")
    # start = time.time()
    # chain2 = adapter.get_live_chain("SPY", "2026-03-20")
    # elapsed2 = time.time() - start
    # print(f"Elapsed: {elapsed2:.3f}s")
    # print(f"Speedup: {elapsed1/elapsed2:.1f}x")

    # Verify same object
    # print(f"Same object: {chain1 is chain2}")


if __name__ == "__main__":
    """
    To use this adapter in production:

    1. Import the MCP tools object
    2. Create adapter: adapter = create_adapter(mcp_tools)
    3. Call methods as shown in examples above

    All methods include automatic caching (60s TTL by default) to reduce API calls.
    """

    print("ORATS Adapter Usage Examples")
    print("=" * 60)
    print("\nThese examples show the interface for using the ORATS adapter.")
    print("To run live, pass the actual mcp_tools object when creating the adapter.")
    print("\nAvailable methods:")
    print("  - get_live_chain(ticker, expiry)")
    print("  - get_iv_rank(ticker)")
    print("  - get_expirations(ticker)")
    print("  - get_historical_chains(ticker, start_date, end_date)")
    print("  - get_current_price(ticker)")
    print("  - get_nearest_expiration(ticker, min_dte, max_dte)")

    # Uncomment to run examples with actual mcp_tools
    # example_live_data()
    # example_iv_rank()
    # example_expirations()
    # example_historical_backtest()
    # example_strategy_integration()
    # example_caching()
