"""Generates the daily markdown research report from all data sources."""

import warnings
warnings.filterwarnings("ignore")

from datetime import datetime
import os
import config
from data_fetcher import fetch_all_data
from portfolio import (gen_portfolio_section, load_portfolio, get_usdcad,
                       compute_positions, compute_actions, build_scores)


def fmt_mc(mc):
    if not mc: return "N/A"
    if mc >= 1e12: return f"${mc/1e12:.2f}T"
    if mc >= 1e9: return f"${mc/1e9:.1f}B"
    if mc >= 1e6: return f"${mc/1e6:.0f}M"
    return f"${mc:,.0f}"


def fmt(n):
    if n is None: return "N/A"
    if isinstance(n, float): return f"{n:.2f}"
    return str(n)


def fmt_rev(r):
    if not r: return "N/A"
    if r >= 1e9: return f"${r/1e9:.1f}B"
    if r >= 1e6: return f"${r/1e6:.0f}M"
    return f"${r:,.0f}"


def api_status():
    sources = ["yfinance (prices, fundamentals, options, earnings)"]
    if config.FINNHUB_API_KEY:
        sources.append("Finnhub (insider trades, news, analyst recs)")
    if config.FRED_API_KEY:
        sources.append("FRED (macro: Fed rate, yields, VIX)")
    if config.FMP_API_KEY:
        sources.append("FMP (analyst estimates, DCF, sector P/E)")
    sources.append("SEC EDGAR (Form 4, 13F filings)")
    sources.append("ta library (RSI, MACD, Bollinger, Stochastic, ADX, OBV, ATR)")
    missing = []
    if not config.FINNHUB_API_KEY: missing.append("FINNHUB_API_KEY")
    if not config.FRED_API_KEY: missing.append("FRED_API_KEY")
    if not config.FMP_API_KEY: missing.append("FMP_API_KEY")
    return sources, missing


GLOSSARY = """## 0. Glossary of Key Terms

### Price & Performance
| Term | Definition |
|------|-----------|
| **ATH** | All-Time High — highest price ever traded. |
| **52-Week Range** | Highest/lowest prices over past 12 months. |
| **Market Cap** | Share price x total shares outstanding. |
| **YTD / YoY** | Year-to-Date / Year-over-Year performance comparison. |
| **Beta** | Volatility vs. S&P 500. Beta > 1 = more volatile. |

### Technical Indicators
| Term | Definition |
|------|-----------|
| **RSI (14)** | Relative Strength Index. >70 = overbought, <30 = oversold, 40-60 = neutral. |
| **MACD** | Moving Average Convergence Divergence. Above signal = bullish, below = bearish. |
| **50/200-DMA** | 50/200-day moving average. Price above = uptrend. |
| **Golden/Death Cross** | 50-DMA crossing above/below 200-DMA. Major trend signals. |
| **Bollinger Bands** | 2 std dev bands around 20-DMA. Price near upper = extended, near lower = oversold. |
| **Stochastic (K/D)** | Momentum oscillator. >80 = overbought, <20 = oversold. |
| **ADX** | Average Directional Index. >25 = strong trend, <20 = weak/no trend. |
| **OBV** | On-Balance Volume. Rising = buying pressure, falling = selling pressure. |
| **ATR** | Average True Range. Measures daily price volatility in dollar terms. |
| **Support / Resistance** | Price floor (buyers) / ceiling (sellers) from recent 30-day range. |

### Valuation
| Term | Definition |
|------|-----------|
| **P/E / Forward P/E** | Price-to-earnings (trailing / estimated next year). |
| **P/S** | Price-to-sales. Useful for high-growth, low-profit companies. |
| **PEG** | P/E divided by growth rate. <1 = undervalued, >2 = expensive. |
| **DCF** | Discounted Cash Flow — intrinsic value based on future cash flows. |

### Options
| Term | Definition |
|------|-----------|
| **Put/Call Ratio** | Puts / Calls traded. <0.7 = bullish, >1.0 = bearish. |
| **IV (Implied Volatility)** | Market's expected price movement from options pricing. |
| **Open Interest** | Total outstanding option contracts at a strike price. |

### Macro & Institutional
| Term | Definition |
|------|-----------|
| **Fed Funds Rate** | Interest rate set by FOMC. Higher = pressure on growth stocks. |
| **10Y/2Y Treasury** | Government bond yields. Inverted (2Y > 10Y) = recession signal. |
| **VIX** | "Fear index" — S&P 500 implied volatility. >20 = elevated fear. |
| **13F Filing** | Quarterly SEC report showing institutional holdings (45-day delay). |
| **Form 4** | SEC filing for insider buys/sells (2-day reporting requirement). |

---
"""


