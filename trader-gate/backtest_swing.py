#!/usr/bin/env python3
"""Backtest the multi-day paper rule from swing.py on years of daily bars.

Why this exists: the paper ledger needs weeks to say anything; this asks the
same question of years of history today. Same rule as swing.py: buy at the next
open after a close above every close of the prior 20 sessions, stop 2 x ATR(14)
under the entry, target 2R, out after 15 sessions, $3 a side.

    python3 trader-gate/backtest_swing.py                       # default basket, 5 years
    python3 trader-gate/backtest_swing.py --symbols AAPL,MSFT --years 10

Caveats it cannot remove: the basket is today's names (survivorship bias), Yahoo
data is unofficial, and fills at the open ignore slippage. A good number here is
a reason to keep testing, not a reason to trade.
"""
import argparse
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
                       "Volume": q["volume"]}, index=pd.to_datetime(res["timestamp"], unit="s"))
    return df.dropna()


def atr(high, low, close, n=14):
    h, l, c = pd.Series(high), pd.Series(low), pd.Series(close)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().to_numpy()


def prior_high(close, n=20):
    return pd.Series(close).shift(1).rolling(n).max().to_numpy()


class Breakout20(Strategy):
    hold = 15
    mult = 2.0
    reward = 2.0

    def init(self):
        self.atr = self.I(atr, self.data.High, self.data.Low, self.data.Close)
        self.hi20 = self.I(prior_high, self.data.Close)

    def next(self):
        for t in self.trades:
            if len(self.data) - t.entry_bar >= self.hold:
                t.close()
        if self.position or self.atr[-1] != self.atr[-1]:
            return
        if self.data.Close[-1] > self.hi20[-1]:
            risk = self.mult * self.atr[-1]
            price = self.data.Close[-1]
            size = int(RISK_USD / risk)
            if size >= 1 and size * price < self.equity:
                self.buy(size=size, sl=price - risk, tp=price + self.reward * risk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=BASKET)
    ap.add_argument("--years", type=int, default=5)
    a = ap.parse_args()
    rows = []
    for sym in a.symbols.split(","):
        try:
            df = bars(sym, a.years)
        except Exception as e:  # one bad symbol must not lose the rest
            print("%s: Data Unavailable (%s)" % (sym, str(e)[:60]))
            continue
        bt = Backtest(df, Breakout20, cash=CASH, commission=lambda size, price: COMMISSION,
                      trade_on_close=False, exclusive_orders=False, finalize_trades=True)
        s = bt.run()
        tr = s._trades
        rows.append({"symbol": sym, "trades": len(tr), "net": float(tr.PnL.sum()) if len(tr) else 0.0,
                     "wins": int((tr.PnL > 0).sum()) if len(tr) else 0})
    out = pd.DataFrame(rows).sort_values("net")
    print(out.to_string(index=False))
    n, w, net = out.trades.sum(), out.wins.sum(), out.net.sum()
    print("\n%d symbols, %d trades, %d winners (%.0f%%), net after $3 a side: $%.2f, average $%.2f a trade"
          % (len(out), n, w, 100 * w / n if n else 0, net, net / n if n else 0))
    print("Survivorship bias (today's names), unofficial data, no slippage: a reason to keep testing, not to trade.")


if __name__ == "__main__":
    main()
