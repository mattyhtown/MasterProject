"""
APEX-SHARPE canonical types — single source of truth.

All agents import types from here. No duplicate definitions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"


class OrderAction(Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class SignalLevel(Enum):
    OK = "OK"
    INFO = "INFO"
    WARNING = "WARNING"
    ACTION = "ACTION"


class AlertSeverity(Enum):
    WARNING = "WARNING"
    ACTION = "ACTION"


class TradeStructure(Enum):
    """Available directional trade structures."""
    CALL_DEBIT_SPREAD = "Call Debit Spread"
    BULL_PUT_SPREAD = "Bull Put Spread"
    LONG_CALL = "Long Call"
    CALL_RATIO_SPREAD = "Call Ratio Spread"
    BROKEN_WING_BUTTERFLY = "Broken Wing Butterfly"


class SignalStrength(Enum):
    """Composite signal strength levels."""
    NONE = 0
    MODERATE = 2       # 2 core signals
    STRONG = 3         # 3 core signals
    VERY_STRONG = 4    # 4 core signals
    EXTREME = 5        # all 5 core signals


class SignalSystemType(Enum):
    """Types of signal systems that feed into the portfolio."""
    VOL_SURFACE = "vol_surface"           # Existing 0DTE fear bounce
    CREDIT_MARKET = "credit_market"       # HYG-TLT, IG/HY spreads
    MOMENTUM = "momentum"                 # MA crossovers, RSI, breadth
    MEAN_REVERSION = "mean_reversion"     # Bollinger, Z-score, VIX MR
    EVENT_DRIVEN = "event_driven"         # FOMC, CPI, NFP, earnings
    SEASONALITY = "seasonality"           # Monthly patterns, OPEX
    PAIRS = "pairs"                       # Pair trading, relative value
    MEME = "meme"                         # Social sentiment, unusual volume
    LSTM = "lstm"                         # Deep learning predictions
    POLITICAL = "political"              # Policy/geopolitical events


class PortfolioTier(Enum):
    """Capital deployment tiers."""
    TREASURY = "treasury"       # T-bills, money market (40-60%)
    LEAPS = "leaps"             # LEAPS/PMCC (20-30%)
    IRON_CONDOR = "iron_condor" # Monthly ICs (10-15%)
    DIRECTIONAL = "directional" # 0DTE signal-driven (5-10%)
    MARGIN_BUFFER = "margin"    # Reserve (10%)


# ---------------------------------------------------------------------------
# Agent result (standard return type for all agents)
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Standard result from any agent run."""
    agent_name: str
    timestamp: datetime
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ANSI colors (shared by all reporters)
# ---------------------------------------------------------------------------

class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