def gen_stock_section(ticker, data):
    p = data["price"]
    t = data["technicals"]
    if "error" in p:
        return f"### {ticker}\nData unavailable: {p['error']}\n\n"

    price = p.get("price", "N/A")
    change = p.get("change_pct", 0)
    sign = "+" if change >= 0 else ""
    name = p.get("name", ticker)

    lines = [f"### {ticker} — {name} | ${price} ({sign}{change}%)"]
    lines.append(f"- **Market Cap:** {fmt_mc(p.get('market_cap'))} | **P/E:** {fmt(p.get('pe_ratio'))} | **Fwd P/E:** {fmt(p.get('forward_pe'))} | **P/S:** {fmt(p.get('ps_ratio'))} | **PEG:** {fmt(p.get('peg_ratio'))} | **Beta:** {fmt(p.get('beta'))}")
    lines.append(f"- **52-Week Range:** ${fmt(p.get('52w_low'))} – ${fmt(p.get('52w_high'))}")

    if "error" not in t:
        lines.append(f"- **RSI:** {t['rsi']} ({t['rsi_signal']}) | **MACD:** {t['macd_buy_sell']} | **ADX:** {t['adx']} ({t['adx_trend']} trend) | **Stoch:** {t['stoch_k']}/{t['stoch_d']}")
        lines.append(f"- **Bollinger:** ${t['bb_lower']:.0f} – ${t['bb_upper']:.0f} (mid ${t['bb_mid']:.0f}) | **ATR:** ${t['atr']:.2f} | **OBV:** {t['obv_trend']}")
        lines.append(f"- **Outlook:** {t['outlook']}")
        if t.get('golden_cross'):
            lines.append(f"- **ALERT: GOLDEN CROSS detected — strongly bullish long-term signal**")
        if t.get('death_cross'):
            lines.append(f"- **ALERT: DEATH CROSS detected — bearish long-term signal**")

    e = data.get("earnings", {})
    lines.append(f"- **Next Earnings:** {e.get('earnings_date', 'N/A')}")

    dcf = data.get("fmp_dcf", {})
    if dcf and dcf.get("dcf"):
        dcf_val = dcf["dcf"]
        updown = "undervalued" if dcf_val > price else "overvalued"
        lines.append(f"- **DCF Fair Value:** ${dcf_val:.2f} ({updown} by {abs(dcf_val - price)/price*100:.0f}%)")

    recs = data.get("analyst_recs", {})
    if recs:
        total = recs.get("strong_buy", 0) + recs.get("buy", 0) + recs.get("hold", 0) + recs.get("sell", 0) + recs.get("strong_sell", 0)
        if total > 0:
            lines.append(f"- **Analyst Consensus:** {recs.get('strong_buy',0)} Strong Buy, {recs.get('buy',0)} Buy, {recs.get('hold',0)} Hold, {recs.get('sell',0)} Sell, {recs.get('strong_sell',0)} Strong Sell")

    news_lines = []
    for n in data.get("news", [])[:3]:
        if n.get("headline"):
            news_lines.append(f"  - [{n['headline']}]({n.get('url', '#')}) ({n.get('source', '')}, {n.get('datetime', '')})")
    if news_lines:
        lines.append("- **Recent News:**")
        lines.extend(news_lines)

    return "\n".join(lines) + "\n\n"


