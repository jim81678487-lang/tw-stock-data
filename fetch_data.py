#!/usr/bin/env python3
"""
台股每日資料：從 FinMind 抓取大盤與個股資料，計算均線、資金面與動能指標。
輸出：
  data/latest.json  給晨報程式讀取的完整數據
  data/latest.md    人看得懂的摘要
只用 Python 標準函式庫，不需要安裝套件。
"""
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data"
TPE = dt.timezone(dt.timedelta(hours=8))

errors = []


# ---------- 抓資料 ----------

def fetch(dataset, data_id=None, start=None, retries=3):
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start:
        params["start_date"] = start
    url = API + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "tw-stock-brief/1.0"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("status") == 200:
                time.sleep(0.4)  # 對 API 客氣一點
                return body.get("data") or []
            last = f"status {body.get('status')}: {body.get('msg')}"
        except Exception as exc:  # 網路或格式錯誤都重試
            last = repr(exc)
        time.sleep(3 * (attempt + 1))
    errors.append(f"{dataset} {data_id or ''} 抓取失敗：{last}")
    return []


def days_ago(n):
    return (dt.datetime.now(TPE).date() - dt.timedelta(days=n)).isoformat()


# ---------- 指標計算 ----------

def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100, 2)


def ret(closes, n):
    """近 n 個交易日漲跌幅 %"""
    return pct(closes[-1], closes[-1 - n]) if len(closes) > n else None


def rsi(closes, n=14):
    """Wilder RSI"""
    if len(closes) <= n:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 1)


def days_on_side(closes, n):
    """收盤價連續站在 n 日均線同一側的天數；正數代表在上方，負數代表在下方"""
    if len(closes) < n:
        return None
    signs = []
    for i in range(n - 1, len(closes)):
        ma = sum(closes[i - n + 1:i + 1]) / n
        signs.append(1 if closes[i] >= ma else -1)
    cur, count = signs[-1], 0
    for s in reversed(signs):
        if s != cur:
            break
        count += 1
    return count * cur


def streak(nets):
    """連續買超或賣超天數；正數為連買，負數為連賣"""
    if not nets or nets[-1] == 0:
        return 0
    cur = 1 if nets[-1] > 0 else -1
    count = 0
    for v in reversed(nets):
        if (v > 0 and cur > 0) or (v < 0 and cur < 0):
            count += 1
        else:
            break
    return count * cur


def light(close, ma60, ma120, drawdown):
    """崩盤警示燈：紅 距高點跌逾20%，橙 跌破半年線，黃 跌破季線，綠 站上季線"""
    if drawdown is not None and drawdown <= -20:
        return "紅燈"
    if ma120 is not None and close < ma120:
        return "橙燈"
    if ma60 is not None and close < ma60:
        return "黃燈"
    return "綠燈"


def price_block(rows, volume_key="Trading_Volume", volume_div=1000):
    """共用的價格與均線計算"""
    rows = sorted((r for r in rows if r.get("close")), key=lambda r: r["date"])
    if not rows:
        return None
    closes = [float(r["close"]) for r in rows]
    vols = [float(r.get(volume_key) or 0) / volume_div for r in rows]
    c = closes[-1]
    ma60, ma120 = sma(closes, 60), sma(closes, 120)
    high_250 = max(closes[-250:])
    return {
        "date": rows[-1]["date"],
        "close": c,
        "chg_1d_pct": ret(closes, 1),
        "chg_5d_pct": ret(closes, 5),
        "chg_20d_pct": ret(closes, 20),
        "ma60": round(ma60, 2) if ma60 else None,
        "ma120": round(ma120, 2) if ma120 else None,
        "bias_ma60_pct": pct(c, ma60),
        "bias_ma120_pct": pct(c, ma120),
        "days_vs_ma60": days_on_side(closes, 60),
        "days_vs_ma120": days_on_side(closes, 120),
        "rsi14": rsi(closes),
        "volume_ratio_20d": round(vols[-1] / sma(vols, 20), 2) if sma(vols, 20) else None,
        "high_250d": high_250,
        "drawdown_from_250d_high_pct": pct(c, high_250),
        "_closes": closes,
    }


# ---------- 個股 ----------

