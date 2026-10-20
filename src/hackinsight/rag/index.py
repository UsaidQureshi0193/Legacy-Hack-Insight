"""Build the Insight Agent's retrieval index.

Chunks come from two places, each chunk keeps (doc_id, title, page, url) for citation:
  1. the trusted library (configs/rag_sources.csv): NIH advisories & guidelines, WHO fact sheets
  2. the narrative pages ("Overview", "Public Health Actions") of the latest IDSR bulletins

Retrieval is hybrid: BM25 (exact words like district or disease names) + multilingual sentence
embeddings (meaning, and Urdu questions against English text), fused with reciprocal rank fusion.
Embeddings are cached by chunk hash, so re-indexing after one new bulletin embeds only new chunks.
"""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np
import pandas as pd

from .. import config
from ..ingest import rag_docs
from ..manifest import Manifest

RAG_DIR = config.ARTIFACTS / "rag"
EMB_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_CHARS, OVERLAP = 900, 150
_model = None


def embedder():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMB_MODEL, device="cpu")
    return _model


def tokenize(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[؀-ۿ]+", s.lower())


def split_text(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[•\-•])", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 <= CHUNK_CHARS:
            cur = f"{cur}\n{p}" if cur else p
            continue
        if cur:
            chunks.append(cur)
        while len(p) > CHUNK_CHARS:            # very long paragraph -> windows with overlap
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - OVERLAP:]
        cur = p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c) > 80]


def library_chunks() -> list[dict]:
    out = []
    for row in rag_docs.sources().itertuples(index=False):
        if not (rag_docs.RAW_DIR / f"{row.doc_id}.{row.kind}").exists():
            continue
        for page, text in rag_docs.pages(row):
            for k, c in enumerate(split_text(text)):
                out.append(dict(chunk_id=f"{row.doc_id}#p{page}-{k}", doc_id=row.doc_id, title=row.title,
                                publisher=row.publisher, page=page, url=row.url, text=c, kind="guidance"))
    return out


def model_card_chunks() -> list[dict]:
    out = []
    for f in sorted(config.ARTIFACTS.glob("model_card_*.md")):
        d = f.stem.replace("model_card_", "")
        for k, c in enumerate(split_text(f.read_text(encoding="utf-8"))):
            out.append(dict(chunk_id=f"MODEL-CARD-{d}#p1-{k}", doc_id=f"MODEL-CARD-{d}",
                            title=f"Model card: next-week suspected {d} cases (this project)",
                            publisher="HackInsight team (generated from metrics.json)", page=1,
                            url=f"docs/model_card_{d}.md", text=c, kind="model_card"))
    return out


def bulletin_chunks(n_latest: int = 6) -> list[dict]:
    import pdfplumber
    man = Manifest()
    pdfs = sorted([e for e in man.entries("nih_idsr") if e.path.endswith(".pdf")], key=lambda e: e.path)[-n_latest:]
    out = []
    for e in pdfs:
        m = re.search(r"(\d{4})_W(\d{2})", e.path)
        doc_id = f"NIH-IDSR-{m.group(1)}-W{m.group(2)}"
        with pdfplumber.open(config.ROOT / e.path) as pdf:
            for i, p in enumerate(pdf.pages[:6]):          # overview + public health actions live up front
                text = p.extract_text() or ""
                if not re.search(r"Overview|Public Health Actions|recommend|advis", text, re.I):
                    continue
                for k, c in enumerate(split_text(text)):
                    out.append(dict(chunk_id=f"{doc_id}#p{i+1}-{k}", doc_id=doc_id,
                                    title=f"IDSR Public Health Bulletin, week {int(m.group(2))} {m.group(1)}",
                                    publisher="National Institute of Health, Islamabad", page=i + 1, url=e.query,
                                    text=c, kind="bulletin"))
    return out


def build() -> int:
    import faiss
    RAG_DIR.mkdir(parents=True, exist_ok=True)
    chunks = pd.DataFrame(library_chunks() + bulletin_chunks() + model_card_chunks())
    chunks["hash"] = chunks["text"].map(lambda t: hashlib.sha1(t.encode()).hexdigest())
    cache_p = RAG_DIR / "emb_cache.npz"
    cache = dict(np.load(cache_p, allow_pickle=False)) if cache_p.exists() else {}
    todo = chunks.loc[~chunks["hash"].isin(cache.keys()), ["hash", "text"]].drop_duplicates("hash")
    if len(todo):
        vecs = embedder().encode(todo["text"].tolist(), batch_size=32, normalize_embeddings=True,
                                 show_progress_bar=False)
        cache.update({h: v.astype("float32") for h, v in zip(todo["hash"], vecs)})
        np.savez(cache_p, **cache)
    emb = np.stack([cache[h] for h in chunks["hash"]]).astype("float32")
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    faiss.write_index(index, str(RAG_DIR / "index.faiss"))
    chunks.to_parquet(RAG_DIR / "chunks.parquet", index=False)
    (RAG_DIR / "meta.json").write_text(json.dumps({"model": EMB_MODEL, "n_chunks": len(chunks),
                                                   "embedded_now": int(len(todo))}), encoding="utf-8")
    return len(chunks)


class Retriever:
    def __init__(self):
        import faiss
        from rank_bm25 import BM25Okapi
        self.chunks = pd.read_parquet(RAG_DIR / "chunks.parquet")
        self.index = faiss.read_index(str(RAG_DIR / "index.faiss"))
        self.bm25 = BM25Okapi([tokenize(t) for t in self.chunks["text"]])

    def search(self, query: str, k: int = 4) -> pd.DataFrame:
        q = embedder().encode([query], normalize_embeddings=True).astype("float32")
        sims, ids = self.index.search(q, min(30, len(self.chunks)))
        dense = pd.Series(sims[0], index=ids[0])
        bm = pd.Series(self.bm25.get_scores(tokenize(query)))
        bm_top = bm.nlargest(30)
        # reciprocal rank fusion
        rrf = {}
        for rank, i in enumerate(dense.index):
            rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
        for rank, i in enumerate(bm_top.index):
            if bm_top[i] > 0:
                rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
        top = sorted(rrf, key=rrf.get, reverse=True)[:k]
        res = self.chunks.iloc[top].copy()
        res["dense_score"] = [float(dense.get(i, 0.0)) for i in top]
        res["bm25_score"] = [float(bm[i]) for i in top]
        res["rrf"] = [rrf[i] for i in top]
        return res