def gen_technicals_table(stocks):
    rows = ["## 2. Technical Analysis Dashboard\n"]
    rows.append("| Ticker | Price | RSI | Signal | MACD | Stoch K/D | ADX | BB Position | OBV | Support | Resistance | Outlook |")
    rows.append("|--------|-------|-----|--------|------|-----------|-----|-------------|-----|---------|------------|---------|")
    for ticker, data in stocks.items():
        p, t = data["price"], data["technicals"]
        if "error" in p or "error" in t:
            rows.append(f"| {ticker} | N/A | — | — | — | — | — | — | — | — | — | — |")
            continue
        price = p["price"]
        bb_pos = "Upper" if price >= t["bb_upper"] * 0.98 else "Lower" if price <= t["bb_lower"] * 1.02 else "Middle"
        rows.append(
            f"| {ticker} | ${price} | {t['rsi']} | {t['rsi_signal']} | "
            f"{t['macd_buy_sell']} | {t['stoch_k']}/{t['stoch_d']} | "
            f"{t['adx']} ({t['adx_trend']}) | {bb_pos} | {t['obv_trend']} | "
            f"${t['support']} | ${t['resistance']} | {t['outlook']} |"
        )
    return "\n".join(rows) + "\n\n"


def gen_options_table(stocks):
    rows = ["## 3. Options Flow & Implied Volatility\n"]
    rows.append("| Ticker | Expiry | P/C Ratio | Sentiment | Call Vol | Put Vol | Call OI | Put OI | Avg Call IV | Avg Put IV |")
    rows.append("|--------|--------|-----------|-----------|---------|---------|---------|--------|-------------|------------|")
    for ticker, data in stocks.items():
        o = data["options"]
        if "error" in o:
            rows.append(f"| {ticker} | N/A | — | — | — | — | — | — | — | — |")
            continue
        rows.append(
            f"| {ticker} | {o.get('nearest_expiry', 'N/A')} | "
            f"{o.get('put_call_ratio', 'N/A')} | {o.get('sentiment', 'N/A')} | "
            f"{o.get('total_call_volume', 0):,} | {o.get('total_put_volume', 0):,} | "
            f"{o.get('total_call_oi', 0):,} | {o.get('total_put_oi', 0):,} | "
            f"{o.get('avg_call_iv', 'N/A')}% | {o.get('avg_put_iv', 'N/A')}% |"
        )

    rows.append("\n### Top Open Interest Strikes\n")
    for ticker, data in stocks.items():
        o = data["options"]
        if "error" in o: continue
        calls = o.get("top_call_strikes", [])
        puts = o.get("top_put_strikes", [])
        if calls or puts:
            parts = [f"**{ticker}:** "]
            if calls: parts.append("Calls: " + ", ".join([f"${c.get('strike',0):.0f}" for c in calls[:3]]))
            if puts: parts.append(" | Puts: " + ", ".join([f"${p.get('strike',0):.0f}" for p in puts[:3]]))
            rows.append("".join(parts))

    return "\n".join(rows) + "\n\n"


def gen_insider_section(stocks):
    rows = ["## 4. Insider Trading Activity\n"]

    # Finnhub data
    finnhub_rows = []
    for ticker, data in stocks.items():
        for trade in data.get("insider_finnhub", [])[:3]:
            finnhub_rows.append(
                f"| {ticker} | {trade.get('name', 'Unknown')} | {trade.get('transaction_type', 'N/A')} | "
                f"{abs(trade.get('change', 0)):,.0f} | {fmt_mc(trade.get('value', 0))} | {trade.get('transaction_date', 'N/A')} |"
            )

    if finnhub_rows:
        rows.append("### From Finnhub (Form 3/4/5)\n")
        rows.append("| Ticker | Insider | Type | Shares | Est. Value | Date |")
        rows.append("|--------|---------|------|--------|------------|------|")
        rows.extend(finnhub_rows)
    else:
        rows.append("*Finnhub insider data: Set FINNHUB_API_KEY in config.py for Form 4 data*\n")

    # SEC EDGAR data
    sec_rows = []
    for ticker, data in stocks.items():
        for filing in data.get("insider_sec", [])[:3]:
            company = filing.get("company", ticker)
            form = filing.get("form_type", "")
            date = filing.get("filing_date", "")
            url = filing.get("url", "")
            link = f"[View]({url})" if url else "—"
            sec_rows.append(f"| {ticker} | {company} | {form} | {date} | {link} |")

    if sec_rows:
        rows.append("\n### From SEC EDGAR (Direct Form 4 Filings)\n")
        rows.append("| Ticker | Company | Form | Filing Date | Link |")
        rows.append("|--------|---------|------|-------------|------|")
        rows.extend(sec_rows)

    return "\n".join(rows) + "\n\n"


