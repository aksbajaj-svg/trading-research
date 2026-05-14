"""Fetches stock data from yfinance, Finnhub, FRED, SEC EDGAR, and FMP.

Optimizations:
- Per-ticker work runs in a ThreadPoolExecutor
- Each ticker fetches its yfinance history ONCE and reuses it for price,
  technicals, options, and earnings
- SEC submissions JSON is fetched once per ticker and shared between
  Form 4 and 13F extraction
- CIK map is preloaded at module import
- FMP auto-disables after first 403 (saves ~36 wasted calls per run)
- FRED series fetched in parallel
"""

import warnings
warnings.filterwarnings("ignore")

import json as _json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.request import urlopen, Request

import pandas as pd
import ta
import yfinance as yf

import config

try:
    import finnhub
    _finnhub_client = finnhub.Client(api_key=config.FINNHUB_API_KEY) if config.FINNHUB_API_KEY else None
except Exception:
    _finnhub_client = None

# Runtime flags
_fmp_disabled = False  # flips to True on first 403


# ============================================================
# CIK PRELOAD (SEC EDGAR)
# ============================================================

_cik_cache = {}

def _preload_cik_map() -> None:
    if _cik_cache:
        return
    try:
        req = Request("https://www.sec.gov/files/company_tickers.json",
                      headers=config.SEC_EDGAR_HEADERS)
        with urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        for val in data.values():
            _cik_cache[val["ticker"]] = str(val["cik_str"]).zfill(10)
    except Exception as e:
        print(f"  [warn] CIK preload failed: {e}")

_preload_cik_map()


# ============================================================
# YFINANCE — bundled per-ticker fetch
# ============================================================

