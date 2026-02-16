"""
DataScoutAgent — discovers and evaluates external datasets for ingestion.

Monitors public data sources for new tradeable signals:
  - FRED / World Bank economic indicators
  - NOAA climate and weather events
  - ACLED / GDELT conflict data
  - Social sentiment (Reddit, X/Twitter proxies)
  - Geopolitical risk indices
  - Shipping / supply chain data
  - Alternative data catalogs

For each source, evaluates:
  - Availability and update frequency
  - Historical depth
  - Potential alpha (does it predict returns?)
  - Integration effort
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


# Known external data sources and their metadata
EXTERNAL_SOURCES = {
    # Economic / Macro
    "fred_gdp": {
        "name": "FRED GDP (Real, Quarterly)",
        "category": "economic",
        "url": "https://fred.stlouisfed.org/series/GDPC1",
        "frequency": "quarterly",
        "history": "1947+",
        "format": "CSV/JSON API",
        "api_free": True,
        "relevance": "Leading indicator of recession, equity drawdowns",
        "integration": "LOW",
    },
    "fred_unemployment": {
        "name": "FRED Unemployment Rate",
        "category": "economic",
        "url": "https://fred.stlouisfed.org/series/UNRATE",
        "frequency": "monthly",
        "history": "1948+",
        "format": "CSV/JSON API",
        "api_free": True,
        "relevance": "Contrarian signal at extremes, regime indicator",
        "integration": "LOW",
    },
    "fred_cpi": {
        "name": "FRED CPI (All Items)",
        "category": "economic",
        "url": "https://fred.stlouisfed.org/series/CPIAUCSL",
        "frequency": "monthly",
        "history": "1947+",
        "format": "CSV/JSON API",
        "api_free": True,
        "relevance": "Inflation regime → Fed policy → rates → equities",
        "integration": "LOW",
    },
    "fred_fed_funds": {
        "name": "FRED Effective Fed Funds Rate",
        "category": "economic",
        "url": "https://fred.stlouisfed.org/series/FEDFUNDS",
        "frequency": "daily",
        "history": "1954+",
        "format": "CSV/JSON API",
        "api_free": True,
        "relevance": "Rate regime directly affects options pricing, carry trades",
        "integration": "LOW",
    },
    "fred_m2": {
        "name": "FRED M2 Money Supply",
        "category": "economic",
        "url": "https://fred.stlouisfed.org/series/M2SL",
        "frequency": "monthly",
        "history": "1959+",
        "format": "CSV/JSON API",
        "api_free": True,
        "relevance": "Liquidity proxy — M2 growth leads equity markets",
        "integration": "LOW",
    },
    "fred_initial_claims": {
        "name": "FRED Initial Jobless Claims",
        "category": "economic",
        "url": "https://fred.stlouisfed.org/series/ICSA",
        "frequency": "weekly",
        "history": "1967+",
        "format": "CSV/JSON API",
        "api_free": True,
        "relevance": "High-frequency recession signal, VIX correlation",
        "integration": "LOW",
    },
    # Climate / Weather
    "noaa_severe_weather": {
        "name": "NOAA Storm Events Database",
        "category": "climate",
        "url": "https://www.ncdc.noaa.gov/stormevents/",
        "frequency": "event-based",
        "history": "1950+",
        "format": "CSV bulk download",
        "api_free": True,
        "relevance": "Natural disasters → insurance stocks, energy prices, commodities",
        "integration": "MEDIUM",
    },
    "enso_index": {
        "name": "NOAA El Nino/La Nina (ONI Index)",
        "category": "climate",
        "url": "https://origin.cpc.ncep.noaa.gov/products/analysis_monitoring/ensostuff/ONI_v5.php",
        "frequency": "monthly",
        "history": "1950+",
        "format": "HTML table / text",
        "api_free": True,
        "relevance": "Agricultural commodities, energy demand patterns",
        "integration": "MEDIUM",
    },
    "global_temperature": {
        "name": "NASA GISS Global Temperature Anomaly",
        "category": "climate",
        "url": "https://data.giss.nasa.gov/gistemp/",
        "frequency": "monthly",
        "history": "1880+",
        "format": "CSV",
        "api_free": True,
        "relevance": "Long-term commodity and energy sector trends",
        "integration": "LOW",
    },
    # Conflict / Geopolitical
    "acled_conflicts": {
        "name": "ACLED Armed Conflict Data",
        "category": "conflict",
        "url": "https://acleddata.com/",
        "frequency": "weekly updates",
        "history": "1997+",
        "format": "CSV/API",
        "api_free": False,
        "relevance": "Oil price spikes, defense stocks, risk-off events",
        "integration": "MEDIUM",
    },
    "gdelt_events": {
        "name": "GDELT Global Events Database",
        "category": "conflict",
        "url": "https://www.gdeltproject.org/",
        "frequency": "15-min updates",
        "history": "1979+",
        "format": "CSV bulk / BigQuery",
        "api_free": True,
        "relevance": "Event-driven sentiment, geopolitical risk quantification",
        "integration": "HIGH",
    },
    "gpr_index": {
        "name": "Geopolitical Risk Index (Caldara & Iacoviello)",
        "category": "conflict",
        "url": "https://www.matteoiacoviello.com/gpr.htm",
        "frequency": "monthly / daily",
        "history": "1985+",
        "format": "Excel/CSV",
        "api_free": True,
        "relevance": "Predicts VIX spikes, gold rallies, equity drawdowns",
        "integration": "LOW",
    },
    # Sentiment / Social
    "aaii_sentiment": {
        "name": "AAII Investor Sentiment Survey",
        "category": "sentiment",
        "url": "https://www.aaii.com/sentimentsurvey",
        "frequency": "weekly",
        "history": "1987+",
        "format": "CSV download",
        "api_free": True,
        "relevance": "Contrarian signal — extreme bearish = buying opportunity",
        "integration": "LOW",
    },
    "put_call_ratio": {
        "name": "CBOE Put/Call Ratio",
        "category": "sentiment",
        "url": "https://www.cboe.com/us/options/market_statistics/",
        "frequency": "daily",
        "history": "2006+",
        "format": "CSV",
        "api_free": True,
        "relevance": "Options sentiment — high P/C = fear = contrarian buy",
        "integration": "LOW",
    },
    "reddit_wsb": {
        "name": "Reddit WallStreetBets Ticker Mentions",
        "category": "sentiment",
        "url": "Various APIs (PushShift, Reddit API)",
        "frequency": "real-time",
        "history": "2019+",
        "format": "JSON API",
        "api_free": True,
        "relevance": "Meme stock early detection, unusual volume predictor",
        "integration": "HIGH",
    },
    "fear_greed_index": {
        "name": "CNN Fear & Greed Index",
        "category": "sentiment",
        "url": "https://edition.cnn.com/markets/fear-and-greed",
        "frequency": "daily",
        "history": "2012+",
        "format": "Web scrape",
        "api_free": True,
        "relevance": "Composite sentiment — extremes predict mean reversion",
        "integration": "MEDIUM",
    },
    # Supply Chain / Trade
    "baltic_dry": {
        "name": "Baltic Dry Index",
        "category": "supply_chain",
        "url": "https://fred.stlouisfed.org/series/DBDI",
        "frequency": "daily",
        "history": "1985+",
        "format": "CSV/JSON (FRED)",
        "api_free": True,
        "relevance": "Global trade activity proxy, leads industrial production",
        "integration": "LOW",
    },
    "container_freight": {
        "name": "Freightos Baltic Index (Container Rates)",
        "category": "supply_chain",
        "url": "https://fbx.freightos.com/",
        "frequency": "weekly",
        "history": "2016+",
        "format": "CSV/API",
        "api_free": False,
        "relevance": "Supply chain bottlenecks → inflation → retail stocks",
        "integration": "MEDIUM",
    },
    # Alternative
    "satellite_nightlights": {
        "name": "VIIRS Nighttime Lights (World Bank)",
        "category": "alternative",
        "url": "https://eogdata.mines.edu/products/vnl/",
        "frequency": "monthly",
        "history": "2012+",
        "format": "GeoTIFF / CSV aggregated",
        "api_free": True,
        "relevance": "Economic activity proxy for emerging markets",
        "integration": "HIGH",
    },
    "epa_emissions": {
        "name": "EPA Greenhouse Gas Reporting",
        "category": "alternative",
        "url": "https://ghgdata.epa.gov/",
        "frequency": "annual",
        "history": "2010+",
        "format": "CSV",
        "api_free": True,
        "relevance": "Carbon regulation risk for energy sector",
        "integration": "MEDIUM",
    },
    "usda_crop_reports": {
        "name": "USDA World Ag Supply/Demand (WASDE)",
        "category": "alternative",
        "url": "https://usda.library.cornell.edu/concern/publications/3t945q76s",
        "frequency": "monthly",
        "history": "1973+",
        "format": "PDF / structured data",
        "api_free": True,
        "relevance": "Commodity futures (corn, wheat, soy) event catalyst",
        "integration": "MEDIUM",
    },
}


class DataScoutAgent(BaseAgent):
    """Discovers and evaluates external datasets for potential alpha."""

    def __init__(self, config=None):
        super().__init__("DataScout", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "catalog")

        if action == "catalog":
            return self._catalog(context.get("category", ""))
        elif action == "recommend":
            return self._recommend(
                context.get("loader"),
                context.get("priority", "alpha"),
            )
        elif action == "evaluate":
            return self._evaluate(
                context.get("source_id", ""),
                context.get("loader"),
            )
        elif action == "gaps":
            return self._coverage_gaps(context.get("loader"))
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _catalog(self, category: str) -> AgentResult:
        """List all known external data sources."""
        sources = []
        for source_id, info in EXTERNAL_SOURCES.items():
            if category and info["category"] != category:
                continue
            sources.append({
                "id": source_id,
                **info,
            })

        # Group by category
        by_category = {}
        for s in sources:
            by_category.setdefault(s["category"], []).append(s)

        return self._result(
            success=True,
            data={
                "sources": sources,
                "total": len(sources),
                "by_category": {k: len(v) for k, v in by_category.items()},
                "categories": list(by_category.keys()),
                "free_sources": sum(1 for s in sources if s["api_free"]),
            },
        )

    def _recommend(self, loader, priority: str) -> AgentResult:
        """Recommend which external datasets to add next."""
        recommendations = []

        # Evaluate each source
        for source_id, info in EXTERNAL_SOURCES.items():
            score = 0
            reasons = []

            # Free API = lower barrier
            if info["api_free"]:
                score += 2
                reasons.append("Free API access")

            # Low integration effort
            if info["integration"] == "LOW":
                score += 3
                reasons.append("Easy integration (CSV/API)")
            elif info["integration"] == "MEDIUM":
                score += 1

            # High frequency = more actionable
            if info["frequency"] in ("daily", "weekly"):
                score += 2
                reasons.append(f"High frequency ({info['frequency']})")
            elif info["frequency"] in ("real-time", "15-min updates"):
                score += 3
                reasons.append("Real-time data")

            # Deep history
            if "+" in info.get("history", ""):
                start_year = int(info["history"].replace("+", ""))
                if start_year < 2000:
                    score += 2
                    reasons.append(f"Deep history ({info['history']})")

            # Relevance to options trading
            rel = info.get("relevance", "").lower()
            if any(w in rel for w in ["vix", "options", "volatility", "premium"]):
                score += 3
                reasons.append("Directly relevant to options")
            elif any(w in rel for w in ["equity", "stocks", "market"]):
                score += 2
                reasons.append("Equity market relevant")

            # Check if we already have related data
            if loader:
                if info["category"] == "economic":
                    # We have some FRED data already
                    score -= 1

            # Priority-based weighting
            if priority == "alpha":
                if info["category"] in ("sentiment", "conflict", "alternative"):
                    score += 2
            elif priority == "reliability":
                if info["category"] in ("economic",):
                    score += 2
            elif priority == "novelty":
                if info["category"] in ("climate", "supply_chain", "alternative"):
                    score += 3

            recommendations.append({
                "id": source_id,
                "name": info["name"],
                "category": info["category"],
                "score": score,
                "reasons": reasons,
                "integration": info["integration"],
                "free": info["api_free"],
                "relevance": info["relevance"],
            })

        recommendations.sort(key=lambda r: r["score"], reverse=True)

        return self._result(
            success=True,
            data={
                "recommendations": recommendations[:15],
                "priority": priority,
                "top_pick": recommendations[0] if recommendations else None,
            },
        )

    def _evaluate(self, source_id: str, loader) -> AgentResult:
        """Deep evaluation of a specific external data source."""
        if source_id not in EXTERNAL_SOURCES:
            return self._result(success=False,
                                errors=[f"Unknown source: {source_id}"])

        info = EXTERNAL_SOURCES[source_id]

        # Build evaluation
        evaluation = {
            **info,
            "id": source_id,
            "pros": [],
            "cons": [],
            "alpha_potential": "UNKNOWN",
            "next_steps": [],
        }

        # Pros
        if info["api_free"]:
            evaluation["pros"].append("Free API — no licensing costs")
        if info["integration"] == "LOW":
            evaluation["pros"].append("Simple CSV/API integration — can add in <1 hour")
        if info["frequency"] in ("daily", "weekly", "real-time"):
            evaluation["pros"].append(f"High update frequency ({info['frequency']})")

        history = info.get("history", "")
        if "+" in history:
            start_year = int(history.replace("+", ""))
            years = 2026 - start_year
            evaluation["pros"].append(f"{years}+ years of history for backtesting")

        # Cons
        if not info["api_free"]:
            evaluation["cons"].append("Requires paid API access")
        if info["integration"] == "HIGH":
            evaluation["cons"].append("Complex integration (scraping, BigQuery, etc.)")
        if info["frequency"] in ("monthly", "quarterly", "annual"):
            evaluation["cons"].append(f"Low frequency ({info['frequency']}) — limited for short-term trading")

        # Alpha potential assessment
        rel = info.get("relevance", "").lower()
        if any(w in rel for w in ["contrarian", "predicts", "leads"]):
            evaluation["alpha_potential"] = "HIGH"
        elif any(w in rel for w in ["correlation", "proxy", "indicator"]):
            evaluation["alpha_potential"] = "MEDIUM"
        else:
            evaluation["alpha_potential"] = "LOW"

        # Next steps
        evaluation["next_steps"] = [
            f"Download sample data from {info['url']}",
            "Load into HistoricalLoader format (date, value columns)",
            "Compute correlation with SPY/VIX returns",
            "Test predictive power: does it lead equity returns?",
            "If promising, add to NoveltyAgent lead/lag scan",
        ]

        # Check what we already have that's related
        if loader:
            if info["category"] == "economic":
                evaluation["existing_overlap"] = (
                    "We have FRED US indicators and World Bank data "
                    "in market_data/data/economic_indicators/"
                )
            elif info["category"] == "sentiment":
                evaluation["existing_overlap"] = (
                    "We have credit spread (HYG-TLT) as a fear proxy. "
                    "This would add a complementary sentiment signal."
                )
            else:
                evaluation["existing_overlap"] = "No existing data in this category"

        return self._result(success=True, data=evaluation)

    def _coverage_gaps(self, loader) -> AgentResult:
        """Identify gaps between what we have and what's available."""
        gaps = {
            "have": [],
            "missing": [],
            "priority_missing": [],
        }

        # What we have
        if loader:
            available = loader.available_tickers()
            for cls, tickers in available.items():
                gaps["have"].append({
                    "category": cls,
                    "count": len(tickers),
                    "type": "price_data",
                })

            # Check economic indicators
            econ_dir = loader.data_dir / "economic_indicators"
            if econ_dir.exists():
                econ_files = list(econ_dir.glob("*.csv"))
                gaps["have"].append({
                    "category": "economic_indicators",
                    "count": len(econ_files),
                    "type": "macro_data",
                })

        # What we're missing
        missing_categories = {
            "sentiment": {
                "description": "Market sentiment (AAII, put/call ratio, Fear & Greed)",
                "impact": "HIGH — contrarian signals at extremes predict mean reversion",
                "sources": ["aaii_sentiment", "put_call_ratio", "fear_greed_index"],
            },
            "climate_events": {
                "description": "Climate and severe weather events",
                "impact": "MEDIUM — commodity and energy sector catalysts",
                "sources": ["noaa_severe_weather", "enso_index"],
            },
            "geopolitical": {
                "description": "Conflict data and geopolitical risk",
                "impact": "HIGH — predicts VIX spikes and risk-off events",
                "sources": ["gpr_index", "acled_conflicts"],
            },
            "supply_chain": {
                "description": "Shipping and trade flow data",
                "impact": "MEDIUM — inflation and industrial production signals",
                "sources": ["baltic_dry", "container_freight"],
            },
            "high_freq_macro": {
                "description": "High-frequency economic data (weekly claims, daily rates)",
                "impact": "HIGH — most actionable for short-term trading",
                "sources": ["fred_initial_claims", "fred_fed_funds"],
            },
            "social_sentiment": {
                "description": "Social media sentiment and unusual activity",
                "impact": "MEDIUM — meme stock detection, retail flow proxy",
                "sources": ["reddit_wsb"],
            },
        }

        for cat, info in missing_categories.items():
            gaps["missing"].append({
                "category": cat,
                **info,
            })

        # Priority: easiest to add with highest impact
        priority = [
            g for g in gaps["missing"]
            if g["impact"] == "HIGH"
        ]
        gaps["priority_missing"] = priority

        return self._result(
            success=True,
            data=gaps,
        )

    def print_catalog(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  EXTERNAL DATA CATALOG")
        print(f"{'='*74}{C.RESET}")
        print(f"  Total sources: {d.get('total', 0)}"
              f" ({d.get('free_sources', 0)} free)")

        by_cat = d.get("by_category", {})
        if by_cat:
            print(f"  Categories: {', '.join(f'{k}({v})' for k, v in by_cat.items())}")

        sources = d.get("sources", [])
        current_cat = ""
        for s in sources:
            if s["category"] != current_cat:
                current_cat = s["category"]
                print(f"\n  {C.CYAN}{current_cat.upper()}{C.RESET}")

            free = f"{C.GREEN}FREE{C.RESET}" if s["api_free"] else f"{C.RED}PAID{C.RESET}"
            effort = s["integration"]
            print(f"    {s['name']:<45} {free}  effort:{effort}")
            print(f"      {C.DIM}{s['relevance']}{C.RESET}")
        print()

    def print_recommend(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  DATA SCOUT RECOMMENDATIONS (priority: {d.get('priority', '?')})")
        print(f"{'='*74}{C.RESET}")

        recs = d.get("recommendations", [])
        for i, r in enumerate(recs, 1):
            score_clr = C.GREEN if r["score"] >= 8 else C.YELLOW if r["score"] >= 5 else C.DIM
            free = "FREE" if r["free"] else "PAID"
            print(f"\n  {score_clr}#{i} [{r['score']}]{C.RESET}"
                  f" {r['name']} ({r['category']})")
            print(f"      {free} | effort: {r['integration']}")
            print(f"      {C.DIM}{r['relevance']}{C.RESET}")
            if r["reasons"]:
                print(f"      Reasons: {', '.join(r['reasons'])}")
        print()

    def print_gaps(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  DATA COVERAGE GAPS")
        print(f"{'='*74}{C.RESET}")

        have = d.get("have", [])
        if have:
            print(f"\n  {C.GREEN}HAVE:{C.RESET}")
            for h in have:
                print(f"    {h['category']:<25} {h['count']:>5} items ({h['type']})")

        missing = d.get("missing", [])
        if missing:
            print(f"\n  {C.RED}MISSING:{C.RESET}")
            for m in missing:
                impact_clr = C.RED if m["impact"] == "HIGH" else C.YELLOW
                print(f"    {m['category']:<25} {impact_clr}impact: {m['impact']}{C.RESET}")
                print(f"      {m['description']}")
                print(f"      Sources: {', '.join(m['sources'])}")

        priority = d.get("priority_missing", [])
        if priority:
            print(f"\n  {C.BOLD}TOP PRIORITY:{C.RESET} {', '.join(p['category'] for p in priority)}")
        print()