def gen_earnings_table(stocks):
    rows = ["## 5. Earnings Calendar\n"]
    rows.append("| Ticker | Next Earnings | EPS Estimate | Revenue Estimate | FMP EPS Est (Avg) | FMP Rev Est (Avg) |")
    rows.append("|--------|---------------|--------------|------------------|-------------------|-------------------|")
    for ticker, data in stocks.items():
        e = data.get("earnings", {})
        fmp = data.get("fmp_estimates", {})
        rows.append(
            f"| {ticker} | {e.get('earnings_date', 'N/A')} | {fmt(e.get('eps_estimate'))} | "
            f"{fmt_rev(e.get('revenue_estimate'))} | {fmt(fmp.get('est_eps_avg'))} | "
            f"{fmt_rev(fmp.get('est_revenue_avg'))} |"
        )
    return "\n".join(rows) + "\n\n"


def gen_macro_section(macro):
    rows = ["## 6. Macro Risk Factors\n"]
    if not macro:
        rows.append("*Set FRED_API_KEY in config.py for live macro data (Fed rate, yields, VIX)*\n")
        return "\n".join(rows) + "\n"

    rows.append("| Indicator | Current | Previous | Date |")
    rows.append("|-----------|---------|----------|------|")
    labels = {
        "fed_funds_rate": "Fed Funds Rate",
        "10y_treasury": "10-Year Treasury",
        "2y_treasury": "2-Year Treasury",
        "cpi": "CPI (Consumer Price Index)",
        "unemployment": "Unemployment Rate",
        "vix": "VIX (Fear Index)",
    }
    for key, label in labels.items():
        d = macro.get(key, {})
        if d:
            val = d.get("value", "N/A")
            prev = d.get("prev_value", "N/A")
            date = d.get("date", "N/A")
            rows.append(f"| {label} | {val}% | {prev}% | {date} |")

    yield_10y = macro.get("10y_treasury", {}).get("value")
    yield_2y = macro.get("2y_treasury", {}).get("value")
    if yield_10y and yield_2y and yield_10y != "." and yield_2y != ".":
        spread = float(yield_10y) - float(yield_2y)
        signal = "INVERTED — Recession warning" if spread < 0 else "Normal"
        rows.append(f"\n**Yield Curve Spread (10Y - 2Y):** {spread:.2f}% — {signal}")

    vix = macro.get("vix", {}).get("value")
    if vix and vix != ".":
        vix_f = float(vix)
        level = "Extreme fear" if vix_f > 30 else "Elevated" if vix_f > 20 else "Low volatility" if vix_f < 15 else "Normal"
        rows.append(f"**VIX Level:** {vix_f:.1f} — {level}")

    return "\n".join(rows) + "\n\n"


