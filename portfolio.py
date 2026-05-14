"""Portfolio tracking: drift vs target, P&L, daily action recommendations.

Reads portfolio.csv (ticker, shares, avg_cost in stock's trade currency — USD
for US listings, native for non-CAD ADRs), pulls live USD/CAD FX, computes
position values in CAD, and emits a markdown section for the daily report.

Action rules are tied to the scoring engine in report_generator (BUY High,
BUY Medium, HOLD, SELL Medium, SELL High) plus drift thresholds.
"""

import csv
import os
from typing import Optional

import yfinance as yf

import config


def load_portfolio() -> list:
    """Return list of dicts: {ticker, shares, avg_cost}."""
    rows = []
    if not os.path.exists(config.PORTFOLIO_CSV):
        return rows
    with open(config.PORTFOLIO_CSV) as f:
        for r in csv.DictReader(f):
            rows.append({
                "ticker": r["ticker"].strip(),
                "shares": float(r["shares"] or 0),
                "avg_cost": float(r["avg_cost"] or 0),
            })
    return rows


def get_usdcad() -> float:
    """Live USD/CAD spot. Falls back to 1.37 if yfinance is unavailable."""
    try:
        fx = yf.Ticker(config.USDCAD_TICKER).history(period="2d")
        if not fx.empty:
            return float(fx["Close"].iloc[-1])
    except Exception:
        pass
    return 1.37


def compute_positions(rows: list, stocks_data: dict, usdcad: float) -> dict:
    """Returns dict with positions list, totals, drift info."""
    positions = []
    total_value_cad = 0.0
    cash_cad = 0.0

    for r in rows:
        tk = r["ticker"]
        if tk == "CASH":
            cash_cad = r["shares"]  # CASH "shares" stores CAD amount directly
            continue

        price_usd = stocks_data.get(tk, {}).get("price", {}).get("price")
        if price_usd is None:
            continue

        value_usd = r["shares"] * price_usd
        value_cad = value_usd * usdcad
        cost_usd = r["shares"] * r["avg_cost"]
        pnl_usd = value_usd - cost_usd
        pnl_pct = (pnl_usd / cost_usd * 100) if cost_usd > 0 else 0.0

        positions.append({
            "ticker": tk,
            "shares": r["shares"],
            "avg_cost": r["avg_cost"],
            "price": price_usd,
            "value_cad": value_cad,
            "cost_usd": cost_usd,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
        })
        total_value_cad += value_cad

    total_value_cad += cash_cad
    capital = config.TOTAL_CAPITAL_CAD

    for p in positions:
        p["actual_weight"] = (p["value_cad"] / total_value_cad) if total_value_cad > 0 else 0
        p["target_weight"] = config.PORTFOLIO_TARGETS.get(p["ticker"], 0)
        p["drift"] = p["actual_weight"] - p["target_weight"]
        p["target_cad"] = p["target_weight"] * capital
        p["gap_cad"] = p["target_cad"] - p["value_cad"]

    cash_target_weight = config.PORTFOLIO_TARGETS.get("CASH", 0)
    cash_actual_weight = (cash_cad / total_value_cad) if total_value_cad > 0 else 0

    return {
        "positions": positions,
        "cash_cad": cash_cad,
        "cash_actual_weight": cash_actual_weight,
        "cash_target_weight": cash_target_weight,
        "total_value_cad": total_value_cad,
        "capital_cad": capital,
        "total_pnl_cad": total_value_cad - capital,
        "total_pnl_pct": ((total_value_cad - capital) / capital * 100) if capital > 0 else 0,
        "usdcad": usdcad,
    }


def _score_to_label(score: int) -> str:
    if score >= 3: return "BUY High"
    if score >= 1: return "BUY Medium"
    if score <= -3: return "SELL High"
    if score <= -1: return "SELL Medium"
    return "HOLD"


