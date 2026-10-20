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


def pages(row) -> list[tuple[int, str]]:
    """Return [(page_number, text)] for a library document. HTML counts as one 'page'."""
    dest = _dest(row)
    if row.kind == "pdf":
        with pdfplumber.open(dest) as pdf:
            return [(i + 1, p.extract_text() or "") for i, p in enumerate(pdf.pages)]
    p = _Text()
    p.feed(dest.read_text(encoding="utf-8", errors="replace"))
    text = re.sub(r"\n\s*\n+", "\n\n", "".join(p.parts))
    # keep the article body: from the first "Key facts"/"Overview" heading onward when present
    m = re.search(r"(Key facts|Overview)", text)
    return [(1, text[m.start():] if m else text)]
