#!/usr/bin/env python3
"""
台股盤中快照：抓證交所即時行情（大盤、櫃買、持股與觀察名單），
和前一交易日算好的季線、半年線比較。
輸出：data/intraday.json 與 data/intraday.md
需要先有 fetch_data.py 產生的 data/latest.json。只用 Python 標準函式庫。
"""
import datetime as dt
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data"
TPE = dt.timezone(dt.timedelta(hours=8))
BASE = "https://mis.twse.com.tw/stock"
OPEN_MIN, CLOSE_MIN = 9 * 60, 13 * 60 + 30
errors = []


def make_opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0"), ("Referer", BASE + "/index.jsp")]
    try:
        op.open(BASE + "/index.jsp", timeout=20).read()
    except Exception:
        pass  # 這一步只是預先取得 cookie，失敗不影響行情查詢
    return op


def query(op, channels):
    params = {"ex_ch": "|".join(channels), "json": "1", "delay": "0", "_": int(time.time() * 1000)}
    url = f"{BASE}/api/getStockInfo.jsp?" + urllib.parse.urlencode(params, safe="|")
    last = None
    for attempt in range(3):
        try:
            with op.open(url, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body.get("msgArray") or []
        except Exception as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    errors.append(f"即時行情抓取失敗：{last!r}")
    return []


def num(x):
    try:
        v = float(str(x).split("_")[0])
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100, 2)


def current_price(m):
    """最新成交價；沒有即時成交（例如漲跌停鎖死）時，依序改用上一筆成交價、最佳買價、最佳賣價、今日最低價"""
    return num(m.get("z")) or num(m.get("pz")) or num(m.get("b")) or num(m.get("a")) or num(m.get("l"))


def cross(prev_days, price, ma, label):
    """和前一日收盤時的位置比較，判斷盤中是否跌破或站回"""
    if prev_days is None or price is None or ma is None:
        return None
    was_above, now_above = prev_days > 0, price >= ma
    if was_above and not now_above:
        return f"盤中跌破{label}"
    if not was_above and now_above:
        return f"盤中站回{label}"
    return None


def elapsed_fraction(now):
    minutes = now.hour * 60 + now.minute
    frac = (minutes - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN)
    return min(max(frac, 0.05), 1.0)