def compute_actions(portfolio_state: dict, stocks_data: dict, scores: dict) -> list:
    """Return a list of recommended actions for today.

    Rules (must stay in sync with daily mgmt rules):
    - score >= 3 (BUY High) and under target -> "Add up to gap"
    - score <= -3 (SELL High) and holding   -> "Trim 50%"
    - score <= -1 (SELL Medium) and holding -> "Trim 25%"
    - RSI > 80 and pnl_pct > 25            -> "Take 20% profit"
    - golden_cross True and under target    -> "Add to target"
    - death_cross True and holding          -> "Trim 50%, reassess"
    - earnings within 5 days                -> "Hold — event risk"
    - position drift > 50% of target weight -> "Rebalance"
    - cash < 2% of total                    -> "Trim winners"
    - cash > 8% of total                    -> "Deploy into top BUY"
    - position pnl < -20%                   -> "Stop-loss review"
    """
    actions = []
    cash_pct = portfolio_state["cash_actual_weight"] * 100

    # Cash buffer signals first
    if cash_pct < 2:
        actions.append(("PORTFOLIO", "Cash buffer low (<2%) — trim names most over target."))
    elif cash_pct > 8:
        top_buy = None
        for tk, s in scores.items():
            if s["score"] >= 3 and tk in config.PORTFOLIO_TARGETS:
                top_buy = tk
                break
        if top_buy:
            actions.append(("PORTFOLIO", f"Cash buffer high (>{cash_pct:.1f}%) — deploy into {top_buy} (BUY High)."))

    by_ticker = {p["ticker"]: p for p in portfolio_state["positions"]}

    for tk in config.PORTFOLIO_TARGETS:
        if tk == "CASH":
            continue
        s = scores.get(tk, {})
        score = s.get("score", 0)
        label = _score_to_label(score)
        pos = by_ticker.get(tk)
        holding = pos is not None and pos["shares"] > 0
        target_cad = config.PORTFOLIO_TARGETS[tk] * portfolio_state["capital_cad"]

        tech = stocks_data.get(tk, {}).get("technicals", {})
        rsi = tech.get("rsi") or 0
        golden = tech.get("golden_cross", False)
        death = tech.get("death_cross", False)

        # Stop-loss review
        if holding and pos["pnl_pct"] <= -20:
            actions.append((tk, f"STOP-LOSS REVIEW — position down {pos['pnl_pct']:.1f}%. Cut or double down with rationale."))
            continue

        # Death cross overrides
        if death and holding:
            actions.append((tk, f"DEATH CROSS — trim 50% (current {pos['value_cad']:.0f} CAD). Reassess at next earnings."))
            continue

        # SELL High
        if score <= -3 and holding:
            actions.append((tk, f"SELL High ({label}, score {score}) — trim 50% (~{pos['value_cad']*0.5:.0f} CAD)."))
            continue

        # Take profit on overbought winners
        if holding and rsi > 80 and pos["pnl_pct"] > 25:
            actions.append((tk, f"RSI {rsi} + up {pos['pnl_pct']:.1f}% — take 20% partial profit (~{pos['value_cad']*0.2:.0f} CAD)."))
            continue

        # SELL Medium
        if score <= -1 and holding:
            actions.append((tk, f"SELL Medium (score {score}) — trim 25% (~{pos['value_cad']*0.25:.0f} CAD)."))
            continue

        # Golden cross
        if golden and (not holding or pos["actual_weight"] < pos["target_weight"]):
            gap = target_cad - (pos["value_cad"] if pos else 0)
            actions.append((tk, f"GOLDEN CROSS — add to target weight (~{gap:.0f} CAD to fill)."))
            continue

        # BUY High under-allocated
        if score >= 3 and (not holding or pos["actual_weight"] < pos["target_weight"]):
            gap = target_cad - (pos["value_cad"] if pos else 0)
            add = gap * 0.25
            actions.append((tk, f"BUY High (score {score}) — add 25% of gap (~{add:.0f} CAD; gap {gap:.0f} CAD)."))
            continue

        # Drift rebalance (only if already holding something meaningful)
        if holding and abs(pos["drift"]) > 0.5 * pos["target_weight"] and pos["target_weight"] > 0:
            direction = "Trim" if pos["drift"] > 0 else "Top up"
            actions.append((tk, f"REBALANCE — {direction}. Actual {pos['actual_weight']*100:.1f}% vs target {pos['target_weight']*100:.1f}%."))

    return actions


def _compute_score_local(data: dict) -> int:
    """Mirror of report_generator's scoring — kept inline so portfolio works standalone."""
    tech = data.get("technicals", {}) or {}
    price = data.get("price", {}) or {}
    opts = data.get("options", {}) or {}
    recs = data.get("analyst_recs", {}) or {}

    outlook = tech.get("outlook", "") or ""
    rsi_val = tech.get("rsi") or 50
    peg = price.get("peg_ratio") or 999
    pe_val = price.get("pe_ratio") or 0
    opt_sent = opts.get("sentiment", "")
    buy_total = (recs.get("strong_buy", 0) or 0) + (recs.get("buy", 0) or 0)
    sell_total = (recs.get("strong_sell", 0) or 0) + (recs.get("sell", 0) or 0)
    hold_total = recs.get("hold", 0) or 0

    score = 0
    if "Bullish" in outlook and "extended" not in outlook: score += 2
    elif "Bullish" in outlook: score += 1
    elif "Bearish" in outlook: score -= 2
    if rsi_val < 30: score += 2
    elif rsi_val < 45: score += 1
    elif rsi_val > 75: score -= 1
    if peg < 1: score += 2
    elif peg < 1.5: score += 1
    elif peg > 3: score -= 1
    if pe_val and pe_val < 25: score += 1
    elif pe_val and pe_val > 80: score -= 1
    if buy_total > sell_total + hold_total: score += 1
    if opt_sent == "Bullish": score += 1
    elif opt_sent == "Bearish": score -= 1
    return score


