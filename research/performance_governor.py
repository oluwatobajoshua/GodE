#!/usr/bin/env python3
"""Walk-forward Sovereign performance governor on trade outcomes (FXMOPOL buckets)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class GovBucket:
    n_eff: float = 0.0
    wins_eff: float = 0.0
    sum_r_eff: float = 0.0
    blocked_until: pd.Timestamp | None = None


@dataclass
class PerformanceGovernor:
    enabled: bool = True
    recent: list[float] = field(default_factory=list)
    buckets: dict[str, GovBucket] = field(default_factory=dict)
    master: GovBucket = field(default_factory=GovBucket)

    def _bucket_mult(self, key: str, fx: bool = True, now: pd.Timestamp | None = None) -> float:
        b = self.buckets.setdefault(key, GovBucket())
        if b.blocked_until is not None and now is not None and now < b.blocked_until:
            return 0.0
        if b.blocked_until is not None and now is not None and now >= b.blocked_until:
            b.blocked_until = None
            b.n_eff = 0.0
            b.wins_eff = 0.0
            b.sum_r_eff = 0.0
        n = b.n_eff
        wr = (b.wins_eff + 5.0) / (n + 10.0)
        score = (b.sum_r_eff) / max(n, 1e-9)
        block_n = 8.0 if fx else 12.0
        block_score = -0.03 if fx else -0.05
        wr_block = 0.38 if fx else 0.34
        if n >= block_n and (score <= block_score or wr < wr_block):
            if now is not None:
                b.blocked_until = now + pd.Timedelta(days=14)
            return 0.0
        if fx and n >= 5.0 and score < -0.12:
            return 0.0
        if n >= 6.0 and score < -0.10:
            return 0.5
        if score < 0.02:
            return 0.5
        if score >= 0.12 and wr >= 0.50:
            return 1.0
        return 0.75

    def combined_mult(self, leg_key: str, now: pd.Timestamp | None = None) -> float:
        if not self.enabled:
            return 1.0
        m = self._bucket_mult("SOV_MASTER", fx=False, now=now)
        if m <= 0:
            return 0.0
        leg = self._bucket_mult(leg_key, fx=True, now=now)
        if leg <= 0:
            return 0.0
        exp = 1.0
        if len(self.recent) >= 8:
            avg = sum(self.recent[-20:]) / min(20, len(self.recent))
            if avg <= -0.20:
                exp = 0.55
            elif avg <= -0.08:
                exp = 0.75
            elif avg >= 0.15:
                exp = 1.12
            elif avg >= 0.06:
                exp = 1.06
        return min(m, leg) * exp

    def record(self, leg_key: str, ret_pct: float) -> None:
        r = ret_pct / 100.0
        self.recent.append(r)
        for key in (leg_key, "SOV_MASTER"):
            b = self.buckets.setdefault(key, GovBucket())
            b.n_eff += 1.0
            b.wins_eff += 1.0 if r > 0 else 0.0
            b.sum_r_eff += r


def apply_governor(trades: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Drop trades governor blocks; scale returns on survivors (walk-forward)."""
    if trades.empty:
        return trades
    gov = PerformanceGovernor(enabled=True)
    leg = f"SOV-FXMOPOL|{symbol.upper()}"
    out_rows = []
    for _, row in trades.sort_values("exit_time").iterrows():
        mult = gov.combined_mult(leg, now=pd.Timestamp(row["entry_time"]))
        if mult <= 0.01:
            continue
        scaled = row.copy()
        scaled["return_pct"] = float(row["return_pct"]) * mult
        scaled["governor_mult"] = mult
        out_rows.append(scaled)
        gov.record(leg, float(row["return_pct"]))
    return pd.DataFrame(out_rows) if out_rows else pd.DataFrame(columns=trades.columns)