def main():
    latest = json.loads((OUT / "latest.json").read_text(encoding="utf-8"))
    stocks = latest.get("holdings", []) + latest.get("watchlist", [])
    holding_codes = {s["code"] for s in latest.get("holdings", [])}
    now = dt.datetime.now(TPE)

    # 每檔同時查上市與上櫃，查不到的那個會自動略過
    channels = ["tse_t00.tw", "otc_o00.tw"]
    for s in stocks:
        channels += [f"tse_{s['code']}.tw", f"otc_{s['code']}.tw"]
    op = make_opener()
    msgs = []
    for i in range(0, len(channels), 20):
        msgs += query(op, channels[i:i + 20])
        time.sleep(1)
    by_code = {m.get("c"): m for m in msgs if m.get("c")}

    trade_date = next((m.get("d") for m in msgs if m.get("d")), None)
    market_open_today = trade_date == now.strftime("%Y%m%d")

    # 大盤
    market = {}
    for key, code in (("taiex", "t00"), ("tpex", "o00")):
        m, base = by_code.get(code), latest.get("market", {}).get(key)
        if not m or not base:
            continue
        price = current_price(m)
        market[key] = {
            "price": price,
            "chg_pct": pct(price, num(m.get("y"))),
            "bias_ma60_pct": pct(price, base.get("ma60")),
            "bias_ma120_pct": pct(price, base.get("ma120")),
            "drawdown_from_high_pct": pct(price, base.get("high_in_history")),
            "signal_ma60": cross(base.get("days_vs_ma60"), price, base.get("ma60"), "季線"),
            "signal_ma120": cross(base.get("days_vs_ma120"), price, base.get("ma120"), "半年線"),
            "time": m.get("t"),
        }

    # 個股
    frac = elapsed_fraction(now)
    rows = []
    for s in stocks:
        m = by_code.get(s["code"])
        if not m or "error" in s:
            rows.append({"code": s["code"], "name": s["name"], "error": "無即時行情"})
            continue
        price, prev = current_price(m), num(m.get("y"))
        chg = pct(price, prev)
        vol = num(m.get("v"))
        avg = s.get("avg_volume_20d")
        signals = [x for x in (
            cross(s.get("days_vs_ma60"), price, s.get("ma60"), "季線"),
            cross(s.get("days_vs_ma120"), price, s.get("ma120"), "半年線"),
        ) if x]
        if chg is not None and chg >= 9.5:
            signals.append("漲停")
        elif chg is not None and chg <= -9.5:
            signals.append("跌停")
        elif chg is not None and abs(chg) >= 5:
            signals.append(f"大{'漲' if chg > 0 else '跌'} {chg:+}%")
        est_ratio = round(vol / avg / frac, 2) if vol and avg else None
        if est_ratio and est_ratio >= 2:
            signals.append(f"預估爆量 {est_ratio} 倍")
        rows.append({
            "code": s["code"],
            "name": s["name"],
            "is_holding": s["code"] in holding_codes,
            "price": price,
            "chg_pct": chg,
            "high": num(m.get("h")),
            "low": num(m.get("l")),
            "volume_lots": vol,
            "est_volume_ratio": est_ratio,
            "bias_ma60_pct": pct(price, s.get("ma60")),
            "bias_ma120_pct": pct(price, s.get("ma120")),
            "signals": signals,
            "time": m.get("t"),
        })

    result = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M %Z"),
        "trade_date": trade_date,
        "market_open_today": market_open_today,
        "market": market,
        "stocks": rows,
        "errors": errors,
        "notes": {
            "ma_basis": "季線與半年線為前一交易日收盤時的數值",
            "est_volume_ratio": "目前成交量換算成全天後，除以 20 日均量",
        },
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "intraday.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(result)
    print(f"完成：{trade_date}，開盤日 {market_open_today}，錯誤 {len(errors)} 筆")
    for e in errors:
        print("  ", e)
    if not market:
        raise SystemExit(1)


def price_text(v):
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,}"


def fmt(v):
    return "無資料" if v is None else f"{v:+}%"


def write_markdown(r):
    lines = [f"# 盤中快照 {r['generated_at']}"]
    if not r["market_open_today"]:
        lines.append("今日休市或尚無今日行情")
    names = {"taiex": "加權指數", "tpex": "櫃買指數"}
    for key, m in r["market"].items():
        if m["price"] is None:
            continue
        sig = "、".join(x for x in (m["signal_ma60"], m["signal_ma120"]) if x)
        lines.append(f"- {names[key]} {price_text(m['price'])}（{fmt(m['chg_pct'])}）｜季線 {fmt(m['bias_ma60_pct'])}、"
                     f"半年線 {fmt(m['bias_ma120_pct'])}｜距高點 {fmt(m['drawdown_from_high_pct'])} {sig}")
    for title, holding in (("持股", True), ("觀察名單異動", False)):
        lines += ["", f"## {title}"]
        for s in r["stocks"]:
            if "error" in s or s["price"] is None:
                continue
            if s["is_holding"] != holding or (not holding and not s["signals"]):
                continue
            lines.append(f"- {s['name']} {price_text(s['price'])}（{fmt(s['chg_pct'])}）｜季線 {fmt(s['bias_ma60_pct'])}、"
                         f"半年線 {fmt(s['bias_ma120_pct'])}" + (f"｜{'、'.join(s['signals'])}" if s["signals"] else ""))
    if r["errors"]:
        lines += ["", "## 錯誤"] + [f"- {e}" for e in r["errors"]]
    (OUT / "intraday.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