def gen_valuation_table(stocks, sector_pe):
    rows = ["## 7. Valuation Multiples Comparison\n"]
    rows.append("| Ticker | Price | P/E | Fwd P/E | P/S | PEG | DCF Value | Market Cap | Verdict |")
    rows.append("|--------|-------|-----|---------|-----|-----|-----------|------------|---------|")
    for ticker, data in stocks.items():
        p = data["price"]
        if "error" in p:
            rows.append(f"| {ticker} | N/A | — | — | — | — | — | — | — |")
            continue
        price = p.get("price", 0)
        pe = p.get("pe_ratio")
        fpe = p.get("forward_pe")
        peg = p.get("peg_ratio")
        dcf = data.get("fmp_dcf", {}).get("dcf")
        dcf_str = f"${dcf:.0f}" if dcf else "N/A"

        if peg and peg < 1: verdict = "**Undervalued** (PEG<1)"
        elif dcf and dcf > price * 1.2: verdict = "**Undervalued** (DCF)"
        elif pe and fpe and fpe < pe * 0.8: verdict = "Earnings accelerating"
        elif peg and peg > 2.5: verdict = "Expensive"
        elif pe and pe > 80: verdict = "Premium"
        elif pe and pe < 25: verdict = "Attractive"
        else: verdict = "Fairly valued"

        rows.append(
            f"| {ticker} | ${price} | {fmt(pe)} | {fmt(fpe)} | {fmt(p.get('ps_ratio'))} | "
            f"{fmt(peg)} | {dcf_str} | {fmt_mc(p.get('market_cap'))} | {verdict} |"
        )

    if sector_pe:
        rows.append(f"\n### Sector Average P/E Ratios")
        rows.append("| Sector | Avg P/E |")
        rows.append("|--------|---------|")
        for sector, pe in sector_pe.items():
            rows.append(f"| {sector} | {fmt(pe)} |")

    return "\n".join(rows) + "\n\n"


def gen_correlation_section(stocks):
    betas = {t: d["price"].get("beta") for t, d in stocks.items() if d["price"].get("beta")}
    rows = ["## 8. Correlation & Portfolio Risk\n"]
    rows.append("### Sector Clusters")
    rows.append("| Cluster | Tickers | Risk |")
    rows.append("|---------|---------|------|")
    rows.append("| AI Infrastructure | NVDA, AVGO, GLW, TSM, AMD, INTC | High correlation — all move on AI sentiment |")
    rows.append("| Mega-Cap Tech | AAPL, MSFT, GOOGL, META, AMZN | Moderate — move together on macro/rates |")
    rows.append("| Enterprise Software | CRM, ORCL, ADBE, SNOW, PLTR | Moderate — AI disruption theme |")
    rows.append("| Other | TSLA, NFLX | Lower — idiosyncratic drivers |\n")

    rows.append("### Beta (Volatility vs. S&P 500)")
    rows.append("| Ticker | Beta | Risk Level |")
    rows.append("|--------|------|------------|")
    for t in sorted(betas, key=lambda x: betas[x], reverse=True):
        b = betas[t]
        risk = "HIGH" if b > 1.5 else "Moderate" if b > 1.0 else "Low"
        rows.append(f"| {t} | {b:.2f} | {risk} |")

    rows.append("\n### Portfolio Recommendations")
    rows.append("- This portfolio is **heavily concentrated in AI/semiconductor infrastructure**")
    rows.append("- Consider adding: healthcare, energy, financials, or international exposure")
    rows.append("- AI infrastructure cluster (NVDA, AVGO, GLW, TSM, AMD) moves as a block")

    return "\n".join(rows) + "\n\n"


