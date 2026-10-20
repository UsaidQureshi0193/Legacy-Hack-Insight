"""Source 2 - Open-Meteo hourly weather for each district centroid (stored in UTC).

* archive API  (ERA5 reanalysis, ~5 day delay): one raw JSON per (location chunk, calendar year).
  Past years never change: once in the manifest with a valid hash they are never fetched again.
* forecast API (updates hourly): last 7 days + next 7 days, at most every FORECAST_EVERY_H hours.

Free tier = non-commercial, < 600 calls/min, < 5,000/hour, < 10,000/day, where a request counts
as (locations x 2-week blocks) calls. 100 districts x 4 years is ~10,000 calls, so history cannot
be fetched in one go without breaking the terms. A persistent call ledger keeps every request
under 90% of each limit; when the budget is used up, fetching is deferred to the next hourly run
(the pipeline continues with the weather it already has) - and a 429 stops fetching immediately.
"""
from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .. import config, http
from ..manifest import Manifest

RAW_DIR = config.RAW / "open_meteo"
LEDGER = config.ARTIFACTS / "open_meteo_ledger.json"   # outside data/ so `make data` never forgets spend
CHUNK = 10
LIMITS = {"minute": 540, "hour": 4500, "day": 9000}     # 90% of the published free-tier limits
# History may use the day budget only up to this point, so the 3-hourly forecast fetch (~100 calls each,
# ~800/day) always has room and the dashboard's weather freshness never goes stale because of backfill.
ARCHIVE_DAY_CAP = 7500
FORECAST_EVERY_H = 3


class BudgetExceeded(Exception):
    pass


def weight(n_locations: int, days: int, n_vars: int) -> float:
    return n_locations * max(1.0, days / 14) * max(1.0, n_vars / 10)


def _ledger() -> list[tuple[float, float]]:
    if LEDGER.exists():
        return [tuple(x) for x in json.loads(LEDGER.read_text(encoding="utf-8"))]
    return []


def _spend(w: float, day_cap: float = LIMITS["day"]) -> None:
    """Wait if the minute window is full; raise BudgetExceeded if hour/day windows are full."""
    now = time.time()
    led = [x for x in _ledger() if now - x[0] < 86400]
    used = lambda s: sum(c for t, c in led if now - t < s)
    if used(86400) + w > day_cap or used(3600) + w > LIMITS["hour"]:
        raise BudgetExceeded(f"day={used(86400):.0f} hour={used(3600):.0f} need={w:.0f}")
    while used(60) + w > LIMITS["minute"]:
        time.sleep(5)
        now = time.time()
    led.append((time.time(), w))
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led), encoding="utf-8")


def _chunks(districts: pd.DataFrame):
    d = districts.sort_values("district_id").reset_index(drop=True)
    for i in range(0, len(d), CHUNK):
        yield i // CHUNK, d.iloc[i:i + CHUNK]


def _params(chunk: pd.DataFrame, **extra) -> dict:
    return {
        "latitude": ",".join(f"{v:.4f}" for v in chunk["lat"]),
        "longitude": ",".join(f"{v:.4f}" for v in chunk["lon"]),
        "hourly": ",".join(config.WEATHER_HOURLY_VARS),
        "timezone": "GMT",
        **extra,
    }


def _save(dest: Path, url: str, params: dict, chunk: pd.DataFrame, manifest: Manifest, days: int,
          day_cap: float = LIMITS["day"]) -> None:
    _spend(weight(len(chunk), days, len(config.WEATHER_HOURLY_VARS)), day_cap)
    try:
        r = http.get(url, params=params)
    except Exception as e:
        if "429" in str(e):
            raise BudgetExceeded(f"server said 429: {e}") from e
        raise
    payload = r.json()
    if isinstance(payload, dict):      # a single location comes back as a dict, many as a list
        payload = [payload]
    doc = {"district_ids": chunk["district_id"].tolist(), "request": params, "response": payload}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc), encoding="utf-8")
    manifest.record(dest, "open_meteo", r.url, config.OPEN_METEO_LICENCE)
    manifest.save()


def fetch_archive(districts: pd.DataFrame, manifest: Manifest) -> dict:
    today = datetime.now(timezone.utc).date()
    done, deferred = 0, 0
    for year in range(int(config.WEATHER_START[:4]), today.year + 1):
        start = date(year, 1, 1)
        end = date(year, 12, 31) if year < today.year else today - timedelta(days=6)  # archive lag
        days = (end - start).days + 1
        for ci, chunk in _chunks(districts):
            dest = RAW_DIR / "archive" / f"{year}_chunk{ci:02d}.json"
            if manifest.has_valid(dest):
                if year < today.year:
                    continue
                fetched = manifest.rows[manifest.rel(dest)].fetched_at_utc[:10]
                if fetched == today.isoformat():
                    continue        # current year: refresh at most once a day
            try:
                _save(dest, config.OPEN_METEO_ARCHIVE,
                      _params(chunk, start_date=start.isoformat(), end_date=end.isoformat()), chunk, manifest, days,
                      day_cap=ARCHIVE_DAY_CAP)
                done += 1
                print(f"  open_meteo archive {dest.name}")
            except BudgetExceeded as e:
                deferred = sum(1 for y in range(year, today.year + 1) for _ in _chunks(districts))
                print(f"  open_meteo archive: budget reached ({e}); remaining files deferred to next run")
                return {"fetched": done, "deferred_approx": deferred}
    return {"fetched": done, "deferred_approx": 0}


def fetch_recent(districts: pd.DataFrame, manifest: Manifest) -> dict:
    now = datetime.now(timezone.utc)
    last = sorted((RAW_DIR / "forecast").glob("*/")) if (RAW_DIR / "forecast").exists() else []
    if last:
        prev = datetime.strptime(last[-1].name, "%Y%m%dT%H").replace(tzinfo=timezone.utc)
        if now - prev < timedelta(hours=FORECAST_EVERY_H):
            return {"fetched": 0, "skipped": f"last forecast {prev:%H}:00 UTC < {FORECAST_EVERY_H} h ago"}
    stamp = now.strftime("%Y%m%dT%H")
    n = 0
    for ci, chunk in _chunks(districts):
        dest = RAW_DIR / "forecast" / stamp / f"chunk{ci:02d}.json"
        if manifest.has_valid(dest):
            continue
        try:
            _save(dest, config.OPEN_METEO_FORECAST, _params(chunk, past_days=7, forecast_days=7), chunk, manifest, 14)
            n += 1
        except BudgetExceeded as e:
            print(f"  open_meteo forecast: budget reached ({e})")
            break
    return {"fetched": n}


def load_raw(kind: str) -> pd.DataFrame:
    """Flatten raw JSON files into long hourly rows. `kind` = archive | forecast."""
    frames = []
    for f in sorted((RAW_DIR / kind).rglob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        fetched = f.parent.name if kind == "forecast" else None
        for did, resp in zip(doc["district_ids"], doc["response"]):
            h = resp.get("hourly") or {}
            if not h:
                continue
            df = pd.DataFrame(h)
            df["district_id"] = did
            df["units_temp"] = resp.get("hourly_units", {}).get("temperature_2m")
            df["units_precip"] = resp.get("hourly_units", {}).get("precipitation")
            df["source_file"] = f.relative_to(config.ROOT).as_posix()
            df["fetch_stamp"] = fetched
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).rename(columns={"time": "ts_utc"})
    out["kind"] = kind
    return out
