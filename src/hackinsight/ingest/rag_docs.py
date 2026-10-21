"""Trusted document library for the Insight Agent (NIH advisories/guidelines, WHO fact sheets).

Same rules as data: fetched by code, saved unedited under data/raw/rag_docs/, recorded in
manifest.csv. The list of documents is configs/rag_sources.csv (reviewed by the team).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote

import pandas as pd
import pdfplumber

from .. import config, http
from ..manifest import Manifest

RAW_DIR = config.RAW / "rag_docs"


def sources() -> pd.DataFrame:
    return pd.read_csv(config.CONFIGS / "rag_sources.csv")


def _dest(row) -> "Path":
    return RAW_DIR / f"{row.doc_id}.{row.kind}"


def fetch(manifest: Manifest) -> int:
    n = 0
    for row in sources().itertuples(index=False):
        dest = _dest(row)
        if manifest.has_valid(dest):
            continue
        url = quote(row.url, safe=":/()-_.~%")
        try:
            http.download(url, dest)
        except Exception as e:   # one dead link must not stop the data pipeline
            print(f"  ! rag_docs {row.doc_id} not fetched ({e}); answers will not cite it")
            continue
        manifest.record(dest, "rag_docs", url, row.licence)
        n += 1
        print(f"  rag_docs {dest.name}")
    manifest.save()
    return n


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript"}

    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "br", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


OCR_CACHE = config.DATA / "cache" / "ocr"
_ocr = None


def _ocr_page(page, key: str) -> str:
    """Text of a SCANNED page (an image with no text layer), read with RapidOCR - a pip package, no
    system install, so `make setup` is enough on any machine. Cached by file SHA-256 + page, so each
    file version is read once. Returns "" when OCR is not installed (the page is then skipped)."""
    global _ocr
    cached = OCR_CACHE / f"{key}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return ""
    if _ocr is None:
        _ocr = RapidOCR()
    img = page.to_image(resolution=200).original.convert("RGB")
    result, _ = _ocr(np.array(img))
    text = "\n".join(r[1] for r in (result or []))
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def pages(row) -> list[tuple[int, str]]:
    """Return [(page_number, text)] for a library document. HTML counts as one 'page'.
    PDF pages without a text layer (scanned advisories) are read with OCR."""
    dest = _dest(row)
    if row.kind == "pdf":
        from ..manifest import sha256_of
        sha = sha256_of(dest)[:16]
        out = []
        with pdfplumber.open(dest) as pdf:
            for i, p in enumerate(pdf.pages):
                text = p.extract_text() or ""
                if len(text.strip()) < 20 and p.images:
                    text = _ocr_page(p, f"{sha}_p{i + 1}")
                out.append((i + 1, text))
        return out
    p = _Text()
    p.feed(dest.read_text(encoding="utf-8", errors="replace"))
    text = re.sub(r"\n\s*\n+", "\n\n", "".join(p.parts))
    # keep the article body: from the first "Key facts"/"Overview" heading onward when present
    m = re.search(r"(Key facts|Overview)", text)
    return [(1, text[m.start():] if m else text)]
