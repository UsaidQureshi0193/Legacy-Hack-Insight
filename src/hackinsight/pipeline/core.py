"""Pipeline steps shared by `make data` (full rebuild) and the hourly Prefect flow (incremental).

Each step is a plain function so it can be called from the CLI, from tests, and wrapped as a
Prefect task without changing the logic.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime, timezone

import duckdb
import pandas as pd

from .. import config
from ..ingest import context_sources, nih_idsr, open_meteo, profile_sources, rag_docs
from . import alerts
from ..manifest import Manifest
from ..quality.agent import DataQualityAgent, Decision
from ..warehouse import build, extract, geo


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def clean_working_folders() -> None:
    """`make data` starts from an empty working folder (raw files are re-verified, not trusted)."""
    # data/cache (parsed PDFs keyed by sha256 + parser version) is kept unless HI_NO_CACHE=1:
    # a cached parse is only reused for the exact same file bytes and the same parser code.
    import os
    targets = [config.CLEANED, config.PROCESSED] + ([config.DATA / "cache"] if os.environ.get("HI_NO_CACHE") else [])
    for p in targets:
        shutil.rmtree(p, ignore_errors=True)
    if config.WAREHOUSE.exists():
        config.WAREHOUSE.unlink()
    config.ensure_dirs()


def verify_raw(manifest: Manifest) -> list[str]:
    """Re-hash every raw file. Missing files are re-fetched by the fetch steps; a hash mismatch
    on a file that exists means it was edited -> delete it so it is fetched again from source."""
    problems = manifest.verify()
    for p in problems:
        if p.startswith("sha256 mismatch"):
            path = config.ROOT / p.split(": ", 1)[1]
            print(f"  ! {p} -> re-fetching from source")
            path.unlink(missing_ok=True)
    return problems


def step_fetch(manifest: Manifest, incremental: bool, rejected: set[str] = frozenset()) -> dict:
    t = time.perf_counter()
    n_ctx = 0 if {"hdx", "opendengue"} <= rejected else context_sources.fetch(manifest)
    n_docs = rag_docs.fetch(manifest)
    new_pdfs = [] if "nih_idsr" in rejected else nih_idsr.fetch(manifest, refresh_listing=True)
    return {"context_files": n_ctx, "rag_docs": n_docs, "new_bulletins": len(new_pdfs),
            "seconds": round(time.perf_counter() - t, 1)}


def matched_districts(ext: pd.DataFrame) -> pd.DataFrame:
    adm2 = context_sources.load_adm2()
    places = ext.loc[(ext["place_type"] == "district") & (ext["table"] == "district"),
                     ["province", "place"]].drop_duplicates()
    m = geo.match(places, adm2).dropna(subset=["district_id"]).drop_duplicates("district_id")
    d = adm2[adm2["adm2_pcode"].isin(m["district_id"])].rename(
        columns={"adm2_pcode": "district_id", "center_lat": "lat", "center_lon": "lon"})
    return d[["district_id", "lat", "lon"]]


def step_weather(manifest: Manifest, districts: pd.DataFrame, incremental: bool) -> dict:
    t = time.perf_counter()
    # Past years are skipped when their hash is valid; the current year refreshes at most once a day.
    n_arch = open_meteo.fetch_archive(districts, manifest)
    n_rec = open_meteo.fetch_recent(districts, manifest)
    return {"archive": n_arch, "forecast": n_rec, "seconds": round(time.perf_counter() - t, 1)}


def load_weather_raw(keep_forecast_runs: int = 3) -> pd.DataFrame:
    arch = open_meteo.load_raw("archive")
    fc = open_meteo.load_raw("forecast")
    if len(fc):
        latest = sorted(fc["fetch_stamp"].unique())[-keep_forecast_runs:]
        fc = fc[fc["fetch_stamp"].isin(latest)]
    return pd.concat([arch, fc], ignore_index=True) if len(fc) else arch


def write_status(run: dict, quality: dict, gold_info: dict | None, ok: bool) -> dict:
    config.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    old = {}
    if build.STATUS_FILE.exists():
        try:
            old = json.loads(build.STATUS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = {}
    status = {
        "last_run": run,
        "quality": {k: quality[k] for k in ("run_id", "status", "counts")},
        "freshness": (gold_info or {}).get("freshness", old.get("freshness", {})),
        "last_good": ({**run, **{k: v for k, v in (gold_info or {}).items() if k != "freshness"}}
                      if ok else old.get("last_good")),
    }
    build.STATUS_FILE.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    (config.ARTIFACTS / "quality_report.json").write_text(json.dumps(quality, indent=2, default=str),
                                                          encoding="utf-8")
    return status


def run(full_rebuild: bool, fetch: bool = True, weather: bool = True) -> dict:
    """The whole data pipeline. full_rebuild=True is `make data`; False is the hourly run."""
    run_id = new_run_id()
    started = datetime.now(timezone.utc)
    steps: dict = {}
    if full_rebuild:
        clean_working_folders()
    config.ensure_dirs()
    manifest = Manifest()
    steps["verify_raw"] = {"problems": verify_raw(manifest)}
    profile = profile_sources.run(force=full_rebuild) if fetch else []
    rejected = profile_sources.rejected(profile)
    steps["gate0"] = {p["source"]: p["decision"] for p in profile}
    if fetch:
        steps["fetch"] = step_fetch(manifest, incremental=not full_rebuild, rejected=rejected)

    t = time.perf_counter()
    ext, probs = extract.run(manifest)
    steps["extract"] = {"rows": len(ext), "seconds": round(time.perf_counter() - t, 1)}

    if weather and fetch and "open_meteo" not in rejected:
        steps["weather"] = step_weather(manifest, matched_districts(ext), incremental=not full_rebuild)
    weather_raw = load_weather_raw()

    # Every raw file used must be in the manifest with a valid hash before we build anything.
    final_problems = manifest.verify()
    con = duckdb.connect(str(config.WAREHOUSE))
    agent = DataQualityAgent(con, run_id)
    for p in profile:
        agent.log(Decision("gate0", f"source_{p['source']}", "GATE0-SOURCE",
                           {"ACCEPT": "PASS", "ACCEPT_API": "FLAG", "NOT_USED": "PASS", "FLAG": "FLAG",
                            "REJECT": "FLAG"}[p["decision"]], p["source"], 0,
                           f"{p['name']}: {p['decision']} - {p['why']}." + (" Not fetched." if p["decision"] in ("REJECT", "NOT_USED") else ""),
                           status="info" if p["decision"] == "ACCEPT" else "applied"))
    if final_problems:
        agent.log(Decision("gate0", "manifest", "MANIFEST", "FAIL", "raw", len(final_problems),
                           "Raw files do not match manifest.csv after fetching; refusing to build.",
                           final_problems[:5]))
        agent.persist()
        quality = agent.summary()
        st = write_status({"run_id": run_id, "status": "failed", "started_utc": started.isoformat()}, quality, None, False)
        con.close()
        raise RuntimeError(f"manifest verification failed: {final_problems[:3]}")

    t = time.perf_counter()
    steps["cleaned"] = build.build_cleaned(
        con, agent, ext, probs, weather_raw, context_sources.load_adm2(), context_sources.load_mpi(),
        context_sources.load_population(), context_sources.load_opendengue_pakistan())
    steps["cleaned"]["seconds"] = round(time.perf_counter() - t, 1)
    t = time.perf_counter()
    ok, gold_info = build.build_gold(con, agent)
    gold_info["seconds"] = round(time.perf_counter() - t, 1)
    steps["gold"] = {k: v for k, v in gold_info.items() if k != "freshness"}
    if ok:
        from ..warehouse import validate_local
        v = validate_local.run(con)
        steps["local_validation"] = {k: v.get(k) for k in ("months", "exact_match_months",
                                                          "mismatch_months_with_missing_bulletin_week",
                                                          "mismatch_months_unexplained", "correlation")}
    agent.persist()
    quality = agent.summary()
    finished = datetime.now(timezone.utc)
    run_info = {"run_id": run_id, "status": "success" if ok else "gate_failed", "started_utc": started.isoformat(),
                "finished_utc": finished.isoformat(), "duration_s": round((finished - started).total_seconds(), 1),
                "mode": "full_rebuild" if full_rebuild else "incremental", "steps": steps}
    status = write_status(run_info, quality, gold_info, ok)
    alerts.write(alerts.from_quality(quality, status.get("freshness", {})), run_id)
    con.close()
    return status


def print_summary(status: dict) -> None:
    r = status["last_run"]
    q = status["quality"]
    print("\n================ make data summary ================")
    print(f"run {r['run_id']}  status={r['status']}  {r.get('duration_s')} s")
    for k, v in r.get("steps", {}).items():
        print(f"  {k:10s} {v}")
    print(f"quality gate: {q['status']}  decisions: {q['counts']}")
    for src, f in status.get("freshness", {}).items():
        print(f"  freshness {src:10s} newest={f['newest']}  age={f['age_h']:.0f}h  {'LATE' if f['late'] else 'ok'}")
    print("====================================================")
