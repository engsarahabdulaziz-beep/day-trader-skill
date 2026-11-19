#!/usr/bin/env python3
"""Backtest the multi-day paper rule from swing.py on years of daily bars.

Why this exists: the paper ledger needs weeks to say anything; this asks the
same question of years of history today. Base rule, as in swing.py: buy at the
next open after a close above every close of the prior 20 sessions, stop
2 x ATR(14) under the signal close, target 2R, out after 15 sessions, $3 a side.

    python3 trader-gate/backtest_swing.py                    # base rule, default basket, 5 years
    python3 trader-gate/backtest_swing.py --market --trend --mult 3 --reward 3 --hold 30
    python3 trader-gate/backtest_swing.py --grid --years 10  # variants, split in two halves

--market: only when SPY closed above its 200-day average the day before.
--trend:  only when the stock closed above its own 200-day average.
--grid runs a small, fixed set of variants on the first half of the data, then
reruns the same variants untouched on the second half. Pick on the first half;
believe only what holds up on the second.

Caveats it cannot remove: the basket is today's names (survivorship bias), Yahoo
data is unofficial, and fills at the open ignore slippage. A good number here is
a reason to keep testing, not a reason to trade.
"""
import argparse
import itertools
import json
import urllib.request

import pandas as pd
from backtesting import Backtest, Strategy

BASKET = ("AAPL,MSFT,NVDA,AMZN,GOOGL,META,AVGO,TSLA,JPM,V,UNH,XOM,LLY,HD,PG,COST,"
          "MA,ABBV,CRM,ORCL,AMD,NFLX,KO,PEP,BAC,CAT,GE,IBM,QCOM,SAP")
RISK_USD = 30.0
CASH = 3000.0
COMMISSION = 3.0


def bars(symbol, years):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%dy&interval=1d" % (symbol, years)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"],
                       "Volume": q["volume"]},
                      index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    return df.dropna()[lambda d: ~d.index.duplicated(keep="last")]


def prepare(df, spy_ok):
    """Indicators on the full history, so a later slice starts warm."""
    c = df.Close
    tr = pd.concat([df.High - df.Low, (df.High - c.shift()).abs(), (df.Low - c.shift()).abs()], axis=1).max(axis=1)
    out = df.copy()
    out["ATR"] = tr.rolling(14).mean()
    out["HI20"] = c.shift(1).rolling(20).max()
    out["MA200"] = c.rolling(200).mean()
    out["SPYOK"] = spy_ok.reindex(out.index).ffill().fillna(0)
    return out.dropna()


class Breakout20(Strategy):
    hold = 15
    mult = 2.0
    reward = 2.0
    market = False
    trend = False

    def init(self):
        pass  # indicators are precomputed columns, see prepare()

    def next(self):
        for t in self.trades:
            if len(self.data) - t.entry_bar >= self.hold:
                t.close()
        if self.position:
            return
        d = self.data
        if d.Close[-1] <= d.HI20[-1]:
            return
        if self.market and not d.SPYOK[-1]:
            return
        if self.trend and d.Close[-1] <= d.MA200[-1]:
            return
        risk = self.mult * d.ATR[-1]
        price = d.Close[-1]
        size = int(RISK_USD / risk)
        if size >= 1 and size * price < self.equity:
            self.buy(size=size, sl=price - risk, tp=price + self.reward * risk)


def run(data, params):
    trades = []
    for df in data.values():
        if len(df) < 60:
            continue
        bt = Backtest(df, Breakout20, cash=CASH, commission=lambda size, price: COMMISSION,
                      finalize_trades=True)
        tr = bt.run(**params)._trades
        trades.extend(tr.PnL.tolist())
    n = len(trades)
    wins = [p for p in trades if p > 0]
    losses = [-p for p in trades if p <= 0]
    return {"trades": n, "win%": round(100 * len(wins) / n) if n else 0,
            "net": round(sum(trades), 2), "per_trade": round(sum(trades) / n, 2) if n else 0,
            "pf": round(sum(wins) / sum(losses), 2) if losses and sum(losses) else None}


def load(symbols, years):
    spy = bars("SPY", years).Close
    spy_ok = (spy.shift(1) > spy.rolling(200).mean().shift(1)).astype(int)
    data = {}
    for sym in symbols:
        try:
            data[sym] = prepare(bars(sym, years), spy_ok)
        except Exception as e:  # one bad symbol must not lose the rest
            print("%s: Data Unavailable (%s)" % (sym, str(e)[:60]))
    return data


GRID = [dict(market=m, trend=t, mult=mu, reward=r, hold=h)
        for m, t, (mu, r, h) in itertools.product(
            (False, True), (False, True), ((2.0, 2.0, 15), (3.0, 2.0, 15), (2.0, 3.0, 30), (3.0, 3.0, 30)))]


def label(p):
    return "%s%s stop %gxATR target %gR hold %d" % ("market " if p["market"] else "",
                                                    "trend " if p["trend"] else "",
                                                    p["mult"], p["reward"], p["hold"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=BASKET)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--market", action="store_true")
    ap.add_argument("--trend", action="store_true")
    ap.add_argument("--mult", type=float, default=2.0)
    ap.add_argument("--reward", type=float, default=2.0)
    ap.add_argument("--hold", type=int, default=15)
    ap.add_argument("--grid", action="store_true")
    a = ap.parse_args()
    data = load(a.symbols.split(","), a.years)
    if not a.grid:
        p = dict(market=a.market, trend=a.trend, mult=a.mult, reward=a.reward, hold=a.hold)
        print(label(p), run(data, p))
    else:
        start = min(df.index[0] for df in data.values())
        end = max(df.index[-1] for df in data.values())
        cut = start + (end - start) / 2
        halves = {"first half %s to %s" % (start.date(), cut.date()): {s: d[d.index < cut] for s, d in data.items()},
                  "second half %s to %s" % (cut.date(), end.date()): {s: d[d.index >= cut] for s, d in data.items()}}
        rows = []
        for p in GRID:
            row = {"variant": label(p)}
            for i, (name, part) in enumerate(halves.items()):
                r = run(part, p)
                row.update({("A_" if i == 0 else "B_") + k: v for k, v in r.items()})
            rows.append(row)
        print("A = " + list(halves)[0] + ", B = " + list(halves)[1])
        print(pd.DataFrame(rows).sort_values("A_net", ascending=False).to_string(index=False))
    print("Survivorship bias (today's names), unofficial data, no slippage: a reason to keep testing, not to trade.")


if __name__ == "__main__":
    main()
