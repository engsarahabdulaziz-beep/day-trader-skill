#!/usr/bin/env python3
"""Paper ledger: what would have happened to a setup nobody traded.

Why this exists: the time machine replays simulated sessions, which says nothing
about whether the rule works live. This keeps a plain record of every setup that
passed the checks and what the real bars did to it afterwards.

    python3 trader-gate/paper.py add                      # from trader/orders.json + trader/state/risk.json
    python3 trader-gate/paper.py add --symbol BILL --time 10:20 --entry 66.18 --stop 65.21 --target 68.12 --shares 30
    python3 trader-gate/paper.py settle                   # fill in today's open rows after the close
    python3 trader-gate/paper.py summary

The outcome reads Yahoo's 5-minute bars from the entry time to 15:55 New York.
Inside one bar the stop is checked before the target, the conservative call.
Hypothetical results on delayed, unofficial data: not a track record.
"""
import argparse
import csv
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
LEDGER = Path("paper/ledger.csv")
FIELDS = ["date", "symbol", "setup", "entry_time", "entry", "stop", "target", "shares",
          "outcome", "exit_time", "exit", "gross", "commission", "net"]


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


def bars(symbol, day):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=5m" % symbol
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for t, h, l, c in zip(res.get("timestamp") or [], q["high"], q["low"], q["close"]):
        d = datetime.fromtimestamp(t, NY)
        if c is not None and d.strftime("%Y-%m-%d") == day:
            out.append((d.strftime("%H:%M"), h, l, c))
    return out


def settle_row(row, commission):
    entry, stop, target = float(row["entry"]), float(row["stop"]), float(row["target"])
    live = [b for b in bars(row["symbol"], row["date"]) if row["entry_time"] <= b[0] < "15:55"]
    if not live:
        return False
    outcome, when, price = "neither", "15:55", live[-1][3]
    for t, h, l, _ in live:
        if l <= stop:
            outcome, when, price = "stop", t, stop
            break
        if h >= target:
            outcome, when, price = "target", t, target
            break
    gross = float(row["shares"]) * (price - entry)
    row.update(outcome=outcome, exit_time=when, exit="%.2f" % price, gross="%.2f" % gross,
               commission="%.2f" % commission, net="%.2f" % (gross - commission))
    return True


def cmd_add(a):
    rows = load()
    today = a.date or datetime.now(NY).strftime("%Y-%m-%d")
    new = []
    if a.symbol:
        new.append((a.symbol.upper(), a.setup, a.time, a.entry, a.stop, a.target, a.shares))
    else:
        orders = json.loads(Path("trader/orders.json").read_text())["orders"]
        verdicts = {r["index"]: r for r in json.loads(Path("trader/state/risk.json").read_text())["results"]}
        for i, o in enumerate(orders, 1):
            if verdicts.get(i, {}).get("verdict") != "PASS":
                continue
            p = o["plan"]
            t = datetime.strptime(p["entry_time"], "%I:%M %p").strftime("%H:%M")
            new.append((o["symbol"], p.get("setup", "orb"), t, p["entry"], p["stop"], p["target"], o["qty"]))
    for sym, setup, t, e, s, tg, n in new:
        if any(r["date"] == today and r["symbol"] == sym and r["entry_time"] == t for r in rows):
            continue
        rows.append(dict.fromkeys(FIELDS, "") | {"date": today, "symbol": sym, "setup": setup, "entry_time": t,
                                                 "entry": e, "stop": s, "target": tg, "shares": n})
        print("added %s %s %s entry %s stop %s target %s x%s" % (today, sym, t, e, s, tg, n))
    save(rows)


def cmd_settle(a):
    rows = load()
    day = a.date or datetime.now(NY).strftime("%Y-%m-%d")
    for r in rows:
        if r["date"] == day and not r["outcome"]:
            try:
                ok = settle_row(r, a.commission)
            except Exception as e:  # one bad symbol must not lose the rest
                ok = False
                print("%s: Data Unavailable (%s)" % (r["symbol"], str(e)[:60]))
            if ok:
                print("%s %s -> %s at %s, net %s" % (r["symbol"], r["entry_time"], r["outcome"], r["exit_time"], r["net"]))
    save(rows)


def cmd_summary(_):
    done = [r for r in load() if r["outcome"]]
    if not done:
        print("No settled rows yet.")
        return
    count = lambda o: sum(r["outcome"] == o for r in done)
    net = sum(float(r["net"]) for r in done)
    days = len({r["date"] for r in done})
    print("%d setups over %d day(s): %d target, %d stop, %d neither. Hypothetical net after commission: $%.2f"
          % (len(done), days, count("target"), count("stop"), count("neither"), net))
    print("Hypothetical, from delayed unofficial bars. Not a track record and not a forecast.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("--symbol"); p.add_argument("--setup", default="orb"); p.add_argument("--time")
    p.add_argument("--entry"); p.add_argument("--stop"); p.add_argument("--target"); p.add_argument("--shares")
    p.add_argument("--date"); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("settle")
    p.add_argument("--date"); p.add_argument("--commission", type=float, default=6.0); p.set_defaults(fn=cmd_settle)
    p = sub.add_parser("summary"); p.set_defaults(fn=cmd_summary)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
