"""Publish what the dashboard needs, and nothing else.

Builds publish/ with the same layout as the repo (so the Space just sets HI_ROOT to it):
  publish/data/warehouse.duckdb      gold schema + quality_log only (small, no raw/cleaned data)
  publish/artifacts/...              status, quality report, briefs, eval, model metrics, rag index
If HF_TOKEN and HI_HF_DATASET (e.g. "your-team/hackinsight-live") are set, the folder is uploaded
to that Hugging Face dataset repo; the Space downloads it on start and every few minutes.
"""
from __future__ import annotations

import os
import shutil

import duckdb

from .. import config

OUT = config.ROOT / "publish"


def snapshot() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "data").mkdir(parents=True)
    dst = OUT / "data" / "warehouse.duckdb"
    src = duckdb.connect(str(config.WAREHOUSE), read_only=True)
    tables = src.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='gold'").fetchall()
    out = duckdb.connect(str(dst))
    out.execute("CREATE SCHEMA gold")
    for (t,) in tables:
        df = src.execute(f"SELECT * FROM gold.{t}").df()
        out.register("_t", df)
        out.execute(f"CREATE TABLE gold.{t} AS SELECT * FROM _t")
        out.unregister("_t")
    ql = src.execute("SELECT * FROM quality_log ORDER BY ts_utc DESC LIMIT 2000").df()
    out.register("_q", ql)
    out.execute("CREATE TABLE quality_log AS SELECT * FROM _q")
    out.close()
    src.close()
    art = OUT / "artifacts"
    art.mkdir()
    for f in ("status.json", "quality_report.json", "briefs.json", "eval_report.json"):
        if (config.ARTIFACTS / f).exists():
            shutil.copy2(config.ARTIFACTS / f, art / f)
    for d in config.ARTIFACTS.glob("model_*"):
        (art / d.name).mkdir()
        for f in ("metrics.json", "forecast.parquet", "test_predictions.parquet"):
            if (d / f).exists():
                shutil.copy2(d / f, art / d.name / f)
    if (config.ARTIFACTS / "rag").exists():
        shutil.copytree(config.ARTIFACTS / "rag", art / "rag", ignore=shutil.ignore_patterns("emb_cache.npz"))
    shutil.copy2(config.CONFIGS / "rag_sources.csv", OUT / "rag_sources.csv")
    shutil.copy2(config.MANIFEST, OUT / "manifest.csv")   # dashboard links every number to its NIH PDF
    if (config.CONFIGS / "brief_reviews.json").exists():
        (OUT / "configs").mkdir(exist_ok=True)
        shutil.copy2(config.CONFIGS / "brief_reviews.json", OUT / "configs" / "brief_reviews.json")


def run() -> str:
    snapshot()
    repo = os.environ.get("HI_HF_DATASET")
    if not (repo and os.environ.get("HF_TOKEN")):
        return f"local only: {OUT}"
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(repo, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(folder_path=str(OUT), repo_id=repo, repo_type="dataset",
                      commit_message="hourly pipeline publish")
    return f"hf://datasets/{repo}"
