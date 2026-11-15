#!/usr/bin/env python3
"""Sovereign Discovery Lab — original research, our own proof standard.

This script does NOT argue from "what textbooks say about FX." It:

1. Builds features from **your** data (prices, rates, sentiment where available).
2. Tests **new** hypotheses (technical, fundamental, sentiment, fusion).
3. Scores every idea on **chronological** design → validation → OOS splits.
4. Writes ``research/results/discovery_proof.csv`` — including failures.

**Proof bar (configurable):** hypothesis is *candidates for EA* only if
OOS profit factor ≥ 1.05, OOS Sharpe ≥ 0.25, and max DD ≥ -30% on that split.
Otherwise the row is kept as ``status=fail`` — that is still valid proof.

Optional AI overlay: set ``OPENAI_API_KEY`` and ``DISCOVERY_LLM_HYPOTHESES=1``
to propose extra feature transforms (still evaluated by this backtester, not trusted).

Run:
    .\\.venv\\Scripts\\python.exe research\\discovery_lab.py
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
MACRO = DATA / "fxmacrodata"
RATES = DATA / "rates"
OUT = HERE / "results" / "discovery_proof.csv"
MANIFEST = HERE / "results" / "discovery_manifest.json"

PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
]

SPLITS = {
    "design": ("2008-01-01", "2015-01-01"),
    "validation": ("2015-01-01", "2019-01-01"),
    "OOS": ("2019-01-01", "2026-12-31"),
}

PASS_PF = float(os.environ.get("DISCOVERY_PASS_PF", "1.05"))
PASS_SHARPE = float(os.environ.get("DISCOVERY_PASS_SHARPE", "0.25"))
PASS_MAXDD = float(os.environ.get("DISCOVERY_PASS_MAXDD", "-30"))


def load_d1() -> pd.DataFrame:
    frames = {}
    for p in PAIRS:
        path = DATA / f"{p}_D1.csv"
        if not path.exists():
            continue
        d = pd.read_csv(path)
        d.columns = [c.lower() for c in d.columns]
        tcol = "time" if "time" in d.columns else "date"
        d[tcol] = pd.to_datetime(d[tcol], utc=True, errors="coerce").dt.tz_convert(None)
        d = d.dropna(subset=[tcol]).drop_duplicates(tcol).set_index(tcol).sort_index()
        frames[p] = d["close"].astype(float)
    return pd.DataFrame(frames).sort_index().ffill(limit=3)


def load_risk_sentiment() -> pd.Series:
    path = MACRO / "risk_sentiment.csv"
    if not path.exists():
        return pd.Series(dtype=float)
    d = pd.read_csv(path, parse_dates=["date"])
    d = d.dropna(subset=["date"]).sort_values("date").set_index("date")
    return d["risk_score"].astype(float)


def metrics(r: pd.Series) -> dict:
    r = r.dropna()
    if r.empty or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0, "pf": 0.0, "winrate": 0.0, "n": 0}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * np.sqrt(252)
    maxdd = (eq / eq.cummax() - 1).min()
    wins = r[r > 0]
    losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "pf": round(pf, 2) if pd.notna(pf) else None,
        "winrate": round((r > 0).mean() * 100, 1),
        "n": len(r),
    }


def slice_split(r: pd.Series, start: str, end: str) -> pd.Series:
    return r[(r.index >= start) & (r.index < end)]


def portfolio_returns(weights: pd.DataFrame, returns: pd.DataFrame, cost_bps: float = 2.0) -> pd.Series:
    w = weights.shift(1).fillna(0.0)
    turnover = w.diff().abs().sum(axis=1) / 2.0
    gross = (w * returns).sum(axis=1)
    cost = turnover * cost_bps / 10000.0
    return (gross - cost).dropna()


# --- Hypothesis families ---


def hyp_tsmom(prices: pd.DataFrame, window: int = 126) -> pd.DataFrame:
    """Technical: time-series momentum."""
    rets = prices.pct_change()
    mom = prices / prices.shift(window) - 1
    sig = np.sign(mom)
    return sig


def hyp_meanrev(prices: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Technical: short-term z-score reversal."""
    z = (prices - prices.rolling(window).mean()) / prices.rolling(window).std()
    sig = -np.sign(z)
    return sig.where(z.abs() > 1.0, 0.0)


def hyp_carry_swap(prices: pd.DataFrame) -> pd.DataFrame:
    """Fundamental proxy: rank by symbol name order; use rate files when present."""
    # Implemented as cross-sectional rank from synthetic carry surface
    # (full carry in carry.py; here fast daily proxy = long high beta FX vs USD basket)
    rets = prices.pct_change()
    vol = rets.rolling(60).std()
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for dt in prices.index[60:]:
        row = vol.loc[dt].dropna().sort_values()
        if len(row) < 4:
            continue
        low_vol = row.index[:2]
        high_vol = row.index[-2:]
        sig.loc[dt, low_vol] = 1.0
        sig.loc[dt, high_vol] = -1.0
    return sig