def build_scores(stocks_data: dict) -> dict:
    return {tk: {"score": _compute_score_local(d), "label": _score_to_label(_compute_score_local(d))}
            for tk, d in stocks_data.items()}


def gen_portfolio_section(stocks_data: dict) -> str:
    rows = load_portfolio()
    if not rows:
        return "## Portfolio\n\n*No portfolio.csv found. Create one to enable tracking.*\n\n"

    usdcad = get_usdcad()
    state = compute_positions(rows, stocks_data, usdcad)
    scores = build_scores(stocks_data)
    actions = compute_actions(state, stocks_data, scores)

    has_positions = any(p["shares"] > 0 for p in state["positions"])

    md = f"## Portfolio Management ($10K CAD target)\n\n"
    md += f"**FX:** USD/CAD = {usdcad:.4f}  |  "
    md += f"**Capital:** ${state['capital_cad']:,.0f} CAD  |  "
    md += f"**Current value:** ${state['total_value_cad']:,.0f} CAD  |  "
    pnl_sign = "+" if state["total_pnl_cad"] >= 0 else ""
    md += f"**P&L:** {pnl_sign}${state['total_pnl_cad']:,.0f} CAD ({pnl_sign}{state['total_pnl_pct']:.2f}%)\n\n"

    md += "### Holdings vs Target\n\n"
    md += "| Ticker | Shares | Avg Cost (USD) | Price (USD) | Value (CAD) | Actual % | Target % | Drift | P&L % | Signal |\n"
    md += "|---|---|---|---|---|---|---|---|---|---|\n"
    for p in state["positions"]:
        sig = scores.get(p["ticker"], {}).get("label", "-")
        drift_pp = (p["drift"] * 100)
        drift_str = f"{'+' if drift_pp >= 0 else ''}{drift_pp:.1f}pp"
        md += (f"| {p['ticker']} | {p['shares']:.4g} | ${p['avg_cost']:.2f} | ${p['price']:.2f} | "
               f"${p['value_cad']:.0f} | {p['actual_weight']*100:.1f}% | {p['target_weight']*100:.1f}% | "
               f"{drift_str} | {p['pnl_pct']:+.1f}% | {sig} |\n")
    md += (f"| CASH | — | — | — | ${state['cash_cad']:.0f} | "
           f"{state['cash_actual_weight']*100:.1f}% | {state['cash_target_weight']*100:.1f}% | — | — | — |\n")

    md += "\n### Today's Recommended Actions\n\n"
    if not has_positions:
        md += ("*Portfolio is uninitialized. Open positions to target weights using the allocation in "
               "[portfolio.csv](portfolio.csv). After each trade, update `shares` and `avg_cost` columns "
               "and re-run the report.*\n\n")
        md += "**Initial buy plan** (at current USD prices, USD/CAD = "
        md += f"{usdcad:.4f}):\n\n"
        md += "| Ticker | Target CAD | At Price (USD) | Shares (approx) | USD Cost |\n|---|---|---|---|---|\n"
        for tk, w in config.PORTFOLIO_TARGETS.items():
            if tk == "CASH":
                continue
            price = stocks_data.get(tk, {}).get("price", {}).get("price")
            if price is None:
                continue
            cad = w * state["capital_cad"]
            usd = cad / usdcad
            shares = usd / price
            md += f"| {tk} | ${cad:.0f} | ${price:.2f} | {shares:.2f} | ${usd:.0f} |\n"
        md += "\n"
    elif not actions:
        md += "*No actions today — portfolio in line with signals.*\n\n"
    else:
        md += "| Ticker | Action |\n|---|---|\n"
        for tk, msg in actions:
            md += f"| **{tk}** | {msg} |\n"
        md += "\n"

    md += ("**How to update:** edit [portfolio.csv](portfolio.csv) after each trade — set `shares` to "
           "your post-trade total, `avg_cost` to your weighted-average USD cost. Set `CASH` row `shares` "
           "to your remaining CAD cash balance. Re-run `python3 report_generator.py` to refresh.\n\n")

    return md


if __name__ == "__main__":
    import data_fetcher
    d = data_fetcher.fetch_all_data()
    print(gen_portfolio_section(d["stocks"]))
