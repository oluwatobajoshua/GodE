#!/usr/bin/env python3
"""Canonical H1 bar loader — single tape for professor, playbook, and parity.

Priority (when canonical_bar_source=mt5 in configs/mt5_data_plane.json):
  1. research/data/mt5/{SYM}_H1.csv   — broker export (MetaTrader5 copy_rates)
  2. research/data/histdata/{SYM}_H1.csv
  3. research/data/{SYM}_H1.csv

Override: SOVEREIGN_BAR_SOURCE=mt5|histdata|legacy

Run audit: research/mt5_data_plane_audit.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "data"
MT5_DIR = DATA / "mt5"
HIST_DIR = DATA / "histdata"
CONFIG = REPO / "configs" / "mt5_data_plane.json"

_SOURCE_ORDER = {
    "mt5": (MT5_DIR, HIST_DIR, DATA),
    "histdata": (HIST_DIR, MT5_DIR, DATA),
    "legacy": (DATA, HIST_DIR, MT5_DIR),
}


def _load_config() -> dict[str, Any]:
    if not CONFIG.exists():
        return {"canonical_bar_source": "mt5"}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"canonical_bar_source": "mt5"}


def canonical_source() -> str:
    env = os.environ.get("SOVEREIGN_BAR_SOURCE", "").strip().lower()
    if env in _SOURCE_ORDER:
        return env
    cfg = _load_config()
    src = str(cfg.get("canonical_bar_source", "mt5")).lower()
    return src if src in _SOURCE_ORDER else "mt5"


def _label_for_dir(directory: Path) -> str:
    if directory == MT5_DIR:
        return "mt5"
    if directory == HIST_DIR:
        return "histdata"
    return "legacy"


def _resolve_path(sym: str, source: str | None = None, *, tf: str = "H1") -> tuple[Path | None, str]:
    sym = sym.upper()
    src = (source or canonical_source()).lower()
    dirs = _SOURCE_ORDER.get(src, _SOURCE_ORDER["mt5"])
    suffix = f"_{tf.upper()}.csv"
    for directory in dirs:
        p = directory / f"{sym}{suffix}"
        if p.exists():
            return p, _label_for_dir(directory)
    return None, "missing"


def resolve_h1_path(sym: str, source: str | None = None) -> Path | None:
    p, _ = _resolve_path(sym, source)
    return p


def bar_source_meta(sym: str, source: str | None = None) -> dict[str, Any]:
    sym = sym.upper()
    p, label = _resolve_path(sym, source)
    canonical = canonical_source()
    out: dict[str, Any] = {
        "symbol": sym,
        "canonical_preference": canonical,
        "resolved_source": label,
        "path": str(p) if p else None,
    }
    if p and p.exists():
        try:
            mtime = p.stat().st_mtime
            out["file_mtime"] = pd.Timestamp(mtime, unit="s", tz="UTC").isoformat()
            out["file_bytes"] = p.stat().st_size
        except OSError:
            pass
    alt: dict[str, str | None] = {}
    for name, directory in (("mt5", MT5_DIR), ("histdata", HIST_DIR), ("legacy", DATA)):
        ap = directory / f"{sym}_H1.csv"
        alt[name] = str(ap) if ap.exists() else None
    out["alternates_present"] = alt
    return out


def _normalize_h1(df: pd.DataFrame) -> pd.DataFrame:
    if "time" not in df.columns and df.index.name in ("time", "Time"):
        df = df.reset_index()
    if "time" not in df.columns:
        raise ValueError("H1 CSV missing time column")
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time"])
    df["time"] = df["time"].dt.tz_convert(None)
    for c in ("open", "high", "low", "close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "spread" in df.columns:
        df["spread"] = pd.to_numeric(df["spread"], errors="coerce")
    if "tick_volume" not in df.columns and "volume" in df.columns:
        df["tick_volume"] = pd.to_numeric(df["volume"], errors="coerce")
    elif "tick_volume" in df.columns:
        df["tick_volume"] = pd.to_numeric(df["tick_volume"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.set_index("time").sort_index()


def load_h1_raw(sym: str, source: str | None = None) -> pd.DataFrame:
    """Load H1 without canonical routing (explicit source only)."""
    sym = sym.upper()
    src = (source or canonical_source()).lower()
    directories = _SOURCE_ORDER.get(src, _SOURCE_ORDER["mt5"])
    for directory in directories:
        p = directory / f"{sym}_H1.csv"
        if p.exists():
            d = pd.read_csv(p)
            return _normalize_h1(d)
    raise FileNotFoundError(f"{sym} H1 not found (source={src}, searched={directories})")


def load_h1(sym: str, source: str | None = None) -> pd.DataFrame:
    """Canonical entry point for research + professor."""
    p, _ = _resolve_path(sym, source)
    if not p:
        raise FileNotFoundError(
            f"{sym.upper()} H1 missing. Export: .\\scripts\\sync_mt5_data_plane.ps1 -Export"
        )
    d = pd.read_csv(p)
    return _normalize_h1(d)


def load_h1_optional(sym: str, source: str) -> pd.DataFrame | None:
    sym = sym.upper()
    directory = {"mt5": MT5_DIR, "histdata": HIST_DIR, "legacy": DATA}.get(source)
    if not directory:
        return None
    p = directory / f"{sym}_H1.csv"
    if not p.exists():
        return None
    return _normalize_h1(pd.read_csv(p))


def m5_meta(sym: str) -> dict[str, Any]:
    """Describe which M5 tape load_m5_scalp will pick (broker vs long history)."""
    sym = sym.upper()
    meta: dict[str, Any] = {"symbol": sym}
    mt5_p, mt5_l = _resolve_path(sym, tf="M5")
    hist_p = HIST_DIR / f"{sym}_M5.csv"
    meta["mt5"] = {"path": str(mt5_p) if mt5_p else None, "source": mt5_l}
    meta["histdata"] = {"path": str(hist_p) if hist_p.exists() else None}
    if mt5_p and mt5_p.exists():
        try:
            t = pd.read_csv(mt5_p, usecols=["time"], nrows=2)
            t2 = pd.read_csv(mt5_p, usecols=["time"]).iloc[-1:]
            t0 = pd.to_datetime(t["time"].iloc[0], utc=True)
            t1 = pd.to_datetime(t2["time"].iloc[0], utc=True)
            meta["mt5"]["days"] = int((t1 - t0).days)
        except (OSError, ValueError, KeyError):
            pass
    if hist_p.exists():
        try:
            t = pd.read_csv(hist_p, usecols=["time"], nrows=1)
            t2 = pd.read_csv(hist_p, usecols=["time"]).iloc[-1:]
            meta["histdata"]["from"] = str(pd.to_datetime(t["time"].iloc[0], utc=True))
            meta["histdata"]["to"] = str(pd.to_datetime(t2["time"].iloc[0], utc=True))
        except (OSError, ValueError, KeyError):
            pass
    return meta


def load_m5_mt5_only(sym: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Broker M5 only — same tape as Strategy Tester (see docs/MT5_DATA_PLANE.md)."""
    sym = sym.upper()
    p, label = _resolve_path(sym, source="mt5", tf="M5")
    if not p or label != "mt5":
        raise FileNotFoundError(
            f"{sym} M5 missing. Export: .\\scripts\\run_scalp_lane.ps1 -Export "
            f"or .\\scripts\\sync_mt5_data_plane.ps1 -Export -ScalpM5"
        )
    df = _normalize_h1(pd.read_csv(p))
    meta = m5_meta(sym)
    meta["chosen"] = "mt5"
    meta["research_source"] = "mt5_only"
    return df, meta


