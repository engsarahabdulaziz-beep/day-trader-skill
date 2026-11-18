#!/usr/bin/env python3
"""Pre-market levels for a list of symbols, from Yahoo's free chart endpoint.

    python3 trader-gate/premarket.py SPY QQQ NVDA

Per symbol: previous close, last pre-market price and its time, gap, pre-market
high/low/VWAP/volume, yesterday's high/low and yesterday's volume against its
20-day average. Yahoo is unofficial and can lag, so every row carries the time
of its last bar. A symbol with no pre-market bars prints "Data Unavailable".
"""
import json
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0"}


def chart(sym, rng, interval, prepost):
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=%s"
           % (sym, rng, interval, "true" if prepost else "false"))
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = [(t, o, h, l, c, v) for t, o, h, l, c, v in
            zip(res.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"], q["volume"])
            if c is not None]
    return rows


def levels(sym):
    today = datetime.now(NY).date()
    daily = [r for r in chart(sym, "2mo", "1d", False)
             if datetime.fromtimestamp(r[0], NY).date() < today]
    prev = daily[-1]
    avg20 = sum(r[5] or 0 for r in daily[-21:-1]) / 20
    pre = [r for r in chart(sym, "1d", "1m", True)
           if datetime.fromtimestamp(r[0], NY).date() == today
           and datetime.fromtimestamp(r[0], NY).strftime("%H:%M") < "09:30"]
    out = {"symbol": sym, "prev_close": round(prev[4], 2), "prev_high": round(prev[2], 2),
           "prev_low": round(prev[3], 2), "prev_rvol": round((prev[5] or 0) / avg20, 2) if avg20 else None}
    if not pre:
        out["premarket"] = "Data Unavailable"
        return out
    vol = sum(r[5] or 0 for r in pre)
    last = pre[-1]
    out.update({
        "pm_last": round(last[4], 2),
        "pm_time_et": datetime.fromtimestamp(last[0], NY).strftime("%H:%M"),
        "gap_pct": round((last[4] / prev[4] - 1) * 100, 2),
        "pm_high": round(max(r[2] for r in pre), 2),
        "pm_low": round(min(r[3] for r in pre), 2),
        "pm_volume": vol,
        "pm_vwap": round(sum((r[2] + r[3] + r[4]) / 3 * (r[5] or 0) for r in pre) / vol, 2) if vol else None,
    })
    return out


if __name__ == "__main__":
    for s in sys.argv[1:]:
        try:
            print(json.dumps(levels(s.upper())))
        except Exception as e:  # one bad symbol must not hide the rest
            print(json.dumps({"symbol": s.upper(), "premarket": "Data Unavailable", "error": str(e)[:80]}))