def stock_snapshot(code, name, start):
    p = price_block(fetch("TaiwanStockPrice", code, start))
    if p is None:
        return {"code": code, "name": name, "error": "無價格資料"}
    p.pop("_closes")
    snap = {"code": code, "name": name, **p}

    # 三大法人（單位換算成張）
    inst = fetch("TaiwanStockInstitutionalInvestorsBuySell", code, days_ago(45))
    by_date = {}
    for r in inst:
        d = by_date.setdefault(r["date"], {"foreign": 0, "trust": 0, "dealer": 0})
        net = (float(r.get("buy") or 0) - float(r.get("sell") or 0)) / 1000
        n = r.get("name", "")
        if n == "Foreign_Investor":
            d["foreign"] += net
        elif n == "Investment_Trust":
            d["trust"] += net
        elif n.startswith("Dealer"):
            d["dealer"] += net
    dates = sorted(by_date)
    if dates:
        f = [by_date[x]["foreign"] for x in dates]
        t = [by_date[x]["trust"] for x in dates]
        dl = [by_date[x]["dealer"] for x in dates]
        snap["institutional"] = {
            "date": dates[-1],
            "foreign_net_lots": round(f[-1]),
            "trust_net_lots": round(t[-1]),
            "dealer_net_lots": round(dl[-1]),
            "foreign_5d_lots": round(sum(f[-5:])),
            "trust_5d_lots": round(sum(t[-5:])),
            "foreign_streak": streak(f),
            "trust_streak": streak(t),
        }

    # 融資融券（張）
    margin = sorted(fetch("TaiwanStockMarginPurchaseShortSale", code, days_ago(30)),
                    key=lambda r: r["date"])
    if margin:
        bal = [float(r.get("MarginPurchaseTodayBalance") or 0) for r in margin]
        short = [float(r.get("ShortSaleTodayBalance") or 0) for r in margin]
        snap["margin"] = {
            "date": margin[-1]["date"],
            "margin_balance_lots": round(bal[-1]),
            "margin_chg_1d_lots": round(bal[-1] - bal[-2]) if len(bal) > 1 else None,
            "margin_chg_5d_lots": round(bal[-1] - bal[-6]) if len(bal) > 5 else None,
            "short_balance_lots": round(short[-1]),
        }

    # 外資持股比例
    hold = sorted(fetch("TaiwanStockShareholding", code, days_ago(45)), key=lambda r: r["date"])
    ratios = [float(r["ForeignInvestmentSharesRatio"]) for r in hold
              if r.get("ForeignInvestmentSharesRatio") not in (None, "")]
    if ratios:
        snap["foreign_holding"] = {
            "date": hold[-1]["date"],
            "ratio_pct": round(ratios[-1], 2),
            "chg_20d_pct_pt": round(ratios[-1] - ratios[-21], 2) if len(ratios) > 20 else None,
        }
    return snap


# ---------- 大盤 ----------

def market_snapshot(start):
    out = {}
    for key, data_id in (("taiex", "TAIEX"), ("tpex", "TPEx")):
        rows = fetch("TaiwanStockPrice", data_id, start)
        p = price_block(rows, volume_key="Trading_money", volume_div=1e8)
        if p is None:
            continue
        closes = p.pop("_closes")
        ath = max(closes)
        p["high_in_history"] = ath
        p["drawdown_from_high_pct"] = pct(closes[-1], ath)
        p["crash_light"] = light(closes[-1], p["ma60"], p["ma120"], p["drawdown_from_high_pct"])
        p["red_light_level"] = round(ath * 0.8, 2)
        out[key] = p

    # 三大法人整體買賣超（億元）
    tot = fetch("TaiwanStockTotalInstitutionalInvestors", None, days_ago(14))
    by_date = {}
    for r in tot:
        d = by_date.setdefault(r["date"], {})
        net = (float(r.get("buy") or 0) - float(r.get("sell") or 0)) / 1e8
        d[r.get("name", "")] = d.get(r.get("name", ""), 0) + net
    if by_date:
        last = max(by_date)
        d = by_date[last]
        foreign = d.get("Foreign_Investor", 0)
        trust = d.get("Investment_Trust", 0)
        dealer = d.get("Dealer_self", 0) + d.get("Dealer_Hedging", 0)
        out["institutional_total"] = {
            "date": last,
            "foreign_net_100m": round(foreign, 2),
            "trust_net_100m": round(trust, 2),
            "dealer_net_100m": round(dealer, 2),
            "total_net_100m": round(foreign + trust + dealer, 2),
        }

    # 整體融資餘額變化（用百分比，避免單位問題）
    tm = sorted(fetch("TaiwanStockTotalMarginPurchaseShortSale", None, days_ago(14)),
                key=lambda r: r["date"])
    money = [r for r in tm if r.get("name") == "MarginPurchaseMoney"]
    if money:
        r = money[-1]
        today, yes = float(r.get("TodayBalance") or 0), float(r.get("YesBalance") or 0)
        out["margin_total"] = {"date": r["date"], "margin_money_chg_pct": pct(today, yes)}
    return out


# ---------- 輸出 ----------

