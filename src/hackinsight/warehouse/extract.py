"""Raw -> extract: parse every IDSR PDF listed in the manifest into one long string table.

Parsing a bulletin takes a few seconds, and the hourly pipeline would otherwise re-parse 200 PDFs
every hour. Results are cached per SHA-256 of the PDF (data/cache/idsr/<sha>.parquet): an
unchanged file is never parsed twice, a corrected re-upload (new hash) is parsed again.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from .. import config
from ..ingest.parse_idsr import PARSER_VERSION, parse_pdf
from ..manifest import Manifest

CACHE = config.DATA / "cache" / "idsr"
NAME_RE = re.compile(r"(\d{4})_W(\d{2})\.pdf$")


def _parse_one(args: tuple[str, str, int, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    path, sha, year, week = args
    try:
        res = parse_pdf(Path(path), year, week)
    except Exception as e:   # one unreadable PDF must not stop the build - it becomes a FLAG
        from ..ingest.parse_idsr import ParseResult
        res = ParseResult(problems=[dict(file=Path(path).name, page=None, issue="parse_error",
                                         detail=f"{type(e).__name__}: {e}"[:300])])
    rows = pd.DataFrame(res.rows)
    probs = pd.DataFrame(res.problems)
    sw = res.stated_week
    meta = dict(listing_year=year, listing_week=week, stated_week=sw[0] if sw else None,
                stated_year=sw[1] if sw else None, sha256=sha)
    for k, v in meta.items():
        rows[k] = v
        probs[k] = v
    return rows, probs


def run(manifest: Manifest, workers: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    CACHE.mkdir(parents=True, exist_ok=True)
    todo, frames, prob_frames = [], [], []
    for e in manifest.entries("nih_idsr"):
        m = NAME_RE.search(e.path)
        if not m:
            continue   # listing snapshots
        cached = CACHE / f"{e.sha256}_v{PARSER_VERSION}.parquet"
        cached_p = CACHE / f"{e.sha256}_v{PARSER_VERSION}.problems.parquet"
        if cached.exists():
            frames.append(pd.read_parquet(cached))
            if cached_p.exists():
                prob_frames.append(pd.read_parquet(cached_p))
        else:
            todo.append((str(config.ROOT / e.path), e.sha256, int(m.group(1)), int(m.group(2))))
    if todo:
        # 4 workers: pdfplumber can use several hundred MB on image-heavy bulletins
        workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
        print(f"  parsing {len(todo)} new bulletins with {workers} workers ...")
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for (rows, probs), args in zip(ex.map(_parse_one, todo), todo):
                sha = args[1]
                rows.to_parquet(CACHE / f"{sha}_v{PARSER_VERSION}.parquet", index=False)
                probs.to_parquet(CACHE / f"{sha}_v{PARSER_VERSION}.problems.parquet", index=False)
                frames.append(rows)
                prob_frames.append(probs)
    rows = pd.concat([f for f in frames if len(f)], ignore_index=True) if frames else pd.DataFrame()
    probs = pd.concat([f for f in prob_frames if len(f)], ignore_index=True) if prob_frames else pd.DataFrame()
    return rows, probs