def gen_summary_table(stocks):
    rows = ["## 9. Summary & Recommendations\n"]
    rows.append("| Ticker | Price | P/E | RSI | Technical | Options | Analyst | Rec | Confidence |")
    rows.append("|--------|-------|-----|-----|-----------|---------|---------|-----|------------|")

    buy_picks = []
    sell_picks = []

    for ticker, data in stocks.items():
        p, t, o = data["price"], data["technicals"], data["options"]
        if "error" in p: continue

        price = p["price"]
        pe = fmt(p.get("pe_ratio"))
        rsi = t.get("rsi", "N/A") if "error" not in t else "N/A"
        outlook = t.get("outlook", "N/A") if "error" not in t else "N/A"
        opt_sent = o.get("sentiment", "N/A") if "error" not in o else "N/A"

        recs = data.get("analyst_recs", {})
        if recs:
            buy_total = recs.get("strong_buy", 0) + recs.get("buy", 0)
            sell_total = recs.get("sell", 0) + recs.get("strong_sell", 0)
            hold_total = recs.get("hold", 0)
            analyst_str = f"{buy_total}B/{hold_total}H/{sell_total}S"
        else:
            analyst_str = "N/A"
            buy_total, sell_total, hold_total = 0, 0, 0

        rsi_val = t.get("rsi", 50) if "error" not in t else 50
        pe_val = p.get("pe_ratio") or 30
        peg = p.get("peg_ratio") or 1.5
        tech = t.get("outlook", "") if "error" not in t else ""

        score = 0
        if "Bullish" in tech and "extended" not in tech: score += 2
        elif "Bullish" in tech: score += 1
        elif "Bearish" in tech: score -= 2
        if rsi_val < 30: score += 2
        elif rsi_val < 45: score += 1
        elif rsi_val > 75: score -= 1
        if peg < 1: score += 2
        elif peg < 1.5: score += 1
        elif peg > 3: score -= 1
        if pe_val < 25: score += 1
        elif pe_val > 80: score -= 1
        if buy_total > sell_total + hold_total: score += 1
        if opt_sent == "Bullish": score += 1
        elif opt_sent == "Bearish": score -= 1

        if score >= 3:
            rec, conf = "**BUY**", "High"
            buy_picks.append((ticker, score, price, tech))
        elif score >= 1:
            rec, conf = "**BUY**", "Medium"
            buy_picks.append((ticker, score, price, tech))
        elif score <= -3:
            rec, conf = "**SELL**", "High"
            sell_picks.append((ticker, score, price, tech))
        elif score <= -1:
            rec, conf = "SELL", "Medium"
            sell_picks.append((ticker, score, price, tech))
        else:
            rec, conf = "HOLD", "Medium"

        rows.append(
            f"| {ticker} | ${price} | {pe} | {rsi} | {outlook} | {opt_sent} | {analyst_str} | {rec} | {conf} |"
        )

    buy_picks.sort(key=lambda x: -x[1])
    sell_picks.sort(key=lambda x: x[1])

    rows.append("\n### Top Conviction Buys")
    for t, s, p, tech in buy_picks[:4]:
        rows.append(f"- **{t}** (${p}) — {tech}, score {s}")

    rows.append("\n### Top Sells / Risks")
    for t, s, p, tech in sell_picks[:3]:
        rows.append(f"- **{t}** (${p}) — {tech}, score {s}")

    return "\n".join(rows) + "\n\n"