def fmt(v, suffix="", sign=True):
    if v is None:
        return "無資料"
    if isinstance(v, float) and v.is_integer() and not suffix.endswith("%"):
        v = int(v)
    return f"{v:+,}{suffix}" if sign else f"{v:,}{suffix}"


def side(days, label):
    if days is None:
        return f"{label}資料不足"
    return f"{label}上方第{days}天" if days > 0 else f"{label}下方第{-days}天"


def num(v):
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,}"


def streak_text(n):
    if not n:
        return "持平"
    return f"連{abs(n)}{'買' if n > 0 else '賣'}"


def stock_line(s):
    if "error" in s:
        return f"- {s['name']} {s['code']}：{s['error']}"
    stale = "（資料非最新交易日）" if s.get("stale") else ""
    parts = [
        f"{s['name']} {num(s['close'])} 元（{fmt(s['chg_1d_pct'], '%')}）{stale}",
        f"季線 {fmt(s['bias_ma60_pct'], '%')}，{side(s['days_vs_ma60'], '季線')}",
        f"半年線 {fmt(s['bias_ma120_pct'], '%')}，{side(s['days_vs_ma120'], '半年線')}",
        f"5日 {fmt(s['chg_5d_pct'], '%')}、20日 {fmt(s['chg_20d_pct'], '%')}",
        f"RSI {s['rsi14']}、量比 {s['volume_ratio_20d']}",
    ]
    inst = s.get("institutional")
    if inst:
        parts.append(f"外資 {fmt(inst['foreign_net_lots'], '張')}（{streak_text(inst['foreign_streak'])}）、"
                     f"投信 {fmt(inst['trust_net_lots'], '張')}（{streak_text(inst['trust_streak'])}）")
    m = s.get("margin")
    if m:
        parts.append(f"融資 {fmt(m['margin_chg_1d_lots'], '張')}")
    return "- " + "｜".join(parts)


def write_markdown(result):
    lines = [f"# 台股資料 {result['data_date']}", f"產生時間：{result['generated_at']}", ""]
    t = result["market"].get("taiex")
    if t:
        lines += [
            "## 大盤",
            f"- 加權指數 {num(t['close'])}（{fmt(t['chg_1d_pct'], '%')}），成交量為 20 日均量的 {t.get('volume_ratio_20d')} 倍",
            f"- 季線 {num(t['ma60'])}（乖離 {fmt(t['bias_ma60_pct'], '%')}），半年線 {num(t['ma120'])}（乖離 {fmt(t['bias_ma120_pct'], '%')}）",
            f"- 距高點 {num(t['high_in_history'])} 回檔 {fmt(t['drawdown_from_high_pct'], '%')}，紅燈線 {num(t['red_light_level'])}",
            f"- 崩盤警示：{t['crash_light']}",
        ]
    it = result["market"].get("institutional_total")
    if it:
        lines.append(f"- 三大法人 {fmt(it['total_net_100m'], '億')}：外資 {fmt(it['foreign_net_100m'], '億')}、"
                     f"投信 {fmt(it['trust_net_100m'], '億')}、自營商 {fmt(it['dealer_net_100m'], '億')}")
    for title, key in (("我的持股", "holdings"), ("觀察名單", "watchlist")):
        lines += ["", f"## {title}"] + [stock_line(s) for s in result[key]]
    if result["errors"]:
        lines += ["", "## 抓取錯誤"] + [f"- {e}" for e in result["errors"]]
    (OUT / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    start = cfg.get("history_start", "2024-01-01")
    market = market_snapshot(start)
    holdings = [stock_snapshot(s["code"], s["name"], start) for s in cfg["holdings"]]
    watch = [stock_snapshot(s["code"], s["name"], start) for s in cfg["watchlist"]]

    data_date = market.get("taiex", {}).get("date")
    for s in holdings + watch:
        if data_date and s.get("date") and s["date"] != data_date:
            s["stale"] = True  # 停牌或資料未更新

    result = {
        "generated_at": dt.datetime.now(TPE).strftime("%Y-%m-%d %H:%M %Z"),
        "data_date": data_date,
        "market": market,
        "holdings": holdings,
        "watchlist": watch,
        "errors": errors,
        "notes": {
            "crash_light": "紅燈 距歷史高點跌逾20%；橙燈 跌破半年線；黃燈 跌破季線；綠燈 站上季線",
            "units": "個股法人與融資單位為張；大盤法人單位為億元；量比為當日量除以20日均量",
            "days_vs_ma": "正數為連續站上均線天數，負數為連續在均線下方天數",
        },
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(result)
    print(f"完成：資料日期 {data_date}，錯誤 {len(errors)} 筆")
    for e in errors:
        print("  ", e)
    # 大盤抓不到代表整體失敗，讓 GitHub Actions 標示紅色
    if not market.get("taiex"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
