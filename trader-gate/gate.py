#!/usr/bin/env python3
"""Discipline gate and journal for the orders trader-risk just checked.

Why this exists: trader-risk checks an order against the money limits, but it
keeps no memory between days. This glue hands each order to two skills from
tradermonty/claude-trading-skills: trader-memory-core (a thesis per trade, so a
losing exit can block a revenge entry inside 24 hours) and
pre-trade-discipline-gate (GO / NO_GO / REVIEW_REQUIRED before manual entry).
The market posture comes from exposure-coach, fed by the breadth, uptrend and
macro-regime analyzers (the inputs that need no API key).

    python3 trader-gate/gate.py            # reads trader/orders.json + trader/state/risk.json
    python3 trader-gate/gate.py --as-of 2026-10-19T10:15:00-04:00

Nothing here places an order.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILLS = Path(os.environ.get("TRADING_SKILLS_DIR", Path.home() / ".claude" / "skills"))
GATE = SKILLS / "pre-trade-discipline-gate" / "scripts" / "check_pre_trade_discipline.py"
MEMORY = SKILLS / "trader-memory-core" / "scripts" / "trader_memory_cli.py"


def read(path):
    return json.loads(Path(path).read_text())


def thesis_ids(state_dir):
    return {p.stem for p in Path(state_dir).glob("*.yaml") if not p.stem.startswith("_")}


def register(order, state_dir, work):
    """One IDEA thesis per order, so the gate can link its report and a later
    losing exit is visible to the revenge-window check."""
    plan = order["plan"]
    src = work / ("thesis_%s.json" % order["symbol"])
    src.write_text(json.dumps({
        "ticker": order["symbol"],
        "thesis_statement": order.get("why") or "%s day trade" % order["symbol"],
        "thesis_type": "pivot_breakout",
        "entry_price": plan["entry"],
        "shares": float(order["qty"]),
        "stop_price": plan["stop"],
        "target_price": plan["target"],
    }))
    before = thesis_ids(state_dir)
    subprocess.run([sys.executable, str(MEMORY), "ingest", "--source", "manual",
                    "--input", str(src), "--state-dir", str(state_dir)],
                   check=True, capture_output=True, text=True)
    new = sorted(thesis_ids(state_dir) - before)
    return new[0] if new else None


def market_posture(out):
    """Today's exposure-coach posture (NEW_ENTRY_ALLOWED / REDUCE_ONLY /
    CASH_PRIORITY), built from the three inputs that need no API key. Computed
    once a day: the inputs are daily series, so re-running intraday adds nothing."""
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done = sorted(out.glob("exposure_posture_%s_*.json" % today))
    if done:
        return done[-1]
    inputs = [("--breadth", "market-breadth-analyzer", "market_breadth_analyzer.py", "market_breadth_2*.json"),
              ("--uptrend", "uptrend-analyzer", "uptrend_analyzer.py", "uptrend_analysis_*.json"),
              ("--regime", "macro-regime-detector", "macro_regime_detector.py", "macro_regime_*.json")]
    args = []
    for flag, skill, script, pattern in inputs:
        subprocess.run([sys.executable, str(SKILLS / skill / "scripts" / script), "--output-dir", str(out)],
                       capture_output=True, text=True)
        fresh = sorted(out.glob(pattern.replace("*", today + "_*", 1) if "2*" not in pattern
                                else "market_breadth_%s_*.json" % today))
        if fresh:
            args += [flag, str(fresh[-1])]
    if not args:
        return None
    subprocess.run([sys.executable, str(SKILLS / "exposure-coach" / "scripts" / "calculate_exposure.py"),
                    *args, "--output-dir", str(out)], capture_output=True, text=True)
    done = sorted(out.glob("exposure_posture_%s_*.json" % today))
    return done[-1] if done else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="trader")
    ap.add_argument("--as-of")
    a = ap.parse_args()
    root = Path(a.dir)
    cfg = read(root / "config.json")
    orders = read(root / "orders.json")["orders"]
    risk = read(root / "state" / "risk.json")
    work = root / "state" / "gate"
    theses = root / "state" / "theses"
    work.mkdir(parents=True, exist_ok=True)
    theses.mkdir(parents=True, exist_ok=True)

    verdicts = {r["index"]: r for r in risk["results"]}
    candidates = []
    for i, order in enumerate(orders, 1):
        v = verdicts.get(i, {})
        if v.get("kind") != "entry":
            continue
        passed = v.get("verdict") == "PASS"
        cand = {
            "symbol": order["symbol"],
            # A trader-risk BLOCK is final, so it never becomes an order permission here.
            "order_intent": "ENTRY_READY" if passed else "REJECTED",
            "entry_in_written_plan": "plan" in order,
            "stop_predefined": bool(order.get("stop_loss_stop_price")),
            "size_within_plan": passed,
            "planned_risk_dollars": cfg["max_loss_per_trade_usd"],
            "actual_risk_dollars": v.get("max_loss"),
            "notes": "; ".join(v.get("reasons") or v.get("info") or []),
        }
        if passed:
            cand["thesis_id"] = register(order, theses, work)
        candidates.append(cand)
    answers = work / "answers.json"
    answers.write_text(json.dumps({"candidates": candidates}, indent=2))

    today = risk.get("today", {})
    halted = (today.get("entries_placed", 0) >= cfg["max_trades_per_day"]
              or today.get("loss_at_risk", 0) >= cfg["max_daily_loss_usd"])
    breaker = work / "circuit_breaker_decision.json"
    breaker.write_text(json.dumps({
        "recommendation": "TRADING_HALTED" if halted else "TRADING_ALLOWED",
        "source": "trader-risk day limits",
        "entries_placed": today.get("entries_placed", 0),
        "loss_at_risk": today.get("loss_at_risk", 0),
    }, indent=2))
    regime = market_posture(root / "state" / "market")
    if regime and not cfg.get("market_posture_blocks", True):
        # The user chose to treat the posture as a warning: exposure-coach sets a
        # multi-week exposure ceiling, which is stricter than a trade that is flat
        # by the close needs. The real reading is kept and printed, never dropped.
        real = read(regime)
        advisory = work / "exposure_decision_advisory.json"
        advisory.write_text(json.dumps({
            "recommendation": "NEW_ENTRY_ALLOWED",
            "source": "advisory mode (market_posture_blocks is false in the config)",
            "real_recommendation": real.get("recommendation"),
            "real_exposure_ceiling_pct": real.get("exposure_ceiling_pct"),
            "real_confidence": real.get("confidence"),
        }, indent=2))
        print("MARKET WARNING (not blocking): posture %s, exposure ceiling %s%%, confidence %s"
              % (real.get("recommendation"), real.get("exposure_ceiling_pct"), real.get("confidence")),
              flush=True)
        regime = advisory

    cmd = [sys.executable, str(GATE), "--answers-file", str(answers),
           "--state-dir", str(theses),
           "--circuit-breaker-decision", str(breaker),
           "--output-dir", str(work / "reports"),
           "--journal-dir", str(root / "state" / "journal")]
    # No posture file means the gate answers REVIEW_REQUIRED, which is the honest
    # result when the market could not be read.
    if regime:
        cmd += ["--market-regime-decision", str(regime)]
    if a.as_of:
        cmd += ["--as-of", a.as_of]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
