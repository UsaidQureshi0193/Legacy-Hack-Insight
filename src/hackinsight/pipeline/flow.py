"""Hourly Prefect flow (cron "5 * * * *" = five minutes past every hour).

    1 fetch new data  2 land in raw (append, never overwrite)  3 Gate 1 -> cleaned
    4 Gate 2 -> gold (freshness + drift)  5 retrain only if new weeks or drift (keep if better)
    6 re-index new documents/bulletins  7 regenerate briefs + golden eval  8 publish dashboard data

A failed gate stops the run: gold, the model and the dashboard keep the last good version.
Run it with:   python -m hackinsight.pipeline.flow      (keeps serving until stopped)
"""
from __future__ import annotations

import json
import os

from prefect import flow, get_run_logger, task

from .. import config


@task(name="data-pipeline", retries=2, retry_delay_seconds=120)
def data_pipeline() -> dict:
    from . import core
    status = core.run(full_rebuild=False)
    if status["last_run"]["status"] != "success":
        raise RuntimeError(f"quality gate failed: {status['quality']['counts']}")
    return status


@task(name="retrain-if-needed")
def retrain_if_needed(status: dict) -> dict:
    log = get_run_logger()
    steps = status["last_run"]["steps"]
    new_weeks = steps.get("fetch", {}).get("new_bulletins", 0)
    psi = steps.get("gold", {}).get("psi_primary")
    model_missing = not (config.ARTIFACTS / f"model_{config.PRIMARY_DISEASE}" / "metrics.json").exists()
    # a full `make data` recreates the warehouse without forecast tables - retrain then too
    import duckdb
    with duckdb.connect(str(config.WAREHOUSE), read_only=True) as c:
        have = {t for (t,) in c.execute("SELECT table_name FROM information_schema.tables "
                                        "WHERE table_schema = 'gold'").fetchall()}
    model_missing = model_missing or any(f"forecast_{d}" not in have for d in config.TARGET_DISEASES)
    drift = psi is not None and psi == psi and psi > 0.25
    # weather history arrives over several runs (API budget); retrain when coverage grew by > 5%
    import json as _json
    mpath = config.ARTIFACTS / f"model_{config.PRIMARY_DISEASE}" / "metrics.json"
    trained_w = _json.loads(mpath.read_text(encoding="utf-8")).get("weather_rows_at_train", 0) if mpath.exists() else 0
    now_w = steps.get("gold", {}).get("weather_rows", 0)
    more_weather = trained_w and now_w > trained_w * 1.05
    if not (new_weeks or drift or model_missing or more_weather):
        log.info("no new bulletin week and no drift -> model unchanged")
        return {"retrained": False}
    from ..model import train
    res = train.run()
    log.info(f"retrained (new_weeks={new_weeks}, drift={drift}, tables_missing={model_missing}, "
             f"weather_rows {trained_w}->{now_w}): {res['headline']}")
    return {"retrained": True, "headline": res["headline"]}


@task(name="reindex-if-needed")
def reindex_if_needed(status: dict) -> int:
    steps = status["last_run"]["steps"].get("fetch", {})
    if steps.get("new_bulletins", 0) or steps.get("rag_docs", 0) or not (config.ARTIFACTS / "rag" / "index.faiss").exists():
        from ..rag import index
        return index.build()
    return 0


@task(name="briefs-and-eval")
def briefs_and_eval(model: dict, indexed: int) -> dict:
    """Briefs only change when the forecast or the library changes (weekly), so they are not
    regenerated every hour - that keeps LLM calls (and cost) ~150x lower. The golden eval uses no
    LLM and runs every hour as a regression check."""
    from ..rag import briefs, evaluate
    n = None
    if model.get("retrained") or indexed or not (config.ARTIFACTS / "briefs.json").exists():
        n = len(briefs.generate())
    e = evaluate.run()
    return {"briefs_regenerated": n, "eval": e["summary"]}


@task(name="publish-dashboard", retries=2, retry_delay_seconds=60)
def publish() -> str:
    from . import publish as pub
    return pub.run()


from .alerts import flow_failed  # noqa: E402


@flow(name="hackinsight-hourly", log_prints=True, on_failure=[flow_failed])
def hourly() -> dict:
    status = data_pipeline()
    model = retrain_if_needed(status)
    n = reindex_if_needed(status)
    be = briefs_and_eval(model, n)
    where = publish()
    out = {"run": status["last_run"]["run_id"], "quality": status["quality"]["status"], "model": model,
           "indexed": n, "briefs_eval": be, "published": where}
    print(json.dumps(out, default=str))
    return out


if __name__ == "__main__":
    hourly.serve(name="hourly", cron="5 * * * *", tags=["hackinsight"],
                 description="Fetch -> quality gates -> gold -> retrain if needed -> reindex -> briefs -> publish")
