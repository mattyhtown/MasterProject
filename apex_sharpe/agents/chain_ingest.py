"""
ChainIngestAgent — intraday option chain snapshots + bars + vol surface.

Periodically fetches option chain data from IB or ORATS, stores each
snapshot in the `chain_snapshots` table, intraday bars in `intraday_bars`,
and vol surface data in `vol_surface_snapshots`.

Provides query methods for agents to retrieve the latest chain (or
historical snapshots) in ORATS-compatible format.

Flow:
    Source (IB/ORATS) -> normalize -> batch upsert -> Supabase
    Supabase -> query -> denormalize -> ORATS-compatible chain dict

Usage:
    python -m apex_sharpe chain-ingest              # polling loop
    python -m apex_sharpe chain-ingest latest SPY   # latest snapshot
    python -m apex_sharpe chain-ingest backfill-bars SPX "30 D" "1 min"
"""

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import ChainIngestCfg, SupabaseCfg
from ..database.supabase_sync import SupabaseSync
from ..types import AgentResult, C


CHAIN_CACHE_PATH = os.path.expanduser("~/.apex_chain_cache.json")


class ChainIngestAgent(BaseAgent):
    """Ingest and query intraday option chain snapshots, bars, and vol surface.

    Actions (via context["action"]):
        "ingest"         - fetch chain from source and store snapshot
        "poll"           - continuous polling loop (blocking)
        "latest"         - query most recent snapshot for ticker/expiry
        "history"        - query snapshots within a time range
        "backfill_bars"  - pull IB historical bars and store
        "status"         - report local cache contents
        "ensure_schema"  - verify DB tables exist
    """

    def __init__(self, config: ChainIngestCfg = None,
                 supabase_cfg: SupabaseCfg = None,
                 orats=None, ib_client=None):
        config = config or ChainIngestCfg()
        super().__init__("ChainIngest", config)
        self._db = SupabaseSync(supabase_cfg or SupabaseCfg())
        self._orats = orats
        self._ib = ib_client
        self._cache = self._load_cache()

    @property
    def enabled(self) -> bool:
        return self._db.enabled

    # -- Cache -----------------------------------------------------------------

    def _load_cache(self) -> Dict:
        if os.path.exists(CHAIN_CACHE_PATH):
            try:
                with open(CHAIN_CACHE_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_cache(self) -> None:
        with open(CHAIN_CACHE_PATH, "w") as f:
            json.dump(self._cache, f)

    # -- Main entry point ------------------------------------------------------

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "ingest")

        if action == "ingest":
            return self._ingest(context)
        elif action == "poll":
            return self._poll(context)
        elif action == "latest":
            return self._latest(context)
        elif action == "history":
            return self._history(context)
        elif action == "backfill_bars":
            return self._backfill_bars(context)
        elif action == "status":
            return self._status()
        elif action == "ensure_schema":
            return self._ensure_schema()
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    # -- Ingestion (chain + vol surface) ---------------------------------------

    def _ingest(self, context: Dict) -> AgentResult:
        """Fetch chain(s) and store a snapshot.

        Context keys:
            tickers: List[str] (default from config)
            source: str — 'orats' or 'ib' (default from config)
        """
        tickers = context.get("tickers", list(self.config.tickers))
        source = context.get("source", self.config.source)
        now = datetime.now(tz=timezone.utc)
        now_iso = now.isoformat()
        day_str = now.strftime("%Y-%m-%d")

        total_rows = 0
        errors = []

        for ticker in tickers:
            try:
                chains = self._fetch_chains(ticker, source)
                if not chains:
                    errors.append(f"No chain data for {ticker}")
                    continue

                rows = self._normalize_rows(chains, ticker, now_iso, source)
                if rows:
                    # Store to Supabase
                    if self.enabled:
                        inserted = self._batch_upsert_chains(rows)
                        total_rows += inserted
                    else:
                        total_rows += len(rows)

                    # Store to local cache
                    cache_key = f"chain_{ticker}"
                    if cache_key not in self._cache:
                        self._cache[cache_key] = {}
                    if day_str not in self._cache[cache_key]:
                        self._cache[cache_key][day_str] = []
                    self._cache[cache_key][day_str].append({
                        "time": now_iso,
                        "source": source,
                        "n_strikes": len(rows),
                    })

                    n_expiries = len(chains)
                    print(f"  [ChainIngest] {ticker}: {len(rows)} strikes "
                          f"({n_expiries} expiries) from {source}")

            except Exception as exc:
                errors.append(f"{ticker}: {exc}")

            # Vol surface snapshot (ORATS)
            if self._orats:
                self._snapshot_vol_surface(ticker, now_iso, day_str)

        self._save_cache()

        return self._result(
            success=total_rows > 0 or not errors,
            data={"rows_inserted": total_rows, "tickers": tickers},
            errors=errors,
            messages=[f"Stored {total_rows} strike rows for "
                      f"{len(tickers)} ticker(s)"],
        )

    def _fetch_chains(self, ticker: str, source: str) -> List[Dict]:
        """Fetch chains for all target expiries from the source.

        Returns list of {expiry: str, data: [strike_rows]}.
        """
        results = []

        if source == "ib" and self._ib and self._ib.is_connected:
            params = self._ib.option_params(ticker)
            expiries = self._filter_expiries(params.get("expirations", []))
            for exp in expiries:
                exp_fmt = f"{exp[:4]}-{exp[4:6]}-{exp[6:]}"
                chain = self._ib.option_chain(
                    ticker, exp_fmt,
                    strike_range=self.config.strike_range,
                )
                if chain and chain.get("data"):
                    results.append({"expiry": exp_fmt, "data": chain["data"]})

        elif source == "orats" and self._orats:
            resp = self._orats.chain(ticker, "")
            if resp and resp.get("data"):
                by_expiry: Dict[str, List] = {}
                for row in resp["data"]:
                    exp = row.get("expirDate", "")
                    by_expiry.setdefault(exp, []).append(row)

                for exp in self._filter_expiries(sorted(by_expiry.keys())):
                    results.append({"expiry": exp, "data": by_expiry[exp]})
        else:
            raise ValueError(
                f"Source '{source}' not available "
                f"(ib={'connected' if self._ib and self._ib.is_connected else 'no'}, "
                f"orats={'yes' if self._orats else 'no'})")

        return results

    def _filter_expiries(self, expiries: List[str]) -> List[str]:
        """Keep only the nearest N expiries within DTE limit."""
        today = date.today()
        valid = []
        for exp in sorted(expiries):
            exp_clean = exp.replace("-", "")
            if len(exp_clean) == 8:
                try:
                    exp_date = date(int(exp_clean[:4]), int(exp_clean[4:6]),
                                    int(exp_clean[6:]))
                except ValueError:
                    continue
                dte = (exp_date - today).days
                if 0 <= dte <= self.config.dte_max:
                    valid.append(exp if "-" in exp else
                                 f"{exp[:4]}-{exp[4:6]}-{exp[6:]}")
            if len(valid) >= self.config.max_expiries:
                break
        return valid

    def _normalize_rows(self, chains: List[Dict], ticker: str,
                        snapshot_time: str, source: str) -> List[Dict]:
        """Convert chain data to flat rows for chain_snapshots table."""
        rows = []
        for chain in chains:
            expiry = chain["expiry"]
            for r in chain["data"]:
                call_bid = r.get("callBidPrice")
                call_ask = r.get("callAskPrice")
                put_bid = r.get("putBidPrice")
                put_ask = r.get("putAskPrice")
                rows.append({
                    "snapshot_time": snapshot_time,
                    "ticker": ticker.upper(),
                    "expir_date": expiry,
                    "strike": r.get("strike", 0),
                    "stock_price": r.get("stockPrice"),
                    "call_bid": call_bid,
                    "call_ask": call_ask,
                    "call_mid": round((call_bid + call_ask) / 2, 4)
                    if call_bid is not None and call_ask is not None else None,
                    "call_iv": r.get("callSmvVol"),
                    "put_bid": put_bid,
                    "put_ask": put_ask,
                    "put_mid": round((put_bid + put_ask) / 2, 4)
                    if put_bid is not None and put_ask is not None else None,
                    "put_iv": r.get("putSmvVol"),
                    "delta": r.get("delta"),
                    "gamma": r.get("gamma"),
                    "theta": r.get("theta"),
                    "vega": r.get("vega"),
                    "source": source,
                })
        return rows

    def _batch_upsert_chains(self, rows: List[Dict],
                             batch_size: int = 500) -> int:
        """Upsert rows into chain_snapshots in batches."""
        inserted = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                self._db.client.table("chain_snapshots").upsert(
                    batch,
                    on_conflict="ticker,snapshot_time,expir_date,strike,source",
                ).execute()
                inserted += len(batch)
            except Exception as exc:
                print(f"  [ChainIngest] Batch upsert failed: {exc}")
        return inserted

    # -- Vol surface snapshot --------------------------------------------------

    def _snapshot_vol_surface(self, ticker: str, now_iso: str,
                              day_str: str) -> bool:
        """Take a vol surface snapshot from ORATS and store."""
        try:
            resp = self._orats.summaries(ticker)
            if not resp or not resp.get("data"):
                return False

            summary = resp["data"][0]
            summary["_snapshot_time"] = now_iso

            # Store to Supabase
            if self.enabled:
                self._db.log_vol_surface_snapshot(
                    ticker, summary, source="orats")

            # Store to local cache
            cache_key = f"vol_surface_{ticker}"
            if cache_key not in self._cache:
                self._cache[cache_key] = {}
            if day_str not in self._cache[cache_key]:
                self._cache[cache_key][day_str] = []
            self._cache[cache_key][day_str].append({
                "time": now_iso,
                "skewing": summary.get("skewing"),
                "contango": summary.get("contango"),
                "iv30d": summary.get("iv30d"),
                "stockPrice": summary.get("stockPrice"),
            })

            return True
        except Exception as exc:
            print(f"  {C.DIM}Vol surface snapshot failed for {ticker}: "
                  f"{exc}{C.RESET}")
            return False

    # -- IB Bar Backfill -------------------------------------------------------

    def _backfill_bars(self, context: Dict) -> AgentResult:
        """Backfill intraday bars from IB and store to Supabase + cache.

        Context keys:
            ticker: str (default "SPX")
            duration: str (default "30 D")
            bar_size: str (default "1 min")
        """
        if not self._ib or not self._ib.is_connected:
            return self._result(
                False, errors=["IB not connected for bar backfill"])

        ticker = context.get("ticker", "SPX")
        duration = context.get("duration", "30 D")
        bar_size = context.get("bar_size", "1 min")

        print(f"  Fetching {ticker} {bar_size} bars ({duration})...",
              flush=True)

        bars = self._ib.historical_bars(ticker, duration, bar_size)
        if not bars:
            return self._result(
                False, errors=[f"No bars returned for {ticker}"])

        # Store to Supabase
        if self.enabled:
            self._db.log_intraday_bars(ticker, bars, bar_size, source="ib")

        # Store to local cache
        cache_key = f"bars_{ticker}_{bar_size.replace(' ', '_')}"
        if cache_key not in self._cache:
            self._cache[cache_key] = {}
        for bar in bars:
            day = str(bar["date"])[:10]
            if day not in self._cache[cache_key]:
                self._cache[cache_key][day] = []
            self._cache[cache_key][day].append(bar)
        self._save_cache()

        msg = f"Stored {len(bars)} {bar_size} bars for {ticker}"
        print(f"  {C.GREEN}{msg}{C.RESET}")

        return self._result(True, data={
            "ticker": ticker,
            "bars": len(bars),
            "bar_size": bar_size,
            "duration": duration,
        }, messages=[msg])

    # -- Status ----------------------------------------------------------------

    def _status(self) -> AgentResult:
        """Report local cache contents."""
        summary = {}
        for key in sorted(self._cache.keys()):
            val = self._cache[key]
            if isinstance(val, dict):
                n_days = len(val)
                n_entries = sum(
                    len(v) if isinstance(v, list) else 1
                    for v in val.values()
                )
                summary[key] = {"days": n_days, "entries": n_entries}

        return self._result(True, data={
            "cache_path": CHAIN_CACHE_PATH,
            "cache": summary,
            "supabase_connected": self.enabled,
        })

    # -- Polling loop ----------------------------------------------------------

    def _poll(self, context: Dict) -> AgentResult:
        """Continuous polling — fetch and store at regular intervals.

        Context keys:
            tickers: List[str]
            source: str
            interval: int (seconds, default from config)
        """
        interval = context.get("interval", self.config.poll_interval)
        tickers = context.get("tickers", list(self.config.tickers))
        source = context.get("source", self.config.source)

        print(f"\n{C.BOLD}{C.CYAN}Chain Ingest — Polling Mode{C.RESET}")
        print(f"  Tickers:  {', '.join(tickers)}")
        print(f"  Source:   {source}")
        print(f"  Interval: {interval}s")
        print(f"  Max DTE:  {self.config.dte_max}")
        print(f"  Expiries: {self.config.max_expiries} nearest")
        if self.enabled:
            print(f"  {C.GREEN}Supabase: connected{C.RESET}")
        else:
            print(f"  {C.YELLOW}Supabase: not connected (local cache only){C.RESET}")
        print()

        n = 0
        try:
            while True:
                now = datetime.now()
                # Market hours check (9:30 - 16:00 ET, rough)
                if (now.hour < 9 or
                    (now.hour == 9 and now.minute < 30) or
                        now.hour >= 16):
                    if n == 0:
                        print(f"  {C.DIM}Outside market hours. "
                              f"Waiting...{C.RESET}")
                    time.sleep(60)
                    continue

                n += 1
                ts = now.strftime("%H:%M:%S")
                print(f"\n  [{ts}] Snapshot #{n}...")

                result = self._ingest({
                    "tickers": tickers,
                    "source": source,
                })

                if result.errors:
                    for e in result.errors:
                        print(f"    {C.YELLOW}{e}{C.RESET}")

                rows = result.data.get("rows_inserted", 0)
                print(f"    {rows} rows stored. Next in {interval}s "
                      f"(Ctrl+C to stop)")
                time.sleep(interval)

        except KeyboardInterrupt:
            self._save_cache()
            print(f"\n{C.BOLD}Chain ingest stopped after "
                  f"{n} snapshots.{C.RESET}")

        return self._result(success=True, data={"snapshots": n})

    # -- Query: latest ---------------------------------------------------------

    def _latest(self, context: Dict) -> AgentResult:
        """Query the most recent chain snapshot.

        Context keys:
            ticker: str
            expiry: str (optional — if omitted, returns all stored expiries)

        Returns:
            data["chain"] — ORATS-compatible {data: [{strike, delta, ...}]}
        """
        if not self.enabled:
            return self._result(
                False, errors=["Supabase not connected for queries"])

        ticker = context.get("ticker", "SPY")
        expiry = context.get("expiry")

        try:
            q = (self._db.client.table("chain_snapshots")
                 .select("snapshot_time")
                 .eq("ticker", ticker.upper())
                 .order("snapshot_time", desc=True)
                 .limit(1))
            if expiry:
                q = q.eq("expir_date", expiry)
            ts_resp = q.execute()

            if not ts_resp.data:
                return self._result(
                    success=False,
                    errors=[f"No chain data for {ticker}"
                            + (f" {expiry}" if expiry else "")],
                )

            latest_ts = ts_resp.data[0]["snapshot_time"]

            q2 = (self._db.client.table("chain_snapshots")
                  .select("*")
                  .eq("ticker", ticker.upper())
                  .eq("snapshot_time", latest_ts)
                  .order("strike"))
            if expiry:
                q2 = q2.eq("expir_date", expiry)
            rows = q2.execute().data or []

            chain = self._rows_to_chain(rows)

            return self._result(
                success=True,
                data={
                    "chain": chain,
                    "snapshot_time": latest_ts,
                    "rows": len(rows),
                    "ticker": ticker.upper(),
                },
            )
        except Exception as exc:
            return self._result(
                success=False,
                errors=[f"Query failed: {exc}"],
            )

    # -- Query: history --------------------------------------------------------

    def _history(self, context: Dict) -> AgentResult:
        """Query chain snapshots within a time range.

        Context keys:
            ticker: str
            expiry: str (optional)
            start: str — ISO timestamp or YYYY-MM-DD
            end: str — ISO timestamp or YYYY-MM-DD (default: now)

        Returns:
            data["snapshots"] — list of {snapshot_time, chain: {data: [...]}}
        """
        if not self.enabled:
            return self._result(
                False, errors=["Supabase not connected for queries"])

        ticker = context.get("ticker", "SPY")
        expiry = context.get("expiry")
        start = context.get("start",
                            (date.today() - timedelta(days=1)).isoformat())
        end = context.get("end",
                          datetime.now(tz=timezone.utc).isoformat())

        try:
            q = (self._db.client.table("chain_snapshots")
                 .select("*")
                 .eq("ticker", ticker.upper())
                 .gte("snapshot_time", start)
                 .lte("snapshot_time", end)
                 .order("snapshot_time")
                 .order("strike"))
            if expiry:
                q = q.eq("expir_date", expiry)

            rows = q.execute().data or []

            by_time: Dict[str, List] = {}
            for row in rows:
                ts = row["snapshot_time"]
                by_time.setdefault(ts, []).append(row)

            snapshots = []
            for ts in sorted(by_time.keys()):
                snapshots.append({
                    "snapshot_time": ts,
                    "chain": self._rows_to_chain(by_time[ts]),
                    "rows": len(by_time[ts]),
                })

            return self._result(
                success=True,
                data={
                    "snapshots": snapshots,
                    "total_snapshots": len(snapshots),
                    "total_rows": len(rows),
                    "ticker": ticker.upper(),
                },
            )
        except Exception as exc:
            return self._result(
                success=False,
                errors=[f"History query failed: {exc}"],
            )

    # -- Schema check ----------------------------------------------------------

    def _ensure_schema(self) -> AgentResult:
        """Check if required tables exist."""
        tables = ["chain_snapshots", "intraday_bars", "vol_surface_snapshots"]
        ok = []
        missing = []

        for table in tables:
            try:
                self._db.client.table(table).select("*").limit(0).execute()
                ok.append(table)
            except Exception:
                missing.append(table)

        if missing:
            return self._result(
                success=False,
                data={"ok": ok, "missing": missing},
                errors=[f"Missing tables: {', '.join(missing)}. "
                        f"Run schema.sql in Supabase SQL editor."],
            )

        return self._result(
            success=True,
            data={"ok": ok},
            messages=[f"All {len(tables)} tables exist"],
        )

    # -- Helpers ---------------------------------------------------------------

    @staticmethod
    def _rows_to_chain(rows: List[Dict]) -> Dict:
        """Convert Supabase rows back to ORATS-compatible chain format.

        Output matches the shape agents expect:
            {data: [{strike, expirDate, stockPrice, delta, gamma, theta, vega,
                      callBidPrice, callAskPrice, callSmvVol,
                      putBidPrice, putAskPrice, putSmvVol}]}
        """
        data = []
        for row in rows:
            data.append({
                "strike": float(row["strike"]),
                "expirDate": row["expir_date"],
                "stockPrice": float(row["stock_price"])
                if row.get("stock_price") else 0,
                "delta": float(row["delta"]) if row.get("delta") else 0,
                "gamma": float(row["gamma"]) if row.get("gamma") else 0,
                "theta": float(row["theta"]) if row.get("theta") else 0,
                "vega": float(row["vega"]) if row.get("vega") else 0,
                "callBidPrice": float(row["call_bid"])
                if row.get("call_bid") else 0,
                "callAskPrice": float(row["call_ask"])
                if row.get("call_ask") else 0,
                "callSmvVol": float(row["call_iv"])
                if row.get("call_iv") else 0,
                "putBidPrice": float(row["put_bid"])
                if row.get("put_bid") else 0,
                "putAskPrice": float(row["put_ask"])
                if row.get("put_ask") else 0,
                "putSmvVol": float(row["put_iv"])
                if row.get("put_iv") else 0,
            })
        return {"data": data}

    # -- Display ---------------------------------------------------------------

    @staticmethod
    def print_latest(result: AgentResult) -> None:
        """Pretty-print a latest chain query result."""
        if not result.success:
            for e in result.errors:
                print(f"  {C.RED}{e}{C.RESET}")
            return

        d = result.data
        chain_data = d["chain"]["data"]
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")
        print(f"  {C.BOLD}CHAIN SNAPSHOT — {d['ticker']}{C.RESET}")
        print(f"  Snapshot: {d['snapshot_time']}")
        print(f"  Strikes:  {d['rows']}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")

        if not chain_data:
            print("  No data")
            return

        by_exp: Dict[str, List] = {}
        for row in chain_data:
            exp = row.get("expirDate", "?")
            by_exp.setdefault(exp, []).append(row)

        for exp in sorted(by_exp.keys()):
            strikes = by_exp[exp]
            spot = strikes[0].get("stockPrice", 0)
            print(f"\n  {C.BOLD}{exp}{C.RESET} — {len(strikes)} strikes"
                  f"  spot=${spot:.2f}")
            print(f"  {'Strike':>8} {'Delta':>7} {'C.Bid':>7} {'C.Ask':>7}"
                  f" {'P.Bid':>7} {'P.Ask':>7} {'C.IV':>7}")
            print(f"  {'-' * 52}")
            for row in strikes:
                print(f"  {row['strike']:>8.1f}"
                      f" {row.get('delta', 0):>+7.3f}"
                      f" {row.get('callBidPrice', 0):>7.2f}"
                      f" {row.get('callAskPrice', 0):>7.2f}"
                      f" {row.get('putBidPrice', 0):>7.2f}"
                      f" {row.get('putAskPrice', 0):>7.2f}"
                      f" {row.get('callSmvVol', 0):>7.1%}")
        print()

    @staticmethod
    def print_history(result: AgentResult) -> None:
        """Pretty-print history query summary."""
        if not result.success:
            for e in result.errors:
                print(f"  {C.RED}{e}{C.RESET}")
            return

        d = result.data
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")
        print(f"  {C.BOLD}CHAIN HISTORY — {d['ticker']}{C.RESET}")
        print(f"  Snapshots: {d['total_snapshots']}")
        print(f"  Total rows: {d['total_rows']}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")

        for snap in d["snapshots"]:
            chain_data = snap["chain"]["data"]
            expiries = set(r.get("expirDate", "?") for r in chain_data)
            print(f"  {snap['snapshot_time']}"
                  f" — {snap['rows']} strikes"
                  f" — expiries: {', '.join(sorted(expiries))}")
        print()

    @staticmethod
    def print_status(result: AgentResult) -> None:
        """Pretty-print cache status."""
        d = result.data
        print(f"\n  {C.BOLD}Chain Ingest Cache{C.RESET}")
        print(f"  Path: {d.get('cache_path', '?')}")
        print(f"  Supabase: "
              f"{'connected' if d.get('supabase_connected') else 'not connected'}")
        cache = d.get("cache", {})
        if cache:
            for key, info in cache.items():
                print(f"    {key}: {info['days']} days, "
                      f"{info['entries']} entries")
        else:
            print("  (empty)")
        print()