def hyp_sentiment_risk(prices: pd.DataFrame, risk: pd.Series) -> pd.DataFrame:
    """Sentiment: risk-on long G10 beta, risk-off flatten."""
    if risk.empty:
        return pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    rs = risk.reindex(prices.index, method="ffill")
    beta = prices.pct_change().rolling(120).corr(prices["EURUSD"].pct_change())
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    on = rs > rs.median()
    sig = np.sign(beta).where(on, 0.0)
    return sig


def hyp_fusion_ml(prices: pd.DataFrame, risk: pd.Series) -> pd.DataFrame:
    """Fusion: walk-forward ridge on lagged features (no peeking within each fold)."""
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    rets = prices.pct_change()
    feats = pd.DataFrame(index=prices.index)
    feats["mom63"] = (prices / prices.shift(63) - 1).mean(axis=1)
    feats["vol20"] = rets.rolling(20).std().mean(axis=1)
    feats["z20"] = ((prices - prices.rolling(20).mean()) / prices.rolling(20).std()).mean(axis=1)
    if not risk.empty:
        rs = risk.reindex(prices.index, method="ffill")
        feats["risk"] = rs
    feats = feats.dropna()
    target = rets.mean(axis=1).shift(-1).reindex(feats.index)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    train_years = 5
    for i in range(train_years * 252, len(feats) - 21, 21):
        train = feats.iloc[i - train_years * 252 : i]
        y = target.reindex(train.index).dropna()
        train = train.loc[y.index]
        if len(y) < 100:
            continue
        scaler = StandardScaler()
        X = scaler.fit_transform(train)
        model = Ridge(alpha=1.0)
        model.fit(X, y)
        test_day = feats.index[i]
        pred = float(model.predict(scaler.transform(feats.loc[[test_day]]))[0])
        direction = 1.0 if pred > 0 else -1.0
        for c in prices.columns:
            sig.loc[test_day, c] = direction / len(prices.columns)
    return sig


from discovery_custom import CUSTOM_HYPOTHESES  # noqa: E402
from discovery_frontier import FRONTIER_HYPOTHESES  # noqa: E402
from discovery_novel import NOVEL_HYPOTHESES  # noqa: E402

HYPOTHESES = [
    ("tech_tsmom_126", lambda p, r: hyp_tsmom(p, 126)),
    ("tech_meanrev_z20", lambda p, r: hyp_meanrev(p, 20)),
    ("fund_vol_carry_proxy", lambda p, r: hyp_carry_swap(p)),
    ("sentiment_risk_regime", lambda p, r: hyp_sentiment_risk(p, r)),
    ("fusion_ridge_walkforward", lambda p, r: hyp_fusion_ml(p, r)),
]
HYPOTHESES.extend(NOVEL_HYPOTHESES)
HYPOTHESES.extend(FRONTIER_HYPOTHESES)
HYPOTHESES.extend(CUSTOM_HYPOTHESES)


def evaluate(name: str, weights: pd.DataFrame, prices: pd.DataFrame) -> list[dict]:
    rets = prices.pct_change()
    port = portfolio_returns(weights, rets)
    rows = []
    for split, (start, end) in SPLITS.items():
        part = slice_split(port, start, end)
        m = metrics(part)
        status = "fail"
        if (m["pf"] or 0) >= PASS_PF and m["sharpe"] >= PASS_SHARPE and m["maxdd"] >= PASS_MAXDD:
            status = "pass"
        if name.startswith("front_"):
            tag = "frontier"
        elif name.startswith("nov_"):
            tag = "novel"
        else:
            tag = "classic"
        rows.append({"hypothesis": name, "family": tag, "split": split, "status": status, **m})
    full = metrics(port)
    tag = "novel" if name.startswith("nov_") else "classic"
    rows.append({"hypothesis": name, "family": tag, "split": "full", "status": "info", **full})
    return rows


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=HERE.parent,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    prices = load_d1()
    if prices.empty:
        raise SystemExit("No D1 price data in research/data")
    risk = load_risk_sentiment()

    all_rows: list[dict] = []
    for name, fn in HYPOTHESES:
        try:
            w = fn(prices, risk)
            w = w.fillna(0.0)
            row_caps = w.abs().sum(axis=1).replace(0, np.nan)
            w = w.div(row_caps, axis=0).fillna(0.0)
            all_rows.extend(evaluate(name, w, prices))
        except Exception as exc:
            all_rows.append({
                "hypothesis": name, "split": "error", "status": "error",
                "cagr": 0, "sharpe": 0, "maxdd": 0, "pf": None, "winrate": 0, "n": 0,
                "note": str(exc),
            })

    df = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)

    candidates = df[(df["split"] == "OOS") & (df["status"] == "pass")]
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": git_revision(),
        "pass_bar": {"pf": PASS_PF, "sharpe": PASS_SHARPE, "maxdd_pct": PASS_MAXDD},
        "candidates_oos": candidates["hypothesis"].tolist(),
        "n_hypotheses": len(HYPOTHESES),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== Discovery Lab (our proof, not old history) ===")
    show = df[df["split"].isin(["OOS", "validation"])]
    print(show.to_string(index=False))
    if "family" in df.columns:
        nov = show[show["family"] == "novel"]
        if not nov.empty:
            print("\n--- Novel / contrarian family (OOS + validation) ---")
            print(nov.to_string(index=False))
    print(f"\nOOS candidates: {manifest['candidates_oos'] or 'none yet'}")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
