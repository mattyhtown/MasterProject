"""Integration test for ORATS adapter with MCP tools.

This demonstrates how to integrate the adapter with actual MCP tools.
"""

from datetime import date, timedelta
from typing import Any


class MockMCPTools:
    """Mock MCP tools for testing purposes.

    In production, this would be the actual MCP tools object with
    mcp__orats__* methods.
    """

    def live_strikes_by_expiry(self, ticker: str, expiry: str) -> dict:
        """Mock live_strikes_by_expiry tool."""
        return {
            "data": [
                {
                    "ticker": ticker,
                    "tradeDate": "2026-02-05",
                    "expirDate": expiry,
                    "strike": 580.0,
                    "stockPrice": 585.50,
                    "callBidPrice": 8.50,
                    "callAskPrice": 8.70,
                    "callValue": 8.60,
                    "callVolume": 150,
                    "callOpenInterest": 1200,
                    "callIvMean": 0.18,
                    "callDelta": 0.52,
                    "callGamma": 0.015,
                    "callTheta": -0.12,
                    "callVega": 0.25,
                    "callRho": 0.10,
                    "putBidPrice": 3.20,
                    "putAskPrice": 3.40,
                    "putValue": 3.30,
                    "putVolume": 200,
                    "putOpenInterest": 1500,
                    "putIvMean": 0.19,
                    "putDelta": -0.48,
                    "putGamma": 0.015,
                    "putTheta": -0.11,
                    "putVega": 0.25,
                    "putRho": -0.09,
                },
                {
                    "ticker": ticker,
                    "tradeDate": "2026-02-05",
                    "expirDate": expiry,
                    "strike": 585.0,
                    "stockPrice": 585.50,
                    "callBidPrice": 5.80,
                    "callAskPrice": 6.00,
                    "callValue": 5.90,
                    "callVolume": 250,
                    "callOpenInterest": 2000,
                    "callIvMean": 0.17,
                    "callDelta": 0.48,
                    "callGamma": 0.018,
                    "callTheta": -0.13,
                    "callVega": 0.28,
                    "callRho": 0.11,
                    "putBidPrice": 5.10,
                    "putAskPrice": 5.30,
                    "putValue": 5.20,
                    "putVolume": 300,
                    "putOpenInterest": 2500,
                    "putIvMean": 0.18,
                    "putDelta": -0.52,
                    "putGamma": 0.018,
                    "putTheta": -0.12,
                    "putVega": 0.28,
                    "putRho": -0.10,
                },
            ]
        }

    def live_summaries(self, ticker: str) -> dict:
        """Mock live_summaries tool."""
        return {
            "data": [
                {
                    "ticker": ticker,
                    "tradeDate": "2026-02-05",
                    "stockPrice": 585.50,
                    "ivRank": 45.5,
                    "ivPct": 44.2,
                    "orIv": 0.18,
                    "iv30": 0.18,
                    "ivHigh": 0.35,
                    "ivLow": 0.12,
                }
            ]
        }

    def live_expirations(self, ticker: str, include: str = "") -> dict:
        """Mock live_expirations tool."""
        today = date.today()
        return {
            "data": [
                {
                    "expirDate": (today + timedelta(days=10)).strftime("%Y-%m-%d"),
                    "isWeekly": True,
                    "isMonthly": False,
                    "isQuarterly": False,
                },
                {
                    "expirDate": (today + timedelta(days=17)).strftime("%Y-%m-%d"),
                    "isWeekly": True,
                    "isMonthly": False,
                    "isQuarterly": False,
                },
                {
                    "expirDate": (today + timedelta(days=38)).strftime("%Y-%m-%d"),
                    "isWeekly": False,
                    "isMonthly": True,
                    "isQuarterly": False,
                },
                {
                    "expirDate": (today + timedelta(days=45)).strftime("%Y-%m-%d"),
                    "isWeekly": True,
                    "isMonthly": False,
                    "isQuarterly": False,
                },
            ]
        }

    def hist_strikes(self, ticker: str, tradeDate: str, dte: str = "") -> dict:
        """Mock hist_strikes tool."""
        trade_dt = datetime.strptime(tradeDate, "%Y-%m-%d")
        expiry_dt = trade_dt + timedelta(days=30)

        return {
            "data": [
                {
                    "ticker": ticker,
                    "tradeDate": tradeDate,
                    "expirDate": expiry_dt.strftime("%Y-%m-%d"),
                    "strike": 580.0,
                    "stockPrice": 585.50,
                    "callBidPrice": 8.50,
                    "callAskPrice": 8.70,
                    "callValue": 8.60,
                    "callVolume": 150,
                    "callOpenInt": 1200,
                    "callSmvVol": 0.18,
                    "callDelta": 0.52,
                    "gamma": 0.015,
                    "callTheta": -0.12,
                    "vega": 0.25,
                    "callRho": 0.10,
                    "putBidPrice": 3.20,
                    "putAskPrice": 3.40,
                    "putValue": 3.30,
                    "putVolume": 200,
                    "putOpenInt": 1500,
                    "putSmvVol": 0.19,
                    "putDelta": -0.48,
                    "putTheta": -0.11,
                    "putRho": -0.09,
                }
            ]
        }


