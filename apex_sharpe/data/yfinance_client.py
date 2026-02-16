"""
yfinance helpers — extracted from ZeroDTEMonitor.

Cross-check spot prices and fetch credit spread data (HYG/TLT).
"""

from typing import Optional, Tuple


def yf_price(symbol: str) -> Optional[float]:
    """Cross-check spot price via yfinance."""
    try:
        import yfinance as yf
        m = {"SPX": "^GSPC", "SPY": "SPY"}
        h = yf.Ticker(m.get(symbol, symbol)).history(period="1d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def yf_credit() -> Tuple[Optional[float], Optional[float],
                          Optional[float], Optional[float]]:
    """Fetch HYG and TLT closes for credit spread signal."""
    try:
        import yfinance as yf
        hyg = yf.Ticker("HYG").history(period="2d")
        tlt = yf.Ticker("TLT").history(period="2d")
        if len(hyg) >= 2 and len(tlt) >= 2:
            return (
                float(hyg["Close"].iloc[-1]),
                float(tlt["Close"].iloc[-1]),
                float(hyg["Close"].iloc[-2]),
                float(tlt["Close"].iloc[-2]),
            )
    except Exception:
        pass
    return None, None, None, None
