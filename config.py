"""Configuration for the daily tech stock research pipeline."""

import os

# Load .env for local development. GitHub Actions sets env vars directly.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
# API KEYS — read from environment (.env locally, GitHub Secrets in CI)
# ============================================================
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")        # https://finnhub.io/register
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")  # https://www.alphavantage.co/support/#api-key
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")              # https://fred.stlouisfed.org/docs/api/api_key.html
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")                # https://site.financialmodelingprep.com/

# ============================================================
# TICKERS & COMPANIES
# ============================================================
TICKERS_TECH = [
    "NVDA", "AAPL", "GLW", "META", "MSFT", "GOOGL", "AMZN", "TSLA",
    "AVGO", "AMD", "CRM", "ORCL", "ADBE", "NFLX", "INTC", "SNOW", "PLTR",
]

# Healthcare leaders — large-cap pharma, biotech, devices, services
TICKERS_HEALTHCARE = ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ISRG"]

# Energy leaders — integrated majors, E&P, oilfield services
TICKERS_ENERGY = ["XOM", "CVX", "COP", "SLB", "EOG"]

# Financials — money-center banks, IBs, payments, asset mgmt
TICKERS_FINANCIALS = ["JPM", "BAC", "GS", "MS", "V", "MA", "BLK", "BRK-B"]

# International exposure — non-US listings & ADRs across geos/sectors
# TSM (Taiwan semis), ASML (Netherlands semicap), NVO (Denmark pharma),
# SAP (Germany software), TM (Japan autos), BABA (China e-commerce),
# SHOP (Canada e-commerce)
TICKERS_INTL = ["TSM", "ASML", "NVO", "SAP", "TM", "BABA", "SHOP"]

TICKERS = (
    TICKERS_TECH
    + TICKERS_HEALTHCARE
    + TICKERS_ENERGY
    + TICKERS_FINANCIALS
    + TICKERS_INTL
)

# Reverse map: ticker -> sector bucket (used by report grouping)
SECTOR_BUCKETS = {}
for _bucket, _list in [
    ("Technology", TICKERS_TECH),
    ("Healthcare", TICKERS_HEALTHCARE),
    ("Energy", TICKERS_ENERGY),
    ("Financials", TICKERS_FINANCIALS),
    ("International", TICKERS_INTL),
]:
    for _t in _list:
        SECTOR_BUCKETS[_t] = _bucket

PRIVATE_COMPANIES = ["Anthropic", "OpenAI", "Databricks"]

REPORTS_DIR = "reports"

# ============================================================
# PORTFOLIO
# Target weights total 1.00. CASH bucket holds the dry-powder reserve.
# Actual holdings are tracked in portfolio.csv — update shares/avg_cost
# after each trade. Report compares actual vs. these targets.
# ============================================================
TOTAL_CAPITAL_CAD = 10000
PORTFOLIO_CSV = "portfolio.csv"
USDCAD_TICKER = "CAD=X"  # yfinance symbol for USD/CAD spot

PORTFOLIO_TARGETS = {
    # Tech / AI (52%)
    "NVDA": 0.07, "MSFT": 0.06, "GOOGL": 0.06, "META": 0.05, "AMZN": 0.05,
    "AAPL": 0.05, "AVGO": 0.05, "AMD": 0.04, "PLTR": 0.04, "ORCL": 0.03, "CRM": 0.02,
    # Healthcare (13%)
    "LLY": 0.06, "UNH": 0.04, "ISRG": 0.03,
    # Financials (13%)
    "JPM": 0.05, "V": 0.04, "BRK-B": 0.04,
    # Energy (8%)
    "XOM": 0.04, "CVX": 0.04,
    # International (10%)
    "TSM": 0.04, "ASML": 0.03, "NVO": 0.03,
    # Cash buffer (4%)
    "CASH": 0.04,
}

# ============================================================
# TECHNICAL ANALYSIS PARAMETERS
# ============================================================
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SMA_SHORT = 50
SMA_LONG = 200
LOOKBACK_DAYS = 250

# ============================================================
# SEC EDGAR (no key needed)
# ============================================================
SEC_EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index?q="
SEC_EDGAR_HEADERS = {"User-Agent": "TradingResearch research@example.com"}

# ============================================================
# FRED ECONOMIC SERIES
# ============================================================
FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "10y_treasury": "DGS10",
    "2y_treasury": "DGS2",
    "cpi": "CPIAUCSL",
    "unemployment": "UNRATE",
    "vix": "VIXCLS",
}