def _bundle_yfinance(ticker: str) -> dict:
    """One yfinance call per ticker, returns price + technicals + options + earnings."""
    out = {"price": {}, "technicals": {}, "options": {}, "earnings": {}}
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=f"{config.LOOKBACK_DAYS}d")
        if hist.empty:
            err = {"ticker": ticker, "error": "No data"}
            return {"price": err, "technicals": err, "options": err, "earnings": err}

        info = stock.info or {}

        # --- PRICE ---
        current = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2] if len(hist) > 1 else current
        change_pct = ((current - prev) / prev) * 100
        out["price"] = {
            "ticker": ticker,
            "price": round(current, 2),
            "change_pct": round(change_pct, 2),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
            "forward_pe": info.get("forwardPE"),
            "ps_ratio": info.get("priceToSalesTrailing12Months"),
            "peg_ratio": info.get("pegRatio"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "50dma": info.get("fiftyDayAverage"),
            "200dma": info.get("twoHundredDayAverage"),
            "volume": info.get("volume"),
            "avg_volume": info.get("averageVolume"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "eps": info.get("trailingEps"),
            "revenue": info.get("totalRevenue"),
            "sector": info.get("sector", "Technology"),
            "name": info.get("shortName", ticker),
        }

        # --- TECHNICALS ---
        if len(hist) >= config.SMA_LONG:
            out["technicals"] = _compute_technicals(ticker, hist)
        else:
            out["technicals"] = {"ticker": ticker, "error": "Insufficient history"}

        # --- OPTIONS ---
        out["options"] = _compute_options(ticker, stock)

        # --- EARNINGS ---
        out["earnings"] = _compute_earnings(ticker, stock)

    except Exception as e:
        err = {"ticker": ticker, "error": str(e)}
        for k in out:
            if not out[k]:
                out[k] = err
    return out


def _compute_technicals(ticker: str, hist: pd.DataFrame) -> dict:
    try:
        close, high, low, volume = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
        rsi_val = ta.momentum.RSIIndicator(close, window=config.RSI_PERIOD).rsi().iloc[-1]

        macd_ind = ta.trend.MACD(close, window_slow=config.MACD_SLOW,
                                  window_fast=config.MACD_FAST,
                                  window_sign=config.MACD_SIGNAL)
        macd_val = macd_ind.macd().iloc[-1]
        macd_signal = macd_ind.macd_signal().iloc[-1]
        macd_diff = macd_ind.macd_diff().iloc[-1]

        sma_50 = close.rolling(window=config.SMA_SHORT).mean().iloc[-1]
        sma_200 = close.rolling(window=config.SMA_LONG).mean().iloc[-1]
        sma_20 = close.rolling(window=20).mean().iloc[-1]
        ema_12 = close.ewm(span=12).mean().iloc[-1]
        current_price = close.iloc[-1]

        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper, bb_lower, bb_mid = bb.bollinger_hband().iloc[-1], bb.bollinger_lband().iloc[-1], bb.bollinger_mavg().iloc[-1]

        stoch = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)
        stoch_k, stoch_d = stoch.stoch().iloc[-1], stoch.stoch_signal().iloc[-1]

        adx_val = ta.trend.ADXIndicator(high, low, close, window=14).adx().iloc[-1]

        obv_series = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
        obv_trend = "Rising" if obv_series.iloc[-1] > obv_series.iloc[-5] else "Falling"

        atr_val = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1]

        recent_30d = hist.tail(30)
        support, resistance = round(recent_30d["Low"].min(), 2), round(recent_30d["High"].max(), 2)

        prev_sma_50 = close.rolling(window=config.SMA_SHORT).mean().iloc[-2]
        prev_sma_200 = close.rolling(window=config.SMA_LONG).mean().iloc[-2]
        golden_cross = prev_sma_50 <= prev_sma_200 and sma_50 > sma_200
        death_cross = prev_sma_50 >= prev_sma_200 and sma_50 < sma_200

        if rsi_val > 70: rsi_signal = "Overbought"
        elif rsi_val < 30: rsi_signal = "Oversold"
        elif rsi_val > 60: rsi_signal = "Mod. Bullish"
        elif rsi_val < 40: rsi_signal = "Mod. Bearish"
        else: rsi_signal = "Neutral"

        macd_buy_sell = "Buy" if macd_diff > 0 else "Sell"
        sma50_signal = "Buy" if current_price > sma_50 else "Sell"
        sma200_signal = "Buy" if current_price > sma_200 else "Sell"

        if golden_cross: outlook = "GOLDEN CROSS — Strongly Bullish"
        elif death_cross: outlook = "DEATH CROSS — Strongly Bearish"
        elif sma50_signal == "Buy" and sma200_signal == "Buy":
            outlook = "Bullish but extended" if rsi_val > 70 else "Bullish"
        elif sma50_signal == "Sell" and sma200_signal == "Sell":
            outlook = "Bearish"
        else:
            outlook = "Mixed / Transitioning"

        return {
            "ticker": ticker,
            "rsi": round(rsi_val, 1), "rsi_signal": rsi_signal,
            "macd": round(macd_val, 3), "macd_signal_line": round(macd_signal, 3),
            "macd_histogram": round(macd_diff, 3), "macd_buy_sell": macd_buy_sell,
            "sma_20": round(sma_20, 2), "sma_50": round(sma_50, 2),
            "sma_200": round(sma_200, 2), "ema_12": round(ema_12, 2),
            "sma50_signal": sma50_signal, "sma200_signal": sma200_signal,
            "golden_cross": golden_cross, "death_cross": death_cross,
            "bb_upper": round(bb_upper, 2), "bb_lower": round(bb_lower, 2),
            "bb_mid": round(bb_mid, 2),
            "stoch_k": round(stoch_k, 1), "stoch_d": round(stoch_d, 1),
            "adx": round(adx_val, 1),
            "adx_trend": "Strong" if adx_val > 25 else "Weak",
            "obv_trend": obv_trend, "atr": round(atr_val, 2),
            "support": support, "resistance": resistance, "outlook": outlook,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def _compute_options(ticker: str, stock: yf.Ticker) -> dict:
    try:
        expirations = stock.options
        if not expirations:
            return {"ticker": ticker, "error": "No options data"}
        nearest_exp = expirations[0]
        chain = stock.option_chain(nearest_exp)
        calls, puts = chain.calls, chain.puts

        total_call_vol = calls["volume"].sum() if "volume" in calls else 0
        total_put_vol = puts["volume"].sum() if "volume" in puts else 0
        pc_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else None
        total_call_oi = calls["openInterest"].sum() if "openInterest" in calls else 0
        total_put_oi = puts["openInterest"].sum() if "openInterest" in puts else 0
        avg_call_iv = calls["impliedVolatility"].mean() if "impliedVolatility" in calls else None
        avg_put_iv = puts["impliedVolatility"].mean() if "impliedVolatility" in puts else None

        top_call_oi = calls.nlargest(3, "openInterest")[["strike", "openInterest", "volume"]].to_dict("records") if "openInterest" in calls else []
        top_put_oi = puts.nlargest(3, "openInterest")[["strike", "openInterest", "volume"]].to_dict("records") if "openInterest" in puts else []

        sentiment = "N/A"
        if pc_ratio is not None:
            sentiment = "Bullish" if pc_ratio < 0.7 else "Bearish" if pc_ratio > 1.0 else "Neutral"

        return {
            "ticker": ticker, "nearest_expiry": nearest_exp,
            "put_call_ratio": pc_ratio, "sentiment": sentiment,
            "total_call_volume": int(total_call_vol) if not pd.isna(total_call_vol) else 0,
            "total_put_volume": int(total_put_vol) if not pd.isna(total_put_vol) else 0,
            "total_call_oi": int(total_call_oi) if not pd.isna(total_call_oi) else 0,
            "total_put_oi": int(total_put_oi) if not pd.isna(total_put_oi) else 0,
            "avg_call_iv": round(avg_call_iv * 100, 1) if avg_call_iv and not pd.isna(avg_call_iv) else None,
            "avg_put_iv": round(avg_put_iv * 100, 1) if avg_put_iv and not pd.isna(avg_put_iv) else None,
            "top_call_strikes": top_call_oi, "top_put_strikes": top_put_oi,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def _compute_earnings(ticker: str, stock: yf.Ticker) -> dict:
    try:
        cal = stock.calendar
        if cal is None:
            return {"ticker": ticker, "earnings_date": "N/A"}
        if isinstance(cal, dict):
            earnings_date = cal.get("Earnings Date")
            if isinstance(earnings_date, list) and earnings_date:
                earnings_date = str(earnings_date[0])
            return {
                "ticker": ticker,
                "earnings_date": str(earnings_date) if earnings_date else "N/A",
                "eps_estimate": cal.get("EPS Estimate"),
                "revenue_estimate": cal.get("Revenue Estimate"),
            }
        if isinstance(cal, pd.DataFrame) and not cal.empty:
            return {"ticker": ticker, "earnings_date": str(cal.iloc[0, 0])}
        return {"ticker": ticker, "earnings_date": "N/A"}
    except Exception:
        return {"ticker": ticker, "earnings_date": "N/A"}


# ============================================================
# FINNHUB
# ============================================================

def fetch_insider_trades(ticker: str) -> list:
    if not _finnhub_client:
        return []
    try:
        today = datetime.now()
        from_date = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        trades = _finnhub_client.stock_insider_transactions(ticker, from_date, to_date)
        return [{
            "name": t.get("name", "Unknown"),
            "share": t.get("share", 0),
            "change": t.get("change", 0),
            "transaction_type": t.get("transactionType", ""),
            "transaction_date": t.get("transactionDate", ""),
            "value": abs(t.get("change", 0)) * (t.get("transactionPrice", 0) or 0),
        } for t in (trades.get("data", []) or [])[:10]]
    except Exception:
        return []


def fetch_company_news(ticker: str) -> list:
    if not _finnhub_client:
        return []
    try:
        today = datetime.now()
        from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        news = _finnhub_client.company_news(ticker, _from=from_date, to=to_date)
        return [{
            "headline": n.get("headline", ""),
            "source": n.get("source", ""),
            "url": n.get("url", ""),
            "datetime": datetime.fromtimestamp(n.get("datetime", 0)).strftime("%Y-%m-%d"),
        } for n in (news or [])[:5]]
    except Exception:
        return []


def fetch_analyst_recommendations(ticker: str) -> dict:
    if not _finnhub_client:
        return {}
    try:
        recs = _finnhub_client.recommendation_trends(ticker)
        if recs:
            latest = recs[0]
            return {
                "ticker": ticker,
                "period": latest.get("period", ""),
                "strong_buy": latest.get("strongBuy", 0),
                "buy": latest.get("buy", 0),
                "hold": latest.get("hold", 0),
                "sell": latest.get("sell", 0),
                "strong_sell": latest.get("strongSell", 0),
            }
        return {}
    except Exception:
        return {}


# ============================================================
# FRED
# ============================================================

def _fetch_fred_series(series_id: str) -> dict:
    if not config.FRED_API_KEY:
        return {}
    try:
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={config.FRED_API_KEY}"
               f"&file_type=json&sort_order=desc&limit=5")
        req = Request(url, headers={"User-Agent": "TradingResearch/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        obs = data.get("observations", [])
        if obs:
            latest = obs[0]
            prev = obs[1] if len(obs) > 1 else latest
            return {
                "value": latest.get("value", "N/A"),
                "date": latest.get("date", "N/A"),
                "prev_value": prev.get("value", "N/A"),
                "prev_date": prev.get("date", "N/A"),
            }
        return {}
    except Exception:
        return {}


def fetch_macro_data() -> dict:
    results = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_fred_series, sid): name
                   for name, sid in config.FRED_SERIES.items()}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return results


# ============================================================
# SEC EDGAR — share submissions JSON between Form 4 + 13F
# ============================================================

def _fetch_sec_submissions(ticker: str) -> dict:
    cik = _cik_cache.get(ticker)
    if not cik:
        return {}
    try:
        req = Request(f"https://data.sec.gov/submissions/CIK{cik}.json",
                      headers=config.SEC_EDGAR_HEADERS)
        with urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read())
    except Exception:
        return {}


