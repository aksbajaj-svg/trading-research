"""Quick connectivity check for all data sources."""
import sys, time, requests, yfinance as yf
from config import (FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, FRED_API_KEY,
                    FMP_API_KEY, SEC_EDGAR_HEADERS)

def t(label, fn):
    start = time.time()
    try:
        ok, detail = fn()
        ms = int((time.time() - start) * 1000)
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {label:<16} {ms:>5}ms  {detail}")
        return ok
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        print(f"  [FAIL] {label:<16} {ms:>5}ms  {type(e).__name__}: {e}")
        return False

def check_yf():
    px = yf.Ticker("NVDA").history(period="5d")
    return (not px.empty, f"NVDA last close ${px['Close'].iloc[-1]:.2f}")

def check_finnhub():
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": "NVDA", "token": FINNHUB_API_KEY}, timeout=10)
    j = r.json()
    return (r.ok and "c" in j, f"NVDA ${j.get('c')}")

def check_av():
    r = requests.get("https://www.alphavantage.co/query",
                     params={"function": "GLOBAL_QUOTE", "symbol": "NVDA",
                             "apikey": ALPHA_VANTAGE_API_KEY}, timeout=10)
    j = r.json()
    return ("Global Quote" in j, f"{list(j.keys())[:2]}")

def check_fred():
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": "DGS10", "api_key": FRED_API_KEY,
                             "file_type": "json", "sort_order": "desc", "limit": 1},
                     timeout=10)
    j = r.json()
    val = j["observations"][0]["value"]
    return (r.ok, f"10Y Treasury {val}%")

def check_fmp():
    r = requests.get(f"https://financialmodelingprep.com/api/v3/quote/NVDA",
                     params={"apikey": FMP_API_KEY}, timeout=10)
    return (r.ok, f"HTTP {r.status_code}")

def check_sec():
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=SEC_EDGAR_HEADERS, timeout=10)
    return (r.ok, f"{len(r.json())} tickers indexed")

def check_ta():
    import ta
    return (True, f"v{ta.__version__ if hasattr(ta, '__version__') else 'installed'}")

print("\n=== API HEALTH CHECK ===")
checks = [("yfinance", check_yf), ("Finnhub", check_finnhub),
          ("AlphaVantage", check_av), ("FRED", check_fred), ("FMP", check_fmp),
          ("SEC EDGAR", check_sec), ("ta lib", check_ta)]
results = [t(name, fn) for name, fn in checks]
ok = sum(results); total = len(results)
print(f"\n  Result: {ok}/{total} sources working\n")
sys.exit(0 if ok == total else 1)