def test_live_chain():
    """Test fetching live options chain."""
    from orats_adapter import create_adapter

    print("Test: Live Options Chain")
    print("=" * 60)

    # Create adapter with mock tools
    mock_tools = MockMCPTools()
    adapter = create_adapter(mock_tools)

    # Fetch chain
    expiry = (date.today() + timedelta(days=38)).strftime("%Y-%m-%d")
    chain = adapter.get_live_chain("SPY", expiry)

    assert chain is not None, "Chain should not be None"
    assert chain.ticker == "SPY"
    assert len(chain.calls) == 2
    assert len(chain.puts) == 2
    assert chain.underlying_price == 585.50

    print(f"✓ Ticker: {chain.ticker}")
    print(f"✓ Expiration: {chain.expiration_date}")
    print(f"✓ Underlying: ${chain.underlying_price:.2f}")
    print(f"✓ Calls: {len(chain.calls)}")
    print(f"✓ Puts: {len(chain.puts)}")
    print(f"✓ Put/Call Ratio: {chain.put_call_ratio:.2f}")
    print(f"✓ ATM Strike: ${chain.get_atm_strike():.2f}")

    # Test delta filtering
    atm_calls = chain.get_calls_by_delta(0.50, tolerance=0.10)
    assert len(atm_calls) > 0, "Should find ATM calls"
    print(f"✓ ATM calls found: {len(atm_calls)}")

    print("✓ All assertions passed!\n")


def test_iv_rank():
    """Test fetching IV rank."""
    from orats_adapter import create_adapter

    print("Test: IV Rank")
    print("=" * 60)

    mock_tools = MockMCPTools()
    adapter = create_adapter(mock_tools)

    iv_data = adapter.get_iv_rank("SPY")

    assert iv_data is not None, "IV data should not be None"
    assert iv_data.ticker == "SPY"
    assert 0 <= iv_data.iv_rank <= 100
    assert not iv_data.is_iv_elevated  # 45.5 < 50

    print(f"✓ Ticker: {iv_data.ticker}")
    print(f"✓ IV Rank: {iv_data.iv_rank:.1f}")
    print(f"✓ IV Percentile: {iv_data.iv_percentile:.1f}")
    print(f"✓ Current IV: {iv_data.current_iv:.1%}")
    print(f"✓ 52W High: {iv_data.iv_52w_high:.1%}")
    print(f"✓ 52W Low: {iv_data.iv_52w_low:.1%}")
    print(f"✓ Is Elevated: {iv_data.is_iv_elevated}")
    print("✓ All assertions passed!\n")


def test_expirations():
    """Test fetching expirations."""
    from orats_adapter import create_adapter

    print("Test: Expirations")
    print("=" * 60)

    mock_tools = MockMCPTools()
    adapter = create_adapter(mock_tools)

    expirations = adapter.get_expirations("SPY")

    assert len(expirations) > 0, "Should find expirations"
    assert all(exp.days_to_expiration > 0 for exp in expirations), "DTE should be positive"

    print(f"✓ Total expirations: {len(expirations)}")
    for exp in expirations:
        exp_type = []
        if exp.is_monthly:
            exp_type.append("Monthly")
        if exp.is_weekly:
            exp_type.append("Weekly")
        type_str = ", ".join(exp_type) if exp_type else "Standard"
        print(f"  - {exp.date} ({exp.days_to_expiration} DTE) - {type_str}")

    # Test nearest expiration
    nearest = adapter.get_nearest_expiration("SPY", min_dte=30, max_dte=45)
    assert nearest is not None, "Should find nearest expiration"
    print(f"✓ Nearest monthly (30-45 DTE): {nearest.date}")
    print("✓ All assertions passed!\n")