def _extract_form4(ticker: str, submissions: dict) -> list:
    cik = _cik_cache.get(ticker, "")
    company_name = submissions.get("name", ticker)
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    descs = recent.get("primaryDocDescription", [])
    accessions = recent.get("accessionNumber", [])
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    results = []
    for i in range(len(forms)):
        if forms[i] in ("4", "4/A") and dates[i] >= cutoff:
            acc_clean = accessions[i].replace("-", "")
            results.append({
                "company": company_name,
                "form_type": forms[i],
                "filing_date": dates[i],
                "document": docs[i],
                "description": descs[i] if i < len(descs) else "",
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{docs[i]}",
            })
            if len(results) >= 10:
                break
    return results


def _extract_13f(ticker: str, submissions: dict) -> list:
    company_name = submissions.get("name", ticker)
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    results = []
    for i in range(len(forms)):
        if "13F" in forms[i] and dates[i] >= cutoff:
            results.append({
                "company": company_name,
                "form_type": forms[i],
                "filing_date": dates[i],
                "accession": accessions[i],
            })
            if len(results) >= 5:
                break
    return results


# Backward-compat wrappers (used elsewhere)
def fetch_sec_insider_filings(ticker: str) -> list:
    return _extract_form4(ticker, _fetch_sec_submissions(ticker))

