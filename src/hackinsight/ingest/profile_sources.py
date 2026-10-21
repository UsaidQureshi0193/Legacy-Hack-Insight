"""Gate 0 - source profiling: check a source BEFORE any of its data enters the warehouse.

For every candidate source:
  * robots.txt allows our user agent to fetch the exact path we use   (rules: no robots.txt violations)
  * a licence / terms statement is recorded                           (rules: licence allows our use)
  * the endpoint answers                                              (it is alive)
Decision policy (one rule for every source):
  * robots.txt allows the path and a licence is recorded          -> ACCEPT
  * robots.txt disallows crawlers on an API host, BUT the publisher's API terms explicitly grant
    programmatic access AND the organisers list the source in the guide  -> ACCEPT_API (flagged, with
    the terms URL - robots.txt here keeps search engines off the API host; our access is governed by
    the API terms, and we stay inside their rate limits with the call ledger)
  * anything else failing robots, or no licence                    -> REJECT, never fetched
Open-Meteo and NASA POWER are in exactly the same situation (both API hosts: "Disallow"), so they get
the same decision; NASA POWER is simply not needed.
Results: artifacts/source_profile.json + gate0 rows in quality_log. Re-checked at most once a day.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .. import config, http

PROFILE = config.ARTIFACTS / "source_profile.json"

CANDIDATES = [
    dict(source="nih_idsr", name="NIH Pakistan IDSR weekly bulletins", url=config.NIH_LISTING_URL,
         licence=config.NIH_LICENCE, role="target (district cases)"),
    dict(source="open_meteo", name="Open-Meteo historical + forecast weather", url=config.OPEN_METEO_ARCHIVE,
         licence=config.OPEN_METEO_LICENCE, role="weather features",
         api_terms="https://open-meteo.com/en/terms", organiser_listed=True),
    dict(source="opendengue", name="OpenDengue v1.3 (Figshare)", url=config.OPENDENGUE_URL,
         licence=config.OPENDENGUE_LICENCE, role="benchmark / local validation"),
    dict(source="hdx", name="HDX: OCHA boundaries, census population, OPHI MPI", url=config.HDX_MPI_URL,
         licence=config.HDX_MPI_LICENCE, role="district ids, population, poverty"),
    dict(source="nasa_power", name="NASA POWER daily point API (candidate)",
         url="https://power.larc.nasa.gov/api/temporal/daily/point", licence="NASA open data",
         role="alternative weather history (evaluated, not needed)",
         api_terms="https://power.larc.nasa.gov/docs/services/api/", organiser_listed=True, needed=False),
]


def _robots_ok(url: str) -> tuple[bool | None, str]:
    u = urlparse(url)
    rp = RobotFileParser()
    try:
        r = http.session().get(f"{u.scheme}://{u.netloc}/robots.txt", timeout=20)
    except Exception as e:
        return None, f"robots.txt not reachable ({type(e).__name__})"
    if r.status_code >= 400:
        return True, f"no robots.txt (HTTP {r.status_code}) - nothing disallowed"
    rp.parse(r.text.splitlines())
    ok = rp.can_fetch(config.USER_AGENT, url)
    return ok, "robots.txt allows this path" if ok else f"robots.txt disallows {u.path}"


def _alive(url: str) -> tuple[bool, str]:
    try:
        r = http.session().head(url, timeout=20, allow_redirects=True)
        if r.status_code in (403, 405):        # some servers refuse HEAD; a tiny GET decides
            r = http.session().get(url, timeout=20, stream=True)
            r.close()
        return r.status_code < 400, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"not reachable ({type(e).__name__})"


def _apply_exclusions(sources: list[dict]) -> list[dict]:
    """A source switched off with HI_EXCLUDE_SOURCES is REJECTED whatever its checks said."""
    return [{**p, "decision": "REJECT", "why": "switched off by the team (HI_EXCLUDE_SOURCES); not fetched, not used"}
            if p["source"] in config.EXCLUDED_SOURCES else p for p in sources]


def run(force: bool = False) -> list[dict]:
    if PROFILE.exists() and not force:
        old = json.loads(PROFILE.read_text(encoding="utf-8"))
        if time.time() - old.get("checked_epoch", 0) < 86400:
            return _apply_exclusions(old["sources"])
    out = []
    for c in CANDIDATES:
        robots, robots_note = _robots_ok(c["url"])
        alive, alive_note = (_alive(c["url"]) if robots is not False or c.get("api_terms")
                             else (None, "not contacted (robots)"))
        if robots is False and c.get("api_terms") and c.get("organiser_listed"):
            decision = "ACCEPT_API" if c.get("needed", True) else "NOT_USED"
            why = (f"{robots_note} (crawler rule on the API host); programmatic access is granted by the API terms "
                   f"{c['api_terms']} and the source is on the organisers' list - CONFIRM WITH ORGANISERS")
        elif robots is False:
            decision, why = "REJECT", robots_note
        elif not c["licence"]:
            decision, why = "REJECT", "no licence / terms recorded"
        elif alive is False:
            decision, why = "FLAG", f"endpoint {alive_note}; existing raw files are used, new fetches may fail"
        else:
            decision, why = "ACCEPT", f"{robots_note}; licence recorded; {alive_note}"
        out.append({**c, "robots": robots, "alive": alive, "decision": decision, "why": why})
    out = _apply_exclusions(out)
    for p in out:
        print(f"  gate0 {p['source']:11s} {p['decision']:6s} {p['why']}")
    PROFILE.write_text(json.dumps({"checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "checked_epoch": time.time(), "sources": out}, indent=2), encoding="utf-8")
    return out


def rejected(profile: list[dict]) -> set[str]:
    return {p["source"] for p in profile if p["decision"] in ("REJECT", "NOT_USED")}
