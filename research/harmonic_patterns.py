#!/usr/bin/env python3
"""Detect ABCD / harmonic (XABCD) patterns from CONFIRMED swings, for drawing.

This is geometry, not a signal. The project has already measured that the visual
patterns it could test carry no forward drift, so nothing here gates a trade - it
exists so the chart can show the same shapes a Butterfly/Harmonic/ABCD indicator
draws, and so the levels it uses are real swing points rather than invented ones.

CAUSALITY
  A k-bar fractal swing at bar i cannot be known until bar i+k. Every pivot used
  here is therefore only admitted once i+k <= last_bar, which is why a pattern is
  drawn with a confirmation lag instead of at the exact turning point. That is the
  honest version; the leaky version looks better and is wrong.

RATIOS (the standard definitions)
  XA = |A-X|, AB = |B-A|, BC = |C-B|, CD = |D-C|, XD = |D-X|
  r_ab = AB/XA, r_bc = BC/AB, r_cd = CD/BC, r_xd = XD/XA

Usage (imported by llm_chart_analyst / chart_annotate)
    from harmonic_patterns import find_patterns
    pats = find_patterns(high, low, k=2, last_bar=n - 1)
"""

from __future__ import annotations

import numpy as np

#--- (name, r_ab window, r_xd window) - tolerance is deliberately wide, because a
#--- tight window on a 5-point ratio chain mostly detects noise
HARMONIC_RULES = (
    ("Butterfly", (0.72, 0.86), (1.18, 1.70)),
    ("Bat",       (0.33, 0.55), (0.82, 0.95)),
    ("Gartley",   (0.55, 0.70), (0.72, 0.86)),
    ("Crab",      (0.33, 0.68), (1.52, 1.75)),
    ("DeepCrab",  (0.82, 0.97), (1.52, 1.75)),
    ("Shark",     (0.33, 0.68), (0.84, 1.16)),
)


def _pivots(high: np.ndarray, low: np.ndarray, k: int, last_bar: int):
    """Confirmed alternating pivots, ordered by bar index."""
    from structure_quality import swings
    up_s, dn_s = swings(high, low, k)
    raw = []
    for i in range(len(high)):
        if i + k > last_bar:          #--- not confirmable yet
            break
        if up_s[i]:
            raw.append(dict(idx=i, price=float(high[i]), kind="H"))
        if dn_s[i]:
            raw.append(dict(idx=i, price=float(low[i]), kind="L"))
    raw.sort(key=lambda p: p["idx"])

    zz = []
    for p in raw:
        if not zz:
            zz.append(p)
            continue
        if p["kind"] == zz[-1]["kind"]:
            more_extreme = (p["price"] > zz[-1]["price"] if p["kind"] == "H"
                            else p["price"] < zz[-1]["price"])
            if more_extreme:
                zz[-1] = p
        else:
            zz.append(p)
    return zz


def _ratio(a: float, b: float) -> float:
    return abs(a) / abs(b) if abs(b) > 1e-12 else 0.0


def classify_xabcd(pts) -> tuple[str, dict] | None:
    """pts: five alternating pivots X,A,B,C,D."""
    if len(pts) != 5:
        return None
    px = [p["price"] for p in pts]
    xa = px[1] - px[0]
    ab = px[2] - px[1]
    bc = px[3] - px[2]
    cd = px[4] - px[3]
    if abs(xa) < 1e-12 or abs(ab) < 1e-12 or abs(bc) < 1e-12:
        return None
    r_ab = _ratio(ab, xa)
    r_bc = _ratio(bc, ab)
    r_cd = _ratio(cd, bc)
    r_xd = _ratio(px[4] - px[0], xa)
    ratios = dict(r_ab=round(r_ab, 3), r_bc=round(r_bc, 3),
                  r_cd=round(r_cd, 3), r_xd=round(r_xd, 3))

    #--- BC/CD sanity shared by every harmonic
    if not (0.30 <= r_bc <= 1.00 and 0.90 <= r_cd <= 2.20):
        return None

    for name, ab_win, xd_win in HARMONIC_RULES:
        if ab_win[0] <= r_ab <= ab_win[1] and xd_win[0] <= r_xd <= xd_win[1]:
            return name, ratios
    return None


def classify_abcd(pts) -> tuple[str, dict] | None:
    """pts: four alternating pivots A,B,C,D (the plain ABCD)."""
    if len(pts) != 4:
        return None
    px = [p["price"] for p in pts]
    ab = px[1] - px[0]
    bc = px[2] - px[1]
    cd = px[3] - px[2]
    if abs(ab) < 1e-12 or abs(bc) < 1e-12:
        return None
    r_bc = _ratio(bc, ab)
    r_cd = _ratio(cd, bc)
    ratios = dict(r_bc=round(r_bc, 3), r_cd=round(r_cd, 3))
    if 0.50 <= r_bc <= 0.90 and 1.10 <= r_cd <= 1.85:
        return "ABCD", ratios
    return None


def find_patterns(high: np.ndarray, low: np.ndarray, k: int = 2,
                  last_bar: int | None = None, max_patterns: int = 2):
    """Return the most recent ABCD / harmonic patterns, newest first.

    Each pattern is a dict with name, direction and an ordered point list whose
    bar offsets are negative (bars back from last_bar), ready to draw.
    """
    n = len(high)
    if last_bar is None:
        last_bar = n - 1
    zz = _pivots(high, low, k, last_bar)
    if len(zz) < 4:
        return []

    found = []

    def emit(name, pts, ratios, family):
        offs = [dict(label=lab, bar_offset=int(p["idx"] - last_bar),
                     price=round(float(p["price"]), 5))
                for lab, p in zip("XABCD"[-len(pts):], pts)]
        #--- direction from the final pivot: a pattern ending on a low is bullish
        direction = "bullish" if pts[-1]["kind"] == "L" else "bearish"
        found.append(dict(name=name, family=family, direction=direction,
                          points=offs, ratios=ratios,
                          start_bar_offset=int(pts[0]["idx"] - last_bar),
                          end_bar_offset=int(pts[-1]["idx"] - last_bar)))

    #--- harmonic: newest 5-pivot window first
    for i in range(len(zz) - 4, -1, -1):
        if len(found) >= max_patterns:
            break
        win = zz[i:i + 5]
        got = classify_xabcd(win)
        if got:
            emit(got[0], win, got[1], "harmonic")

    #--- plain ABCD on the newest 4-pivot window
    if len(found) < max_patterns:
        win = zz[-4:]
        got = classify_abcd(win)
        if got:
            emit(got[0], win, got[1], "abcd")

    found.sort(key=lambda p: p["end_bar_offset"], reverse=True)
    return found[:max_patterns]