def fetch_13f_filings(ticker: str) -> list:
    return _extract_13f(ticker, _fetch_sec_submissions(ticker))


# ============================================================
# FMP — auto-disables after first 403
# ============================================================

def _fmp_get(url: str):
    global _fmp_disabled
    if _fmp_disabled or not config.FMP_API_KEY:
        return None
    try:
        req = Request(url, headers={"User-Agent": "TradingResearch/1.0"})
        with urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        # urllib raises HTTPError for 403
        msg = str(e)
        if "403" in msg or "401" in msg:
            _fmp_disabled = True
            print(f"  [warn] FMP returned 403 — disabling for this run")
        return None


def fetch_fmp_analyst_estimates(ticker: str) -> dict:
    data = _fmp_get(f"https://financialmodelingprep.com/api/v3/analyst-estimates/{ticker}?limit=1&apikey={config.FMP_API_KEY}")
    if not data:
        return {}
    est = data[0]
    return {
        "ticker": ticker, "date": est.get("date", ""),
        "est_revenue_avg": est.get("estimatedRevenueAvg"),
        "est_revenue_high": est.get("estimatedRevenueHigh"),
        "est_revenue_low": est.get("estimatedRevenueLow"),
        "est_eps_avg": est.get("estimatedEpsAvg"),
        "est_eps_high": est.get("estimatedEpsHigh"),
        "est_eps_low": est.get("estimatedEpsLow"),
    }