def gen_executive_summary(stocks: dict, macro: dict) -> str:
    """One-page TL;DR for the top of the report."""
    today = datetime.now().strftime("%Y-%m-%d")
    scores = build_scores(stocks)

    # Portfolio snapshot
    rows = load_portfolio()
    usdcad = get_usdcad()
    pstate = compute_positions(rows, stocks, usdcad)
    has_positions = any(p["shares"] > 0 for p in pstate["positions"])
    actions = compute_actions(pstate, stocks, scores) if has_positions else []

    # Score buckets
    buy_high = sorted(
        [(t, s["score"], stocks[t].get("price", {}).get("price"))
         for t, s in scores.items() if s["score"] >= 3 and t in config.PORTFOLIO_TARGETS],
        key=lambda x: -x[1])
    sell_signals = sorted(
        [(t, s["score"], s["label"])
         for t, s in scores.items() if s["score"] <= -1 and t in config.PORTFOLIO_TARGETS],
        key=lambda x: x[1])

    # Macro regime
    vix = (macro.get("vix") or {}).get("value", "N/A")
    ten_y = (macro.get("10y_treasury") or {}).get("value", "N/A")
    fed = (macro.get("fed_funds_rate") or {}).get("value", "N/A")
    try:
        vix_f = float(vix)
        regime = "Risk-On" if vix_f < 18 else ("Caution" if vix_f < 25 else "Risk-Off")
    except (ValueError, TypeError):
        regime = "N/A"

    # Upcoming earnings (next 5 calendar days) — for portfolio names only
    today_dt = datetime.now()
    upcoming = []
    for tk in config.PORTFOLIO_TARGETS:
        e = stocks.get(tk, {}).get("earnings", {})
        ed = e.get("earnings_date", "N/A")
        if ed and ed != "N/A":
            try:
                # earnings_date is sometimes "2026-05-20 00:00:00" or just date
                dt = datetime.strptime(str(ed)[:10], "%Y-%m-%d")
                days = (dt - today_dt).days
                if 0 <= days <= 5:
                    upcoming.append((tk, ed[:10], days))
            except Exception:
                pass
    upcoming.sort(key=lambda x: x[2])

    # Build the page
    md = f"""# Daily Tech Stock Research — Executive Summary

**Date:** {today}  |  **Market Regime:** {regime}  |  **USD/CAD:** {usdcad:.4f}

## Market Snapshot

| VIX | 10Y Treasury | Fed Funds |
|---|---|---|
| {vix} | {ten_y}% | {fed}% |

## Portfolio
"""
    if has_positions:
        pnl_sign = "+" if pstate["total_pnl_cad"] >= 0 else ""
        md += (f"**Value:** ${pstate['total_value_cad']:,.0f} CAD  |  "
               f"**P&L:** {pnl_sign}${pstate['total_pnl_cad']:,.0f} CAD "
               f"({pnl_sign}{pstate['total_pnl_pct']:.2f}%)  |  "
               f"**Cash:** ${pstate['cash_cad']:,.0f} CAD ({pstate['cash_actual_weight']*100:.1f}%)\n\n")
    else:
        md += "*Portfolio not yet opened. See Portfolio Management section for initial buy plan.*\n\n"

    md += "## Today's Top Actions\n\n"
    if actions:
        md += "| Ticker | Action |\n|---|---|\n"
        for tk, msg in actions[:5]:
            md += f"| **{tk}** | {msg} |\n"
        md += "\n"
    elif not has_positions:
        md += "*Open initial positions per the buy plan below.*\n\n"
    else:
        md += "*No actions today — holdings aligned with signals.*\n\n"

    md += "## Top BUY Signals (held names)\n\n"
    if buy_high:
        md += "| Ticker | Price (USD) | Score | Signal |\n|---|---|---|---|\n"
        for t, sc, px in buy_high[:6]:
            px_str = f"${px:.2f}" if px else "N/A"
            md += f"| {t} | {px_str} | {sc} | BUY High |\n"
        md += "\n"
    else:
        md += "*No BUY High signals among portfolio names today.*\n\n"

    md += "## Watch List (Trim / Sell signals)\n\n"
    if sell_signals:
        md += "| Ticker | Score | Signal |\n|---|---|---|\n"
        for t, sc, lbl in sell_signals[:5]:
            md += f"| {t} | {sc} | {lbl} |\n"
        md += "\n"
    else:
        md += "*No sell signals among portfolio names.*\n\n"

    md += "## Earnings in Next 5 Days\n\n"
    if upcoming:
        md += "| Ticker | Earnings Date | Days |\n|---|---|---|\n"
        for tk, ed, days in upcoming:
            md += f"| {tk} | {ed} | {days} |\n"
        md += "\n*Avoid opening new positions in these names until after the report.*\n\n"
    else:
        md += "*No portfolio names report earnings in the next 5 days.*\n\n"

    md += "## Table of Contents\n\n"
    md += (
        "1. [Executive Summary](#daily-tech-stock-research--executive-summary)\n"
        "2. [Glossary of Key Terms](#0-glossary-of-key-terms)\n"
        "3. [Individual Stock Analysis](#1-individual-stock-analysis)\n"
        "4. [Technical Analysis Dashboard](#2-technical-analysis-dashboard)\n"
        "5. [Options Flow & Implied Volatility](#3-options-flow--implied-volatility)\n"
        "6. [Insider Trading Activity](#4-insider-trading-activity)\n"
        "7. [Earnings Calendar](#5-earnings-calendar)\n"
        "8. [Macro Risk Factors](#6-macro-risk-factors)\n"
        "9. [Valuation Multiples Comparison](#7-valuation-multiples-comparison)\n"
        "10. [Correlation & Portfolio Risk](#8-correlation--portfolio-risk)\n"
        "11. [Summary & Recommendations](#9-summary--recommendations)\n"
        "12. [Portfolio Management](#portfolio-management-10k-cad-target)\n\n"
    )

    # Force page break in PDF; harmless in markdown viewers.
    md += '<div style="page-break-after: always;"></div>\n\n'
    return md