def test_caching():
    """Test caching behavior."""
    from orats_adapter import create_adapter
    import time

    print("Test: Caching")
    print("=" * 60)

    mock_tools = MockMCPTools()
    adapter = create_adapter(mock_tools, cache_ttl=2)  # 2 second TTL for testing

    expiry = (date.today() + timedelta(days=38)).strftime("%Y-%m-%d")

    # First call
    start = time.time()
    chain1 = adapter.get_live_chain("SPY", expiry)
    elapsed1 = time.time() - start

    # Second call (cached)
    start = time.time()
    chain2 = adapter.get_live_chain("SPY", expiry)
    elapsed2 = time.time() - start

    print(f"✓ First call: {elapsed1*1000:.2f}ms")
    print(f"✓ Second call (cached): {elapsed2*1000:.2f}ms")
    print(f"✓ Speedup: {elapsed1/elapsed2:.1f}x")
    assert chain1 is chain2, "Should return same cached object"
    print("✓ Same object returned from cache")

    # Wait for cache to expire
    print("  Waiting for cache expiration...")
    time.sleep(2.1)

    # Third call (fresh)
    start = time.time()
    chain3 = adapter.get_live_chain("SPY", expiry)
    elapsed3 = time.time() - start

    print(f"✓ Third call (expired cache): {elapsed3*1000:.2f}ms")
    print("✓ All assertions passed!\n")


def test_option_contract_properties():
    """Test OptionContract computed properties."""
    from orats_adapter import OptionContract, OptionType

    print("Test: OptionContract Properties")
    print("=" * 60)

    # Create test contract
    call = OptionContract(
        ticker="SPY",
        expiration_date=date.today() + timedelta(days=30),
        strike=580.0,
        option_type=OptionType.CALL,
        mid=8.60,
        underlying_price=585.50,
    )

    # Test properties
    assert call.days_to_expiration == 30
    assert call.is_itm  # 585.50 > 580.00
    assert not call.is_otm
    assert abs(call.intrinsic_value - 5.50) < 0.01  # 585.50 - 580.00
    assert abs(call.extrinsic_value - 3.10) < 0.01  # 8.60 - 5.50

    print(f"✓ DTE: {call.days_to_expiration}")
    print(f"✓ ITM: {call.is_itm}")
    print(f"✓ Intrinsic: ${call.intrinsic_value:.2f}")
    print(f"✓ Extrinsic: ${call.extrinsic_value:.2f}")

    # Test put
    put = OptionContract(
        ticker="SPY",
        expiration_date=date.today() + timedelta(days=30),
        strike=580.0,
        option_type=OptionType.PUT,
        mid=3.30,
        underlying_price=585.50,
    )

    assert not put.is_itm  # 585.50 > 580.00 (OTM for put)
    assert put.is_otm
    assert abs(put.intrinsic_value - 0.0) < 0.01
    assert abs(put.extrinsic_value - 3.30) < 0.01

    print(f"✓ Put ITM: {put.is_itm}")
    print(f"✓ Put Intrinsic: ${put.intrinsic_value:.2f}")
    print(f"✓ Put Extrinsic: ${put.extrinsic_value:.2f}")
    print("✓ All assertions passed!\n")


if __name__ == "__main__":
    from datetime import datetime

    print("\n" + "=" * 60)
    print("ORATS Adapter Integration Tests")
    print("=" * 60 + "\n")

    try:
        test_live_chain()
        test_iv_rank()
        test_expirations()
        test_caching()
        test_option_contract_properties()

        print("=" * 60)
        print("✓ ALL TESTS PASSED!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n✗ Test failed: {str(e)}")
        raise
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        raise