def fetch_fmp_dcf(ticker: str) -> dict:
    data = _fmp_get(f"https://financialmodelingprep.com/api/v3/discounted-cash-flow/{ticker}?apikey={config.FMP_API_KEY}")
    if not data:
        return {}
    return {"ticker": ticker, "dcf": data[0].get("dcf"), "stock_price": data[0].get("Stock Price")}


def fetch_sector_pe() -> dict:
    data = _fmp_get(f"https://financialmodelingprep.com/api/v4/sector_price_earning_ratio?apikey={config.FMP_API_KEY}")
    if not data:
        return {}
    return {item.get("sector", ""): item.get("pe", "") for item in data}


# ============================================================
# PER-TICKER WORKER
# ============================================================

def _fetch_ticker(ticker: str):
    yf_bundle = _bundle_yfinance(ticker)
    sec_subs = _fetch_sec_submissions(ticker)
    return ticker, {
        "price": yf_bundle["price"],
        "technicals": yf_bundle["technicals"],
        "options": yf_bundle["options"],
        "earnings": yf_bundle["earnings"],
        "insider_finnhub": fetch_insider_trades(ticker),
        "insider_sec": _extract_form4(ticker, sec_subs),
        "filings_13f": _extract_13f(ticker, sec_subs),
        "news": fetch_company_news(ticker),
        "analyst_recs": fetch_analyst_recommendations(ticker),
        "fmp_estimates": fetch_fmp_analyst_estimates(ticker),
        "fmp_dcf": fetch_fmp_dcf(ticker),
    }


# ============================================================
# COMBINED FETCH (parallelized)
# ============================================================

def fetch_all_data(tickers: list = None, max_workers: int = 8) -> dict:
    if tickers is None:
        tickers = config.TICKERS

    t0 = time.time()
    print(f"  Fetching {len(tickers)} tickers in parallel (workers={max_workers})...")

    data = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_ticker, tk): tk for tk in tickers}
        for fut in as_completed(futures):
            tk = futures[fut]
            try:
                _, payload = fut.result()
                data[tk] = payload
                print(f"  ✓ {tk}")
            except Exception as e:
                data[tk] = {"error": str(e)}
                print(f"  ✗ {tk}: {e}")

    print(f"  Stocks done in {time.time() - t0:.1f}s. Fetching macro + sector...")
    macro = fetch_macro_data()
    sector_pe = fetch_sector_pe()

    print(f"  Total pipeline time: {time.time() - t0:.1f}s")
    return {"stocks": data, "macro": macro, "sector_pe": sector_pe}


# Backward-compat single-ticker fetchers (used by report_generator if needed)
def fetch_price_data(ticker: str) -> dict:
    return _bundle_yfinance(ticker)["price"]

def fetch_technical_indicators(ticker: str) -> dict:
    return _bundle_yfinance(ticker)["technicals"]

def fetch_options_data(ticker: str) -> dict:
    return _bundle_yfinance(ticker)["options"]

def fetch_earnings_calendar(ticker: str) -> dict:
    return _bundle_yfinance(ticker)["earnings"]


if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL", "MSFT"]
    result = fetch_all_data(tickers)
    print(_json.dumps(result, indent=2, default=str)[:2000])
