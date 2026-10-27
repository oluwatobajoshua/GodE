#!/usr/bin/env python3
"""Independent metrics for the EA's M5 mean-reversion engine (mode 0).

This reads the candidates produced by `m5_independent.py` and reports the mode-0
mean-reversion setup over the EA's 07:00-16:59 UTC session.  It does not rerun
the generator, so it is cheap and reproducible from the saved candidate CSV.

Run after `m5_independent.py`:
    .\\.venv\\Scripts\\python.exe research/m5_meanrev_independent.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "histdata"
CAND = OUT / "m5_candidates_histdata.csv"


def block(d: pd.DataFrame, label: str) -> dict:
    if d.empty:
        return {"label": label, "n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    w = d["r_multiple"] > 0
    gp = d.loc[w, "r_multiple"].sum()
    gl = -d.loc[~w, "r_multiple"].sum()
    return {
        "label": label,
        "n": int(len(d)),
        "win": round(float(w.mean() * 100), 1),
        "avg": round(float(d["r_multiple"].mean()), 3),
        "total": round(float(d["r_multiple"].sum()), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else 999.0,
    }


def main() -> int:
    d = pd.read_csv(CAND, parse_dates=["signal_time", "entry_time", "exit_time"])
    m0 = d[d["mode"] == 0].copy()
    m0["year"] = m0["signal_time"].dt.year
    m0["signal_hour"] = m0["signal_time"].dt.hour
    session = m0[(m0["signal_hour"] >= 7) & (m0["signal_hour"] <= 16)].copy()

    rows = []
    for hour in range(24):
        rows.append({"scope": f"mode0 hour {hour}", **block(m0[m0["signal_hour"] == hour], f"hour {hour}")})
    rows.append({"scope": "mode0 session_7_16", **block(session, "session 7-16")})
    for year, part in session.groupby("year"):
        rows.append({"scope": f"mode0 session {year}", **block(part, str(year))})
    for label, lo, hi in [
        ("design_2011_2014", 2011, 2015),
        ("validation_2015_2019", 2015, 2020),
        ("OOS_2020_2025", 2020, 2026),
        ("full_2011_2025", 2011, 2026),
    ]:
        part = session[(session["year"] >= lo) & (session["year"] < hi)]
        rows.append({"scope": label, **block(part, label)})

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "m5_meanrev_independent.csv", index=False)
    print("=== M5 mode-0 mean reversion by hour, all years ===")
    print(out[out["scope"].str.match(r"mode0 hour \d+$")].to_string(index=False))
    print("\n=== M5 mode-0 session 7-16 by year ===")
    print(out[out["scope"].str.match(r"mode0 session \d{4}")].to_string(index=False))
    print("\n=== M5 mode-0 session 7-16 by split ===")
    print(out[out["scope"].str.startswith(("design_", "validation_", "OOS_", "full_"))].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