def generate_report(all_data: dict) -> str:
    stocks = all_data.get("stocks", all_data)
    macro = all_data.get("macro", {})
    sector_pe = all_data.get("sector_pe", {})

    today = datetime.now().strftime("%Y-%m-%d")
    sources, missing = api_status()

    # === Page 1: Executive Summary ===
    report = gen_executive_summary(stocks, macro)

    report += f"""# Daily Tech Stock Research Report — {today}

**Generated:** {today} {datetime.now().strftime("%H:%M")} ET | **Pipeline:** Automated

### Data Sources Connected
{chr(10).join(['- ' + s for s in sources])}

"""
    if missing:
        report += f"### Missing API Keys (add to config.py for more data)\n"
        report += "\n".join([f"- `{k}`" for k in missing]) + "\n\n"

    report += "---\n\n"
    report += GLOSSARY

    report += "## 1. Individual Stock Analysis\n\n"
    for ticker, data in stocks.items():
        report += gen_stock_section(ticker, data)

    report += "---\n\n" + gen_technicals_table(stocks)
    report += "---\n\n" + gen_options_table(stocks)
    report += "---\n\n" + gen_insider_section(stocks)
    report += "---\n\n" + gen_earnings_table(stocks)
    report += "---\n\n" + gen_macro_section(macro)
    report += "---\n\n" + gen_valuation_table(stocks, sector_pe)
    report += "---\n\n" + gen_correlation_section(stocks)
    report += "---\n\n" + gen_summary_table(stocks)
    report += "---\n\n" + gen_portfolio_section(stocks)

    report += """---

*Disclaimer: This is AI-generated research for informational purposes only, not financial advice. Always do your own due diligence.*

*Data: [yfinance](https://github.com/ranaroussi/yfinance) | [Finnhub](https://finnhub.io/) | [FRED](https://fred.stlouisfed.org/) | [FMP](https://site.financialmodelingprep.com/) | [SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar) | [ta](https://github.com/bukosabino/ta)*
"""
    return report


def run_pipeline():
    print(f"{'='*60}")
    print(f"DAILY TECH STOCK RESEARCH PIPELINE")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Tickers: {', '.join(config.TICKERS)}")
    print(f"{'='*60}\n")

    sources, missing = api_status()
    print("Connected APIs:")
    for s in sources:
        print(f"  [OK] {s}")
    if missing:
        print(f"\nMissing keys (optional): {', '.join(missing)}")
    print()

    print("Fetching data...")
    all_data = fetch_all_data(config.TICKERS)

    print("\nGenerating report...")
    report = generate_report(all_data)

    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = os.path.join(config.REPORTS_DIR, f"{today}-daily-tech-research-auto.md")
    with open(filepath, "w") as f:
        f.write(report)

    # Also save raw data as JSON for programmatic use
    import json
    json_path = os.path.join(config.REPORTS_DIR, f"{today}-data.json")
    with open(json_path, "w") as f:
        json.dump(all_data, f, indent=2, default=str)

    print(f"\nReport: {filepath} ({len(report):,} chars)")
    print(f"Data:   {json_path}")

    # PDF export (best-effort — won't fail the pipeline if WeasyPrint isn't installed)
    try:
        from pdf_export import markdown_to_pdf
        pdf_path = filepath.replace(".md", ".pdf")
        markdown_to_pdf(filepath, pdf_path)
        print(f"PDF:    {pdf_path} ({os.path.getsize(pdf_path):,} bytes)")
    except Exception as e:
        print(f"PDF:    skipped ({type(e).__name__}: {e})")

    return filepath


if __name__ == "__main__":
    run_pipeline()
