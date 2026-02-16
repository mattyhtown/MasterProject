"""
IBClient — Interactive Brokers data and execution client.

Wraps ib_async (successor to ib_insync) with a synchronous interface
matching the ORATSClient DI pattern. Returns plain dicts for
compatibility with existing agents.

Requires: pip install ib_async
Connection: IB Gateway (port 4001/4002) or TWS (port 7497/7496)
"""

import asyncio
import atexit
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..config import IBCfg

# Lazy import — only fail when actually connecting
_ib_async = None


def _ensure_ib_async():
    global _ib_async
    if _ib_async is None:
        try:
            import ib_async as _mod
            _ib_async = _mod
        except ImportError:
            raise ImportError(
                "ib_async not installed. Run: pip install ib_async"
            )
    return _ib_async


# Ticker → contract type mapping
_INDEX_TICKERS = {"SPX", "NDX", "RUT", "DJX", "VIX"}
_TRADING_CLASS = {
    "SPX": "SPX",    # Monthly
    "SPXW": "SPXW",  # Weekly / 0DTE
}


class IBClient:
    """Interactive Brokers client with synchronous interface.

    Usage:
        with IBClient(config) as ib:
            summary = ib.account_summary()
            bars = ib.historical_bars("SPY", "30 D", "1 day")
    """

    def __init__(self, config: IBCfg):
        self.config = config
        self._ib = None
        self._loop = None
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib is not None and self._ib.isConnected()

    def connect(self) -> None:
        """Connect to IB Gateway or TWS."""
        if self.is_connected:
            return
        mod = _ensure_ib_async()
        self._ib = mod.IB()
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(
                self._ib.connectAsync(
                    self.config.host,
                    self.config.port,
                    clientId=self.config.client_id,
                    timeout=self.config.timeout,
                )
            )
            self._connected = True
            acct = self._ib.managedAccounts()
            mode = "PAPER" if self.config.paper else "LIVE"
            print(f"[IB] Connected ({mode}) — account(s): {', '.join(acct)}")
        except Exception as exc:
            self._connected = False
            raise ConnectionError(f"[IB] Connection failed: {exc}") from exc

    def disconnect(self) -> None:
        """Disconnect from IB."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._connected = False
        if self._loop and not self._loop.is_closed():
            self._loop.close()
        self._ib = None
        self._loop = None

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    # -- Contract builders ---------------------------------------------------

    def _stock(self, ticker: str):
        """Build a Stock or Index contract."""
        mod = _ensure_ib_async()
        if ticker.upper() in _INDEX_TICKERS:
            exchange = "CBOE" if ticker.upper() != "RUT" else "RUSSELL"
            contract = mod.Index(ticker.upper(), exchange, "USD")
        else:
            contract = mod.Stock(ticker.upper(), "SMART", "USD")
        self._ib.qualifyContracts(contract)
        return contract

    def _option(self, ticker: str, expiry: str, strike: float,
                right: str, trading_class: str = ""):
        """Build an Option contract.

        Args:
            ticker: Underlying symbol
            expiry: Expiration date YYYYMMDD or YYYY-MM-DD
            strike: Strike price
            right: 'C' or 'P'
            trading_class: e.g. 'SPX' (monthly) or 'SPXW' (weekly/0DTE)
        """
        mod = _ensure_ib_async()
        exp_fmt = expiry.replace("-", "")
        exchange = "SMART"
        if ticker.upper() in _INDEX_TICKERS:
            exchange = "CBOE"
        tc = trading_class or _TRADING_CLASS.get(ticker.upper(), "")
        contract = mod.Option(
            ticker.upper(), exp_fmt, strike, right, exchange,
            tradingClass=tc if tc else ticker.upper(),
        )
        return contract

    # -- Historical data -----------------------------------------------------

    def historical_bars(self, ticker: str, duration: str = "30 D",
                        bar_size: str = "1 day",
                        what_to_show: str = "TRADES") -> List[Dict]:
        """Download historical OHLCV bars.

        Args:
            ticker: Symbol (SPY, SPX, QQQ, etc.)
            duration: e.g. '30 D', '6 M', '1 Y', '10 Y'
            bar_size: e.g. '1 min', '5 mins', '1 hour', '1 day'
            what_to_show: 'TRADES', 'MIDPOINT', 'BID', 'ASK'

        Returns:
            List of {date, open, high, low, close, volume, bar_count}
        """
        contract = self._stock(ticker)
        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
        )
        return [
            {
                "date": str(b.date),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": int(b.volume),
                "bar_count": b.barCount,
            }
            for b in bars
        ]

    # -- Live quotes ---------------------------------------------------------

    def live_quote(self, ticker: str) -> Dict:
        """Get current bid/ask/last for a ticker."""
        contract = self._stock(ticker)
        self._ib.reqMarketDataType(4)  # delayed if no subscription
        [t] = self._ib.reqTickers(contract)
        return {
            "symbol": ticker.upper(),
            "bid": t.bid if t.bid == t.bid else None,  # NaN check
            "ask": t.ask if t.ask == t.ask else None,
            "last": t.last if t.last == t.last else None,
            "volume": int(t.volume) if t.volume == t.volume else 0,
            "time": str(t.time) if t.time else None,
        }

    # -- Options chain -------------------------------------------------------

    def option_params(self, ticker: str) -> Dict:
        """Get available expirations and strikes for options."""
        contract = self._stock(ticker)
        chains = self._ib.reqSecDefOptParams(
            contract.symbol, "", contract.secType, contract.conId,
        )
        if not chains:
            return {"expirations": [], "strikes": [], "exchanges": []}
        # Pick SMART or first available
        chain = next(
            (c for c in chains if c.exchange == "SMART"),
            chains[0],
        )
        return {
            "expirations": sorted(chain.expirations),
            "strikes": sorted(chain.strikes),
            "exchange": chain.exchange,
            "trading_class": chain.tradingClass,
            "multiplier": chain.multiplier,
        }

    def option_chain(self, ticker: str, expiry: str,
                     strike_range: float = 50.0) -> Optional[Dict]:
        """Fetch live option chain with Greeks.

        Returns data in ORATS-compatible format:
            {data: [{strike, expirDate, callBidPrice, callAskPrice,
                      callValue, putBidPrice, putAskPrice, putValue,
                      delta, gamma, theta, vega, iv, stockPrice, ...}]}

        Args:
            ticker: Underlying symbol
            expiry: Expiration date YYYY-MM-DD
            strike_range: Fetch strikes within ±range of spot
        """
        mod = _ensure_ib_async()
        contract = self._stock(ticker)
        [und_ticker] = self._ib.reqTickers(contract)
        spot = und_ticker.marketPrice()
        if spot != spot:  # NaN
            spot = und_ticker.close
        if not spot or spot != spot:
            return None

        # Get chain params
        params = self.option_params(ticker)
        if not params["expirations"]:
            return None

        exp_fmt = expiry.replace("-", "")
        if exp_fmt not in params["expirations"]:
            # Find closest expiry
            avail = sorted(params["expirations"])
            exp_fmt = min(avail, key=lambda e: abs(
                int(e) - int(exp_fmt))) if avail else exp_fmt

        # Filter strikes near spot
        strikes = [
            s for s in params["strikes"]
            if spot - strike_range <= s <= spot + strike_range
        ]
        if not strikes:
            strikes = sorted(params["strikes"])

        # Build option contracts
        contracts = []
        for right in ["P", "C"]:
            for strike in strikes:
                opt = self._option(
                    ticker, exp_fmt, strike, right,
                    trading_class=params.get("trading_class", ""),
                )
                contracts.append(opt)

        # Qualify in batches (IB has limits)
        qualified = []
        batch_size = 50
        for i in range(0, len(contracts), batch_size):
            batch = contracts[i:i + batch_size]
            results = self._ib.qualifyContracts(*batch)
            qualified.extend([c for c in results if c is not None])

        if not qualified:
            return None

        # Request market data + Greeks
        tickers = self._ib.reqTickers(*qualified)
        self._ib.sleep(2)  # Allow Greeks to populate

        # Build ORATS-compatible output
        data = []
        # Group by strike
        by_strike: Dict[float, Dict] = {}
        for t in tickers:
            c = t.contract
            strike = c.strike
            if strike not in by_strike:
                by_strike[strike] = {
                    "strike": strike,
                    "expirDate": f"{c.lastTradeDateOrContractMonth[:4]}-"
                                f"{c.lastTradeDateOrContractMonth[4:6]}-"
                                f"{c.lastTradeDateOrContractMonth[6:]}",
                    "stockPrice": spot,
                }
            row = by_strike[strike]
            greeks = t.modelGreeks
            if c.right == "C":
                row["callBidPrice"] = t.bid if t.bid == t.bid else 0
                row["callAskPrice"] = t.ask if t.ask == t.ask else 0
                row["callValue"] = t.modelGreeks.optPrice if greeks else 0
                if greeks:
                    row["delta"] = round(greeks.delta or 0, 4)
                    row["gamma"] = round(greeks.gamma or 0, 6)
                    row["callSmvVol"] = round((greeks.impliedVol or 0), 4)
            elif c.right == "P":
                row["putBidPrice"] = t.bid if t.bid == t.bid else 0
                row["putAskPrice"] = t.ask if t.ask == t.ask else 0
                row["putValue"] = t.modelGreeks.optPrice if greeks else 0
                if greeks:
                    row["putSmvVol"] = round((greeks.impliedVol or 0), 4)
                    # Theta/vega are shared, take from puts if not set
                    if "theta" not in row:
                        row["theta"] = round(greeks.theta or 0, 4)
                    if "vega" not in row:
                        row["vega"] = round(greeks.vega or 0, 4)

        data = sorted(by_strike.values(), key=lambda r: r["strike"])

        # Cancel market data
        for t in tickers:
            self._ib.cancelMktData(t.contract)

        return {"data": data}

    # -- Account data --------------------------------------------------------

    def account_summary(self) -> Dict:
        """Get account summary (net liq, buying power, margin, etc.)."""
        accounts = self._ib.managedAccounts()
        acct = self.config.account_id or (accounts[0] if accounts else "")
        summary = self._ib.accountSummary(acct)
        result = {"account": acct}
        for item in summary:
            if item.tag in (
                "NetLiquidation", "BuyingPower", "TotalCashValue",
                "GrossPositionValue", "MaintMarginReq", "AvailableFunds",
                "InitMarginReq", "ExcessLiquidity",
            ):
                try:
                    result[item.tag] = float(item.value)
                except ValueError:
                    result[item.tag] = item.value
        return result

    def positions(self) -> List[Dict]:
        """Get all open positions."""
        raw = self._ib.positions()
        result = []
        for pos in raw:
            c = pos.contract
            entry = {
                "account": pos.account,
                "symbol": c.symbol,
                "secType": c.secType,
                "exchange": c.exchange,
                "qty": float(pos.position),
                "avg_cost": float(pos.avgCost),
                "conId": c.conId,
            }
            if c.secType == "OPT":
                entry.update({
                    "strike": c.strike,
                    "right": c.right,
                    "expiry": c.lastTradeDateOrContractMonth,
                    "multiplier": c.multiplier,
                    "tradingClass": c.tradingClass,
                })
            result.append(entry)
        return result

    def portfolio(self) -> List[Dict]:
        """Get portfolio items with market values."""
        items = self._ib.portfolio()
        return [
            {
                "symbol": item.contract.symbol,
                "secType": item.contract.secType,
                "qty": float(item.position),
                "market_price": float(item.marketPrice),
                "market_value": float(item.marketValue),
                "avg_cost": float(item.averageCost),
                "unrealized_pnl": float(item.unrealizedPNL),
                "realized_pnl": float(item.realizedPNL),
            }
            for item in items
        ]

    def portfolio_pnl(self) -> Dict:
        """Get account-level P&L."""
        accounts = self._ib.managedAccounts()
        acct = self.config.account_id or (accounts[0] if accounts else "")
        self._ib.reqPnL(acct)
        self._ib.sleep(1)
        pnl_list = self._ib.pnl()
        if pnl_list:
            p = pnl_list[0]
            return {
                "daily_pnl": p.dailyPnL or 0,
                "unrealized_pnl": p.unrealizedPnL or 0,
                "realized_pnl": p.realizedPnL or 0,
            }
        return {"daily_pnl": 0, "unrealized_pnl": 0, "realized_pnl": 0}

    # -- Order execution -----------------------------------------------------

    def place_combo_order(self, legs: List[Dict], action: str = "SELL",
                          quantity: int = 1, limit_price: float = 0.0,
                          timeout: int = 60) -> Dict:
        """Place a multi-leg combo (BAG) order.

        Args:
            legs: List of {symbol, expiry, strike, right, action, ratio}
            action: 'BUY' or 'SELL' for the combo
            quantity: Number of combos
            limit_price: Net limit price (credit if SELL)
            timeout: Seconds to wait for fill before canceling

        Returns:
            {order_id, status, fills: [{leg, price, commission}], ...}
        """
        mod = _ensure_ib_async()

        # Build and qualify individual contracts
        option_contracts = []
        for leg in legs:
            opt = self._option(
                leg["symbol"], leg["expiry"], leg["strike"], leg["right"],
                trading_class=leg.get("trading_class", ""),
            )
            option_contracts.append(opt)

        qualified = self._ib.qualifyContracts(*option_contracts)

        # Build BAG contract
        combo = mod.Contract()
        combo.symbol = legs[0]["symbol"].upper()
        combo.secType = "BAG"
        combo.currency = "USD"
        combo.exchange = "SMART"

        combo_legs = []
        for i, (leg, contract) in enumerate(zip(legs, qualified)):
            cl = mod.ComboLeg()
            cl.conId = contract.conId
            cl.ratio = leg.get("ratio", 1)
            cl.action = leg["action"]
            cl.exchange = "SMART"
            combo_legs.append(cl)
        combo.comboLegs = combo_legs

        # Place limit order
        order = mod.LimitOrder(action, quantity, limit_price)
        order.tif = "DAY"

        trade = self._ib.placeOrder(combo, order)

        # Wait for fill
        elapsed = 0
        while not trade.isDone() and elapsed < timeout:
            self._ib.sleep(1)
            elapsed += 1

        # Build result
        fills = []
        for fill in trade.fills:
            fills.append({
                "conId": fill.contract.conId,
                "price": fill.execution.price,
                "qty": fill.execution.shares,
                "commission": fill.commissionReport.commission
                if fill.commissionReport else 0,
                "time": str(fill.execution.time),
            })

        status = trade.orderStatus.status if trade.orderStatus else "Unknown"

        # Cancel if not filled
        if not trade.isDone():
            self._ib.cancelOrder(order)
            status = "CANCELLED_TIMEOUT"

        return {
            "order_id": trade.order.orderId,
            "perm_id": trade.order.permId,
            "status": status,
            "fills": fills,
            "avg_price": trade.orderStatus.avgFillPrice
            if trade.orderStatus else 0,
            "filled_qty": trade.orderStatus.filled
            if trade.orderStatus else 0,
        }

    def what_if_order(self, legs: List[Dict], action: str = "SELL",
                       quantity: int = 1,
                       limit_price: float = 0.0) -> Optional[Dict]:
        """Preview margin impact without placing the order.

        Returns:
            {init_margin_change, maint_margin_change, equity_with_loan, ...}
        """
        mod = _ensure_ib_async()

        option_contracts = []
        for leg in legs:
            opt = self._option(
                leg["symbol"], leg["expiry"], leg["strike"], leg["right"],
                trading_class=leg.get("trading_class", ""),
            )
            option_contracts.append(opt)
        self._ib.qualifyContracts(*option_contracts)

        combo = mod.Contract()
        combo.symbol = legs[0]["symbol"].upper()
        combo.secType = "BAG"
        combo.currency = "USD"
        combo.exchange = "SMART"

        combo_legs = []
        for leg, contract in zip(legs, option_contracts):
            cl = mod.ComboLeg()
            cl.conId = contract.conId
            cl.ratio = leg.get("ratio", 1)
            cl.action = leg["action"]
            cl.exchange = "SMART"
            combo_legs.append(cl)
        combo.comboLegs = combo_legs

        order = mod.LimitOrder(action, quantity, limit_price)

        try:
            wi = self._ib.whatIfOrder(combo, order)
            return {
                "init_margin_change": float(wi.initMarginChange or 0),
                "maint_margin_change": float(wi.maintMarginChange or 0),
                "equity_with_loan": float(wi.equityWithLoanValue or 0),
                "commission": float(wi.commission or 0),
                "min_commission": float(wi.minCommission or 0),
                "max_commission": float(wi.maxCommission or 0),
            }
        except Exception as exc:
            print(f"[IB] whatIfOrder failed: {exc}")
            return None
