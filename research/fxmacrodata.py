#!/usr/bin/env python3
"""Small, dependency-light client for the FXMacroData REST API.

Why this exists
---------------
The EA needs a real, timestamped macro/event input instead of synthetic
sentiment.  FXMacroData exposes official-source release calendars and
announcement records.  USD data is available without a key for the most recent
90 days; an API key unlocks the full history and the other 21 currencies.

The client deliberately uses only the standard library plus pandas so it can
run inside the existing research venv.  Every response is cached as raw JSON,
which keeps the research reproducible and avoids hammering the free endpoint.

Environment
-----------
FXMACRODATA_API_KEY   optional; required for non-USD currencies and full history

Usage
-----
    from research.fxmacrodata import FXMacroData

    api = FXMacroData()
    cal = api.calendar("USD", "2026-07-01", "2026-10-26")
    risk = api.risk_sentiment("2026-01-01", "2026-10-26")
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "data" / "fxmacrodata" / "raw"
BASE_URL = "https://api.fxmacrodata.com"
USER_AGENT = "GodE-research/1.0"


class FXMacroDataError(RuntimeError):
    """Raised when the API rejects a request or returns an invalid payload."""


def _as_iso(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class FXMacroData:
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | str = CACHE_DIR,
        cache_ttl_seconds: int = 6 * 60 * 60,
        timeout: int = 45,
        force_refresh: bool = False,
    ) -> None:
        self.api_key = api_key or os.environ.get("FXMACRODATA_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout = timeout
        self.force_refresh = force_refresh

    # ------------------------------------------------------------------ HTTP
    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        payload = json.dumps([path, params], sort_keys=True, default=str)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
        safe = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
        return self.cache_dir / f"{safe}_{digest}.json"

    def _load_cache(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl_seconds and not self.force_refresh:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        cache_path = self._cache_path(path, clean)
        if not force and not self.force_refresh:
            cached = self._load_cache(cache_path)
            if cached is not None:
                return cached

        url = BASE_URL + path
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(body)
            except json.JSONDecodeError:
                detail = {"detail": body[:500]}
            message = detail.get("detail") or detail.get("error") or f"HTTP {exc.code}"
            if exc.code == 401:
                message += (
                    " | Set FXMACRODATA_API_KEY for non-USD currencies and full history."
                )
            raise FXMacroDataError(f"{exc.code} {path}: {message}") from exc
        except urllib.error.URLError as exc:
            raise FXMacroDataError(f"network error for {path}: {exc.reason}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FXMacroDataError(f"invalid JSON from {path}") from exc
        if not isinstance(payload, dict):
            raise FXMacroDataError(f"unexpected payload from {path}: {type(payload).__name__}")

        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(cache_path)
        return payload

    # -------------------------------------------------------------- calendar
    def calendar(
        self,
        currency: str = "USD",
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        force: bool = False,
    ) -> pd.DataFrame:
        """Return scheduled macro releases with UTC timestamps.

        The free tier exposes the most recent ~90 days.  The response carries
        ``history_truncated`` and ``history_start_date``; keep those in
        ``DataFrame.attrs`` so downstream analysis can refuse to imply more
        coverage than it has.
        """
        currency = currency.upper()
        params = {"start_date": _as_iso(start), "end_date": _as_iso(end)}
        payload = self._request(f"/v1/calendar/{currency}", params, force=force)
        rows = payload.get("data") or []
        df = pd.DataFrame(rows)
        if df.empty:
            empty = pd.DataFrame(
                columns=[
                    "event_id",
                    "currency",
                    "release",
                    "name",
                    "event_time",
                    "event_date",
                    "has_time",
                    "importance",
                    "market_tier",
                    "top_tier",
                    "source",
                    "source_url",
                    "reference_period",
                ]
            )
            empty.attrs.update({k: v for k, v in payload.items() if k != "data"})
            return empty

        df["currency"] = currency
        df["event_id"] = df.get("calendar_event_id")
        df["release"] = df.get("release")
        df["name"] = df.get("name")
        df["importance"] = df.get("event_importance", "unknown").astype(str).str.lower()
        df["market_tier"] = pd.to_numeric(df.get("market_tier"), errors="coerce")
        df["top_tier"] = df.get("top_tier_for_currency", False).fillna(False).astype(bool)
        df["source"] = df.get("source")
        df["source_url"] = df.get("source_url")
        df["reference_period"] = df.get("reference_period")

        utc = pd.to_datetime(df.get("announcement_datetime_utc"), utc=True, errors="coerce")
        epoch = pd.to_numeric(df.get("announcement_datetime"), errors="coerce")
        fallback = pd.to_datetime(epoch, unit="s", utc=True, errors="coerce")
        df["event_time"] = utc.fillna(fallback)
        df["has_time"] = df["event_time"].notna()

        date_col = pd.to_datetime(df.get("date"), errors="coerce")
        df["event_date"] = date_col.dt.date
        df.loc[df["event_date"].isna(), "event_date"] = df.loc[
            df["event_date"].isna(), "event_time"
        ].dt.date

        keep = [
            "event_id",
            "currency",
            "release",
            "name",
            "event_time",
            "event_date",
            "has_time",
            "importance",
            "market_tier",
            "top_tier",
            "source",
            "source_url",
            "reference_period",
        ]
        out = df[keep].sort_values(["event_time", "importance"], na_position="last")
        out = out.reset_index(drop=True)
        out.attrs.update({k: v for k, v in payload.items() if k != "data"})
        return out

    # ---------------------------------------------------------- risk sentiment
    def risk_sentiment(
        self,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        force: bool = False,
    ) -> pd.DataFrame:
        """Return the daily risk-on/risk-off composite.

        The endpoint is available without a key for USD/free usage.  It is a
        derived, backfilled series: ``point_in_time_safe`` is false in the API
        metadata.  Use it as a live/regime context feature, not as a clean
        backtest input unless you reconstruct the components yourself.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        meta: dict[str, Any] = {}
        while True:
            payload = self._request(
                "/v1/risk_sentiment",
                {
                    "start_date": _as_iso(start),
                    "end_date": _as_iso(end),
                    "limit": 100,
                    "offset": offset,
                },
                force=force,
            )
            rows.extend(payload.get("data") or [])
            meta = {k: v for k, v in payload.items() if k != "data"}
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            next_offset = pagination.get("next_offset")
            if next_offset is None or next_offset <= offset:
                break
            offset = int(next_offset)

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df["risk_score"] = pd.to_numeric(df.get("val"), errors="coerce")
        df["risk_regime"] = df.get("regime")
        df["provisional"] = df.get("provisional", False).fillna(False).astype(bool)
        df["coverage_status"] = df.get("coverage_status")
        df["last_changed_at"] = pd.to_datetime(
            df.get("last_changed_at"), utc=True, errors="coerce"
        )
        # Conservative availability: a daily value is usable on the next UTC day.
        df["available_at"] = pd.to_datetime(df["date"], utc=True) + pd.Timedelta(days=1)
        keep = [
            "date",
            "available_at",
            "risk_score",
            "risk_regime",
            "provisional",
            "coverage_status",
            "last_changed_at",
        ]
        out = df[keep].sort_values("date").reset_index(drop=True)
        out.attrs.update(meta)
        out.attrs["point_in_time_safe"] = bool(
            (meta.get("data_quality") or {}).get("point_in_time_safe", False)
        )
        return out

    # ------------------------------------------------------------ announcements
    def announcements(
        self,
        currency: str,
        indicator: str,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        revisions: str = "latest",
        force: bool = False,
    ) -> pd.DataFrame:
        """Return historical observations for one indicator."""
        rows: list[dict[str, Any]] = []
        offset = 0
        meta: dict[str, Any] = {}
        while True:
            payload = self._request(
                f"/v1/announcements/{currency.upper()}/{indicator}",
                {
                    "start_date": _as_iso(start),
                    "end_date": _as_iso(end),
                    "limit": 100,
                    "offset": offset,
                    "revisions": revisions,
                },
                force=force,
            )
            rows.extend(payload.get("data") or [])
            meta = {k: v for k, v in payload.items() if k != "data"}
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            next_offset = pagination.get("next_offset")
            if next_offset is None or next_offset <= offset:
                break
            offset = int(next_offset)

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["currency"] = currency.upper()
        df["indicator"] = indicator
        df["date"] = pd.to_datetime(df.get("date"), errors="coerce").dt.date
        df["value"] = pd.to_numeric(df.get("val"), errors="coerce")
        df["previous_value"] = pd.to_numeric(df.get("previous_value"), errors="coerce")
        df["change"] = pd.to_numeric(df.get("change_from_previous"), errors="coerce")
        epoch = pd.to_numeric(df.get("announcement_datetime"), errors="coerce")
        df["announcement_time"] = pd.to_datetime(epoch, unit="s", utc=True, errors="coerce")
        df["announcement_time_local"] = pd.to_datetime(
            df.get("announcement_datetime_local"), utc=True, errors="coerce"
        )
        keep = [
            "currency",
            "indicator",
            "date",
            "announcement_time",
            "value",
            "previous_value",
            "change",
            "source",
            "source_url",
        ]
        out = df[[c for c in keep if c in df.columns]].sort_values("announcement_time")
        out = out.reset_index(drop=True)
        out.attrs.update(meta)
        return out

    def data_catalogue(self, currency: str = "USD", force: bool = False) -> dict[str, Any]:
        return self._request(f"/v1/data_catalogue/{currency.upper()}", force=force)


def pair_currencies(pair: str) -> tuple[str, str]:
    pair = pair.upper().replace("/", "").replace("-", "")
    if len(pair) != 6:
        raise ValueError(f"Expected a six-letter FX pair, got {pair!r}")
    return pair[:3], pair[3:]


def calendar_for_pair(
    api: FXMacroData,
    pair: str = "EURUSD",
    start: str | date | datetime | None = None,
    end: str | date | datetime | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """Fetch both legs of a pair; missing non-USD legs are skipped gracefully."""
    base, quote = pair_currencies(pair)
    frames = []
    for currency in dict.fromkeys([base, quote]):
        try:
            frame = api.calendar(currency, start, end, force=force)
        except FXMacroDataError as exc:
            if "api_key" not in str(exc).lower():
                raise
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("event_time").reset_index(drop=True)
