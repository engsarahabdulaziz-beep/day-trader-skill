#!/usr/bin/env python3
"""Daily watch over the user's own lists: what changed since the last run.

Why this exists: a list of thirty names is unreadable every day, but a change
(a 20-day breakout, a move back above the 50-day average, a new 52-week low) is
what the multi-day rules act on. This prints only those changes, from completed
daily closes, and remembers the last state so a signal is reported once.

    python3 trader-gate/watch.py --symbols AAPL,MA,CRWD --list "I would buy"
    python3 trader-gate/watch.py --symbols ... --all     # every name, not just changes

Signals, on closes (the same definitions as trader-scanner/scan.py where they overlap):
  breakout_20d  close above every close of the 20 sessions before it
  reclaim_ma20  the close before was under its 20-day average, this one is above
  above_ma50    closed above the 50-day average after closing below it
  below_ma50    closed below the 50-day average after closing above it
  low_52w       a new 52-week closing low
Flags: price under $5, or 20-day average dollar volume under $20M.
Watch only: nothing here is a trade. Yahoo data is delayed and unofficial.
"""
import argparse
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STATE = Path("trader/state/watch_state.json")


def closes(symbol):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=2y&interval=1d" % symbol
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    now = datetime.now(NY)
    today = now.strftime("%Y-%m-%d")
    out = {}
    for t, c, v in zip(res.get("timestamp") or [], q["close"], q["volume"]):
        d = datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d")
        if c is None or (d == today and now.hour < 16):
            continue  # today's bar counts only once the session has closed
        out[d] = (c, v or 0)
    return sorted(out.items())


def read(symbol):
    bars = closes(symbol)
    c = [b[1][0] for b in bars]
    ma = lambda k, end=len(c): sum(c[end - k:end]) / k if end >= k else None
    last, prev = c[-1], c[-2]
    year = c[-252:]
    m20, m20p, m50, m50p, m200 = ma(20), ma(20, len(c) - 1), ma(50), ma(50, len(c) - 1), ma(200)
    sig = []
    if last > max(c[-21:-1]):
        sig.append("breakout_20d")
    if m20 and m20p and prev < m20p and last > m20:
        sig.append("reclaim_ma20")
    if m50 and m50p and prev <= m50p and last > m50:
        sig.append("above_ma50")
    if m50 and m50p and prev >= m50p and last < m50:
        sig.append("below_ma50")
    if last <= min(year):
        sig.append("low_52w")
    trend = ("up" if m200 and last > m50 > m200 else "down" if m200 and last < m50 < m200 else "mixed")
    dollar_vol = sum(b[1][0] * b[1][1] for b in bars[-20:]) / 20
    flags = [f for f, hit in (("under $5", last < 5), ("thin volume", dollar_vol < 20e6)) if hit]
    return {"date": bars[-1][0], "close": round(last, 2), "trend": trend, "signals": sig, "flags": flags,
            "ma50": round(m50, 2) if m50 else None, "ma200": round(m200, 2) if m200 else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--list", default="watch")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    changes = 0
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        try:
            now = read(sym)
        except Exception as e:  # one bad symbol must not hide the rest
            print("%-6s Data Unavailable (%s)" % (sym, str(e)[:50]))
            continue
        before = state.get(sym, {})
        new = now["date"] != before.get("date")
        changed_trend = before.get("trend") and before["trend"] != now["trend"]
        if a.all or (new and (now["signals"] or changed_trend)):
            changes += 1
            print("%-6s %s close %.2f trend %s%s | %s%s" % (
                sym, now["date"], now["close"], now["trend"],
                " (was %s)" % before["trend"] if changed_trend else "",
                ", ".join(now["signals"]) or "no signal",
                " | " + ", ".join(now["flags"]) if now["flags"] else ""))
        state[sym] = now
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))
    if not changes:
        print("%s: no new signals." % a.list)
    print("Watch only, not a trade. Yahoo data, delayed and unofficial.")


if __name__ == "__main__":
    main()
