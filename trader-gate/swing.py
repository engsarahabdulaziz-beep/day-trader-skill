#!/usr/bin/env python3
"""Paper ledger for multi-day setups from the daily scan.

Why this exists: the daily scan tags charts (20-day breakout, bull flag,
reclaimed the 20-day average) but nothing says whether buying them and holding
for days pays. This records a fixed, mechanical version of that trade and
settles it from real daily bars, so the rule can be judged before money is on it.

    python3 trader-gate/swing.py add       # after the open: today's picks from the daily watchlist
    python3 trader-gate/swing.py settle    # after the close: update every open row
    python3 trader-gate/swing.py summary

The paper trade: buy at the day's open, the first session after the signal.
Stop 2 x ATR(14) under the entry, target 3 x that risk above it, out at the
close of the 30th session if neither came first (changed from 2R / 15 sessions
on 2026-11-19 after trader-gate/backtest_swing.py --grid; rows already open
were moved to the new target). A gap through the stop exits
at that day's open. On a day that touches both, the stop counts. Shares are
sized to the max loss per trade. Every row also records the market posture of
its entry day, so blocked days can be compared with allowed ones.
Hypothetical, on delayed unofficial data: not a track record.
"""
import argparse
import csv
import glob
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
LEDGER = Path("paper/swing.csv")
SETUPS = {"breakout_20d", "bull_flag", "reclaim_ma20"}
FIELDS = ["signal_date", "entry_date", "symbol", "setups", "posture", "entry", "stop", "target", "shares",
          "status", "exit_date", "exit", "days_held", "net"]
HOLD_DAYS = 30
ATR_MULT = 2.0
REWARD = 3.0


def daily(symbol):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=6mo&interval=1d" % symbol
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    return [(datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d"), o, h, l, c)
            for t, o, h, l, c in zip(res.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"])
            if c is not None and o is not None]


def atr14(bars):
    tr = [max(bars[i][2] - bars[i][3], abs(bars[i][2] - bars[i - 1][4]), abs(bars[i][3] - bars[i - 1][4]))
          for i in range(1, len(bars))]
    return sum(tr[-14:]) / 14


def load():
    if not LEDGER.exists():
        return []
    with LEDGER.open() as f:
        return list(csv.DictReader(f))


def save(rows):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def posture(day):
    files = sorted(glob.glob("trader/state/market/exposure_posture_%s_*.json" % day))
    if not files:
        return "unknown"
    return json.loads(Path(files[-1]).read_text()).get("recommendation", "unknown")


def cmd_add(a):
    cfg = json.loads(Path("trader/config.json").read_text())
    today = a.date or datetime.now(NY).strftime("%Y-%m-%d")
    watch = json.loads(Path(a.watchlist).read_text())
    if watch.get("mode") != "daily":
        sys.exit("swing.py: %s is not a daily scan" % a.watchlist)
    rows = load()
    open_syms = {r["symbol"] for r in rows if r["status"] == "open"}
    picks = [h for h in watch["hits"] if SETUPS & set(h["setups"]) and h["symbol"] not in open_syms]
    picks.sort(key=lambda h: -h["price"] * h["volume"])
    added = 0
    for h in picks:
        if added >= a.count:
            break
        try:
            bars = daily(h["symbol"])
        except Exception as e:  # one bad symbol must not lose the rest
            print("%s: Data Unavailable (%s)" % (h["symbol"], str(e)[:60]))
            continue
        day = [b for b in bars if b[0] == today]
        before = [b for b in bars if b[0] < today]
        if not day or len(before) < 15:
            print("%s: no %s bar yet, skipped" % (h["symbol"], today))
            continue
        entry = day[0][1]
        risk = ATR_MULT * atr14(before)
        shares = min(int(cfg["max_loss_per_trade_usd"] / risk), int(cfg["max_order_usd"] / entry))
        if shares < 1:
            print("%s: one share risks more than the max loss, skipped" % h["symbol"])
            continue
        rows.append(dict.fromkeys(FIELDS, "") | {
            "signal_date": before[-1][0], "entry_date": today, "symbol": h["symbol"],
            "setups": "+".join(sorted(SETUPS & set(h["setups"]))), "posture": posture(today),
            "entry": "%.2f" % entry, "stop": "%.2f" % (entry - risk), "target": "%.2f" % (entry + REWARD * risk),
            "shares": shares, "status": "open"})
        added += 1
        print("added %s entry %.2f stop %.2f target %.2f x%d" % (h["symbol"], entry, entry - risk, entry + REWARD * risk, shares))
    save(rows)


def cmd_settle(a):
    rows = load()
    today = a.date or datetime.now(NY).strftime("%Y-%m-%d")
    for r in rows:
        if r["status"] != "open":
            continue
        try:
            held = [b for b in daily(r["symbol"]) if r["entry_date"] <= b[0] <= today]
        except Exception as e:
            print("%s: Data Unavailable (%s)" % (r["symbol"], str(e)[:60]))
            continue
        entry, stop, target = float(r["entry"]), float(r["stop"]), float(r["target"])
        done = None
        for n, (d, o, h, l, c) in enumerate(held, 1):
            if n > 1 and o <= stop:
                done = ("stop", d, o, n)
            elif l <= stop:
                done = ("stop", d, stop, n)
            elif h >= target:
                done = ("target", d, target, n)
            elif n >= HOLD_DAYS:
                done = ("time", d, c, n)
            if done:
                break
        r["days_held"] = len(held)
        if done:
            status, d, price, n = done
            r.update(status=status, exit_date=d, exit="%.2f" % price, days_held=n,
                     net="%.2f" % (float(r["shares"]) * (price - entry) - a.commission))
            print("%s -> %s on %s at %.2f, net %s" % (r["symbol"], status, d, price, r["net"]))
        elif held:
            print("%s open, day %d, last close %.2f (entry %.2f)" % (r["symbol"], len(held), held[-1][4], entry))
    save(rows)


def cmd_summary(_):
    rows = load()
    done = [r for r in rows if r["status"] != "open"]
    count = lambda s: sum(r["status"] == s for r in done)
    print("%d open, %d closed: %d target, %d stop, %d time exit. Hypothetical net after commission: $%.2f"
          % (len(rows) - len(done), len(done), count("target"), count("stop"), count("time"),
             sum(float(r["net"]) for r in done)))
    for p in sorted({r["posture"] for r in done}):
        sub = [r for r in done if r["posture"] == p]
        print("  entered under %s: %d closed, net $%.2f" % (p, len(sub), sum(float(r["net"]) for r in sub)))
    print("Hypothetical, from delayed unofficial bars. Not a track record and not a forecast.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("--watchlist", default="trader/state/watchlist-daily.json")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--date")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("settle")
    p.add_argument("--date")
    p.add_argument("--commission", type=float, default=6.0)
    p.set_defaults(fn=cmd_settle)
    p = sub.add_parser("summary")
    p.set_defaults(fn=cmd_summary)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