def _scalp_m5_policy() -> str:
    env = os.environ.get("SOVEREIGN_M5_SOURCE", "").strip().lower()
    if env in ("mt5", "mt5_only"):
        return "mt5_only"
    if env == "histdata_blend":
        return "histdata_blend"
    scalp_cfg = REPO / "configs" / "scalp_lane.json"
    if scalp_cfg.exists():
        try:
            bars = json.loads(scalp_cfg.read_text(encoding="utf-8-sig")).get("bars") or {}
            src = str(bars.get("research_source", "mt5_only")).lower()
            if src == "mt5_only":
                return "mt5_only"
            if src == "histdata_blend":
                return "histdata_blend"
        except (json.JSONDecodeError, OSError):
            pass
    return "mt5_only"


def load_m5_scalp(sym: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """M5 for scalping — **default broker tape only** (configs/scalp_lane.json).

    HistData blend is exploration-only: ``SOVEREIGN_M5_SOURCE=histdata_blend``.
    """
    if _scalp_m5_policy() == "mt5_only":
        return load_m5_mt5_only(sym)
    sym = sym.upper()
    meta = m5_meta(sym)
    hist_p = HIST_DIR / f"{sym}_M5.csv"
    mt5_p, _ = _resolve_path(sym, tf="M5")
    use_hist = False
    mt5_days = int(meta.get("mt5", {}).get("days") or 0)
    if hist_p.exists():
        use_hist = mt5_days < 400 or not mt5_p
    if use_hist:
        df = _normalize_h1(pd.read_csv(hist_p))
        if "spread" not in df.columns:
            df["spread"] = 10.0
        med_spread = 10.0
        if mt5_p and mt5_p.exists():
            try:
                s = pd.read_csv(mt5_p, usecols=["spread"])["spread"]
                med_spread = float(s.median())
                df.loc[df.index >= pd.Timestamp("2024-01-01"), "spread"] = med_spread
            except (OSError, ValueError, KeyError):
                pass
        meta["chosen"] = "histdata_calibrated"
        meta["spread_median_points"] = med_spread
        return df, meta
    if mt5_p:
        df = _normalize_h1(pd.read_csv(mt5_p))
        meta["chosen"] = "mt5"
        if mt5_days < 400:
            meta["warning"] = (
                f"broker M5 only ~{mt5_days} days — run research/build_histdata_m5.py for 3-split proof"
            )
        return df, meta
    raise FileNotFoundError(
        f"{sym} M5 missing. Export MT5 or build research/data/histdata/{sym}_M5.csv"
    )


def load_m5(sym: str, source: str | None = None) -> pd.DataFrame:
    """Canonical M5 (mt5 if long enough, else histdata via load_m5_scalp)."""
    if source:
        p, _ = _resolve_path(sym, source, tf="M5")
        if not p:
            raise FileNotFoundError(sym)
        return _normalize_h1(pd.read_csv(p))
    df, _ = load_m5_scalp(sym)
    return df
